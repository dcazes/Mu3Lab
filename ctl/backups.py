"""Mu3Lab :: ctl/backups.py

WHAT: The Kopia repository: what it holds, how well each app is protected, and
      the few one-off Kopia commands Mu3Lab runs itself.
WHY: Git is not a database or personal-data backup system. Backups remain
     encrypted, local, service-scoped, and independently restorable.
HOW: Kopia's own server (``ctl.backup_service``) makes the backups: it keeps
     the schedule, applies the retention policy and maintains the repository.
     This module runs the one-off commands around it in a pinned, network-less
     container that sees only the repository (``/srv/mu3lab/backups/kopia``),
     its own configuration (``/srv/mu3lab/state/kopia``), the password (a
     private env-file) and the folders the command needs:

     - restoring one backed-up folder beside the live one (``restore_folder``);
     - the backup an update takes just before it changes anything (``snapshot``);
     - listing an app's backups and reading back a sample of stored data.

Facts checked against the pinned image (0.23.1), not assumed:

- Kopia applies a source's retention policy every time it takes a snapshot.
- Restoring ``<id>/<folder>`` into an existing directory gives that directory
  the folder's own owner and mode and keeps every file's owner, so a database
  folder stays owned by its container user.
- With the password in the environment, Kopia writes no password file.
- Snapshots the server makes carry no tags. Their app comes from the source
  path (``/data/<app>``), their folders from the snapshot itself, and the
  release they belong to from what the backup service noted just before the
  snapshot (``note_release``).
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl import actions, resource_locks
from ctl.runtime import RuntimePaths
from ctl.secret_file import locked, write_atomic
from ctl.store import records
from ctl.store.secrets import SecretStore

KOPIA_IMAGE = "docker.io/kopia/kopia:0.23.1@sha256:89fd95ee2942880ca00eae964266958a394421ddbdf69bca62e38afc55f5900e"
# Kopia records user@host; a container's hostname is random, which would split
# every app's history into one source per run.
HOST = "mu3lab"
REPOSITORY_MARKER = "kopia.repository.f"
# Kopia's own retention, applied after every snapshot of a source.
RETENTION = {"latest": 10, "daily": 7, "weekly": 4, "monthly": 12}
POLICY = [
    "--compression=zstd",
    # Cache folders are kept: a restore must be exact.
    "--ignore-cache-dirs=false",
    f"--keep-latest={RETENTION['latest']}",
    "--keep-hourly=0",
    f"--keep-daily={RETENTION['daily']}",
    f"--keep-weekly={RETENTION['weekly']}",
    f"--keep-monthly={RETENTION['monthly']}",
    "--keep-annual=0",
]
REASONS = frozenset({"pre-update"})
# The Kopia source for Mu3Lab's own state (control database, key, deployments).
PLATFORM_SOURCE = "mu3lab"
_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{16,64}$")
_VERSION = re.compile(r"^[A-Za-z0-9._+-]{1,80}$")
_RELEASE_ID = re.compile(r"^[0-9a-f]{16}$")
# Every one-off backup container carries this label so one that outlived its
# Docker CLI (a timeout, a worker restart) can be found before anything else runs.
CONTAINER_LABEL = "mu3lab.role=backup"
SURVIVOR_WAIT_SECONDS = 1800
STOP_SECONDS = 30
# Backups and checks older than this are reported as stale.
STALE_DAYS = 8
# The scheduled check reads back this share of stored data at most this often.
DATA_CHECK_SUBSET = "5%"
DATA_CHECK_DAYS = 7
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
REPOSITORY_LOCK = "backup-repository"
# How many "release before a backup" notes are kept per app.
RELEASE_NOTES = 200

Log = Callable[[str], None]


class BackupError(RuntimeError):
    """A backup step failed; the message is safe to show the person."""


def readiness(paths: RuntimePaths = RuntimePaths()) -> dict:
    """Report verified backup facts, never infer protection from a directory.

    Kopia writes its repository marker only once the repository is created.
    The repository counts as verified only while its last structural check
    passed within ``STALE_DAYS``; each app's own protection is ``protection``.
    """
    repository = _repository(paths)
    engine_available = shutil.which("docker") is not None
    present = repository_present(paths)
    check = records.get("backup", "repository", paths)
    checked_at = str(check.get("structure_checked_at", "")) if check.get("ok") else ""
    snapshot_ok = any(
        str(records.get("backup-apps", key, paths).get("completed_at") or "")
        for key in records.names("backup-apps", paths)
    )
    integrity_ok = bool(checked_at) and not _older_than(checked_at, STALE_DAYS)
    if not engine_available or not present:
        state = "not_configured"
    elif snapshot_ok and integrity_ok:
        state = "verified"
    else:
        state = "local_only"
    return {
        "engine": "kopia",
        "available": engine_available,
        "repository_path": str(repository),
        "repository_present": present,
        "retention": {key: RETENTION[key] for key in ("daily", "weekly", "monthly")},
        "snapshot_present": snapshot_ok,
        "integrity_verified": integrity_ok,
        "last_verified_at": checked_at,
        "off_device": False,
        "state": state,
    }


def _older_than(stamp: str, days: float) -> bool:
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    return (datetime.now(UTC) - when).total_seconds() > days * 86400


def record_backup(service_id: str, completed_at: str, paths: RuntimePaths | None = None) -> None:
    """The newest backup of a source finished at ``completed_at`` (from Kopia's own list)."""
    paths = paths or RuntimePaths()
    record = records.get("backup-apps", service_id, paths)
    if str(record.get("completed_at") or "") != completed_at:
        records.put("backup-apps", service_id, {**record, "completed_at": completed_at}, paths)


def protection(service_id: str, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """How well one app's data is protected, from its own evidence only.

    ``missing``: no backup. ``stale``: the newest is older than ``STALE_DAYS``.
    ``unchecked``: no repository check has passed since it was saved.
    ``checked``: a structural check passed since; ``verification`` says
    whether stored data was also read back. Copies are local only for now.
    """
    paths = paths or RuntimePaths()
    record = records.get("backup-apps", service_id, paths)
    repository = records.get("backup", "repository", paths)
    saved = str(record.get("completed_at", ""))
    result: dict[str, Any] = {
        "state": "missing",
        "completed_at": saved,
        "verification": "none",
        "restore_tested_at": str(record.get("restore_tested_at", "")),
        "off_device": False,
    }
    if not saved:
        result["detail"] = "No backup of this app has been saved yet."
        return result
    structure = str(repository.get("structure_checked_at", "")) if repository.get("ok") else ""
    data = str(repository.get("data_checked_at", "")) if repository.get("ok") else ""
    if data and data >= saved:
        result["verification"] = "data"
    elif structure and structure >= saved:
        result["verification"] = "structure"
    if _older_than(saved, STALE_DAYS):
        result["state"], result["detail"] = "stale", "The newest backup is more than a week old."
    elif result["verification"] == "none":
        result["state"], result["detail"] = "unchecked", "The newest backup has not been checked yet."
    else:
        result["state"] = "checked"
        result["detail"] = (
            "The newest backup was checked, including a sample of its stored data."
            if result["verification"] == "data"
            else "The newest backup's repository structure was checked."
        )
    result["detail"] += " Backups are kept on this server only."
    return result


def data_check_due(paths: RuntimePaths | None = None) -> bool:
    """A repository exists and its stored data was not read back within ``DATA_CHECK_DAYS``."""
    paths = paths or RuntimePaths()
    if not repository_present(paths):
        return False
    stamp = str(records.get("backup", "repository", paths).get("data_checked_at", ""))
    return not stamp or _older_than(stamp, DATA_CHECK_DAYS)


def start_verification(stopping: threading.Event, log: Log, *, interval: float = 6 * 3600) -> threading.Thread:
    """Read back a share of stored backup data about weekly; Kopia's server has no schedule for this."""

    def loop() -> None:
        while not stopping.wait(timeout=interval):
            try:
                if data_check_due():
                    log("Checking a sample of the stored backup data.")
                    check(log, read_data=DATA_CHECK_SUBSET)
            except (BackupError, OSError) as exc:
                log(f"The scheduled backup check did not pass: {exc}")

    thread = threading.Thread(target=loop, name="mu3lab-backup-verification", daemon=True)
    thread.start()
    return thread


def record_restore_tested(service_id: str, paths: RuntimePaths | None = None) -> None:
    """A restore of this app finished and the app came up healthy."""
    paths = paths or RuntimePaths()
    record = records.get("backup-apps", service_id, paths)
    records.put(
        "backup-apps",
        service_id,
        {**record, "restore_tested_at": datetime.now(UTC).isoformat(timespec="seconds")},
        paths,
    )


def check(log: Log, *, read_data: str = "", paths: RuntimePaths | None = None) -> None:
    """Check every snapshot's structure; with ``read_data`` (e.g. "5%") also read back that share of files."""
    paths = paths or RuntimePaths()
    if not repository_present(paths):
        raise BackupError("There is no backup repository to check yet.")
    _connect(log, paths)
    percent = read_data.rstrip("%") or "0"
    args = ["snapshot", "verify", f"--verify-files-percent={percent}"]
    rc, output = _kopia(args, log, paths=paths, timeout=6 * 3600 if read_data else 3600)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    current = records.get("backup", "repository", paths)
    if rc:
        records.put("backup", "repository", {**current, "ok": False, "failed_at": now}, paths)
        raise BackupError(f"The backup repository check failed: {output[-500:]}")
    update = {"ok": True, "structure_checked_at": now}
    if read_data:
        update["data_checked_at"] = now
    records.put("backup", "repository", {**current, **update}, paths)


def repository_password(paths: RuntimePaths) -> str:
    """Create the repository password once and keep it in the encrypted store.

    A repository that already exists is never given a new password: that
    would only lock Mu3Lab out of the backups it has.
    """
    with locked(paths.state / "backup-password.lock"):
        store = SecretStore(paths)
        password = store.get("platform", "backup-password")
        if password is None and repository_present(paths):
            raise BackupError(
                "The backup repository exists but Mu3Lab's password for it is missing, so it cannot be opened."
            )
        if password is None:
            password = secrets.token_urlsafe(48)
            store.put("platform", "backup-password", password)
        return password


def _repository(paths: RuntimePaths) -> Path:
    return paths.backups / "kopia"


def _config(paths: RuntimePaths) -> Path:
    return paths.state / "kopia"


def repository_present(paths: RuntimePaths) -> bool:
    return (_repository(paths) / REPOSITORY_MARKER).is_file()


def _mutating(args: list[str]) -> bool:
    return args[:2] != ["snapshot", "list"] and args[:1] != ["ls"]


def _kopia(
    args: list[str],
    log: Log,
    *,
    mounts: Sequence[tuple[Path, str, bool]] = (),
    paths: RuntimePaths,
    timeout: int = 3600,
    password: str = "",
) -> tuple[int, str]:
    """Run one Kopia command against the local repository in its pinned container.

    ``password`` is given only while rebuilding a new computer, before Mu3Lab's
    own encrypted store (which normally holds it) has been restored.

    The container is named and labelled. Before a command that changes data
    runs, any one-off backup container still running from an earlier attempt
    is waited for, so a retried restore never races the previous one. If this
    command times out, its container is stopped before returning: killing the
    Docker CLI alone would leave Kopia writing.
    """
    if _mutating(args):
        # One data-changing one-off Kopia command at a time, across every process.
        try:
            with resource_locks.hold(REPOSITORY_LOCK, paths=paths):
                survivor = _wait_for_survivors(log)
                if survivor:
                    return 1, survivor
                return _run_kopia(args, log, mounts=mounts, paths=paths, timeout=timeout, password=password)
        except resource_locks.Busy:
            return 1, "Another backup task is still using the backup repository; try again later."
    return _run_kopia(args, log, mounts=mounts, paths=paths, timeout=timeout, password=password)


def _run_kopia(
    args: list[str],
    log: Log,
    *,
    mounts: Sequence[tuple[Path, str, bool]],
    paths: RuntimePaths,
    timeout: int,
    password: str = "",
) -> tuple[int, str]:
    name = f"mu3lab-kopia-{secrets.token_hex(6)}"
    for folder in (_repository(paths), _config(paths)):
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mu3lab-kopia-") as temporary:
        # The password reaches Kopia through a private env-file, never a command line.
        env_file = Path(temporary) / "env"
        write_atomic(env_file, f"KOPIA_PASSWORD={password or repository_password(paths)}\n".encode())
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            CONTAINER_LABEL,
            "--network",
            "none",
            "--hostname",
            HOST,
            "--env-file",
            str(env_file),
            "-e",
            "KOPIA_CONFIG_PATH=/cfg/repository.config",
            "-e",
            "KOPIA_CACHE_DIRECTORY=/cfg/cache",
            "-e",
            "KOPIA_LOG_DIR=/cfg/logs",
            "-e",
            "KOPIA_CHECK_FOR_UPDATES=false",
            "-v",
            f"{_repository(paths)}:/repo",
            "-v",
            f"{_config(paths)}:/cfg",
        ]
        for source, target, read_only in mounts:
            command.extend(["-v", f"{source}:{target}{':ro' if read_only else ''}"])
        command.extend([KOPIA_IMAGE, *args])
        rc, output = actions.docker_cmd(command, log, timeout=timeout)
        if rc == 124:
            _stop_container(name, log)
        return rc, output


def _running_backups() -> list[str]:
    rc, output = actions.docker_cmd(
        ["docker", "ps", "--quiet", "--filter", f"label={CONTAINER_LABEL}"], lambda _line: None, timeout=30
    )
    return output.split() if rc == 0 else []


def _wait_for_survivors(log: Log) -> str:
    """Wait for earlier one-off backup containers to exit; return an error if they don't."""
    deadline = time.monotonic() + SURVIVOR_WAIT_SECONDS
    running = _running_backups()
    if running:
        log("A backup command from an earlier attempt is still running; waiting for it to finish.")
    while running:
        if time.monotonic() >= deadline:
            return (
                "A backup command from an earlier attempt is still running, so nothing was changed. "
                "Try again once it has finished."
            )
        time.sleep(5)
        running = _running_backups()
    return ""


def _stop_container(name: str, log: Log) -> None:
    """Stop a backup container that outlived its command; Kopia exits cleanly on SIGTERM."""
    actions.docker_cmd(["docker", "stop", "--time", str(STOP_SECONDS), name], log, timeout=STOP_SECONDS + 30)


def _json(output: str, kind: type) -> Any:
    """The first JSON value of ``kind`` in the output; Docker merges Kopia's progress (stderr) into it."""
    decoder = json.JSONDecoder()
    opener = "[" if kind is list else "{"
    index = output.find(opener)
    while index != -1:
        try:
            value, _end = decoder.raw_decode(output, index)
        except ValueError:
            value = None
        if isinstance(value, kind):
            return value
        index = output.find(opener, index + 1)
    return None


IDENTITY = ["--override-username", HOST, "--override-hostname", HOST, "--no-persist-credentials"]


def _connect(log: Log, paths: RuntimePaths) -> None:
    """Connect this machine's Kopia configuration to the existing repository (a new host, lost state)."""
    if (_config(paths) / "repository.config").is_file():
        return
    rc, output = _kopia(
        ["repository", "connect", "filesystem", "--path=/repo", *IDENTITY], log, paths=paths, timeout=300
    )
    if rc:
        raise BackupError(f"The backup repository could not be opened: {output[-500:]}")


def ensure_repository(log: Log, paths: RuntimePaths | None = None) -> None:
    """Create the repository once, with Mu3Lab's policy; reconnect to an existing one."""
    paths = paths or RuntimePaths()
    if repository_present(paths):
        _connect(log, paths)
        return
    rc, output = _kopia(
        ["repository", "create", "filesystem", "--path=/repo", *IDENTITY], log, paths=paths, timeout=300
    )
    if rc or not repository_present(paths):
        raise BackupError(f"The backup repository could not be created: {output[-500:]}")
    rc, output = _kopia(["policy", "set", "--global", *POLICY], log, paths=paths, timeout=300)
    if rc:
        raise BackupError(f"The backup repository's policy could not be set: {output[-500:]}")


def sources(data_directories: list[Path], paths: RuntimePaths | None = None) -> list[Path]:
    """The app's data folders that exist on this server."""
    paths = paths or RuntimePaths()
    found = []
    for directory in data_directories:
        check_root(directory, paths)
        if directory.exists():
            found.append(directory)
    return found


def snapshot(
    service_id: str,
    data_directories: list[Path],
    reason: str,
    log: Log,
    *,
    version: str = "",
    release_id: str = "",
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    """Snapshot the app's (stopped) data folders as the source ``/data/<app>``, as the server does.

    Only an update makes its own backup this way: it already holds the app
    stopped, and the backup must exist before anything changes.
    """
    paths = paths or RuntimePaths()
    if reason not in REASONS or not _NAME.fullmatch(service_id) or service_id == PLATFORM_SOURCE:
        raise BackupError("unknown backup reason")
    folders = sources(data_directories, paths)
    if not folders:
        raise BackupError("This app has no data on this server to back up yet.")
    ensure_repository(log, paths)
    tags = [f"app:{service_id}", f"reason:{reason}", f"folders:{'+'.join(folder.name for folder in folders)}"]
    if version and _VERSION.fullmatch(version):
        tags.append(f"version:{version}")
    if release_id and _RELEASE_ID.fullmatch(release_id):
        # The exact deployment the data belongs to; the version is only its label.
        tags.append(f"release:{release_id}")
    rc, output = _kopia(
        [
            "snapshot",
            "create",
            f"/data/{service_id}",
            "--json",
            "--fail-fast",
            f"--description=Mu3Lab {reason} backup of {service_id}",
            *(f"--tags={tag}" for tag in tags),
        ],
        log,
        mounts=[(folder, f"/data/{service_id}/{folder.name}", True) for folder in folders],
        paths=paths,
        timeout=6 * 3600,
    )
    manifest = _json(output, dict)
    if rc or not manifest or not manifest.get("id"):
        raise BackupError(f"The backup did not complete: {output[-500:]}")
    summary = (manifest.get("rootEntry") or {}).get("summ") or {}
    if int(summary.get("numFailed") or 0):
        raise BackupError(f"The backup skipped {summary['numFailed']} unreadable files, so it is not complete.")
    record_backup(service_id, datetime.now(UTC).isoformat(timespec="seconds"), paths)
    return {"snapshot_id": str(manifest["id"]), "files": int(summary.get("files") or 0)}


def note_release(
    service_id: str, release_id: str, version: str, folders: list[str], paths: RuntimePaths | None = None
) -> None:
    """Just before the server backs up an app: the release its data belongs to, for a later restore."""
    paths = paths or RuntimePaths()
    entries = list(records.get("backup-releases", service_id, paths).get("entries") or [])
    entries.append(
        {
            "at": datetime.now(UTC).isoformat(timespec="microseconds"),
            "release_id": release_id,
            "version": version,
            "folders": sorted(folders),
        }
    )
    records.put("backup-releases", service_id, {"entries": entries[-RELEASE_NOTES:]}, paths)


def _noted(service_id: str, end: str, paths: RuntimePaths) -> dict[str, Any]:
    """The note made by the newest backup that started before ``end`` (the snapshot's end time)."""
    try:
        finished = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return {}
    best: dict[str, Any] = {}
    for entry in records.get("backup-releases", service_id, paths).get("entries") or []:
        try:
            noted = datetime.fromisoformat(str(entry.get("at")))
        except (TypeError, ValueError):
            continue
        if noted <= finished and (not best or str(entry["at"]) > str(best["at"])):
            best = entry
    return best


def snapshots(service_id: str, log: Log = lambda _line: None, paths: RuntimePaths | None = None) -> list[dict]:
    """The app's snapshots, newest first. A missing repository has none."""
    paths = paths or RuntimePaths()
    if not repository_present(paths) or not _NAME.fullmatch(service_id):
        return []
    _connect(log, paths)
    rc, output = _kopia(["snapshot", "list", f"/data/{service_id}", "--json"], log, paths=paths, timeout=120)
    listing = _json(output, list)
    if rc or listing is None:
        raise BackupError(f"Backups could not be listed: {output[-300:]}")
    result = []
    for item in listing:
        tags = item.get("tags") or {}
        if (item.get("source") or {}).get("path") != f"/data/{service_id}" or tags.get("tag:app", service_id) != (
            service_id
        ):
            continue
        snapshot_id = str(item.get("id", ""))
        noted = {} if tags else _noted(service_id, str(item.get("endTime") or ""), paths)
        folders = [name for name in str(tags.get("tag:folders", "")).split("+") if name] or list(
            noted.get("folders") or []
        )
        result.append(
            {
                "id": snapshot_id,
                "short_id": snapshot_id[:8],
                "time": str(item.get("startTime", "")),
                "reason": str(tags.get("tag:reason") or "backup"),
                "version": str(tags.get("tag:version") or noted.get("version") or ""),
                "release_id": str(tags.get("tag:release") or noted.get("release_id") or ""),
                "paths": [f"/data/{name}" for name in folders],
                "complete": not int((item.get("stats") or {}).get("errorCount") or 0),
            }
        )
    return sorted(result, key=lambda snap: snap["time"], reverse=True)


def folders_in(snapshot_id: str, log: Log, paths: RuntimePaths | None = None, *, password: str = "") -> list[str]:
    """The top-level folders a snapshot holds, read from the snapshot itself."""
    paths = paths or RuntimePaths()
    if not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise BackupError("Unknown backup.")
    rc, output = _kopia(["ls", "--long", snapshot_id], log, paths=paths, timeout=300, password=password)
    if rc:
        raise BackupError(f"That backup could not be read: {output[-300:]}")
    names = []
    for line in output.splitlines():
        name = line.rsplit(" ", 1)[-1]
        if line.startswith("d") and name.endswith("/") and _NAME.fullmatch(name[:-1]):
            names.append(name[:-1])
    return sorted(names)


def restore_folder(snapshot_id: str, folder: str, target: Path, log: Log, paths: RuntimePaths | None = None) -> None:
    """Restore one backed-up folder into ``target``, a new empty folder beside the live one.

    Kopia restores ``<snapshot>/<folder>`` into the target itself, which then
    takes the folder's own owner and mode (a database folder stays owned by
    its container user). Every block read is authenticated, so a damaged
    backup fails here rather than reaching the live data.
    """
    paths = paths or RuntimePaths()
    if not _SNAPSHOT_ID.fullmatch(snapshot_id) or not _NAME.fullmatch(folder):
        raise BackupError("Unknown backup.")
    _connect(log, paths)
    rc, output = _kopia(
        ["snapshot", "restore", "--no-ignore-permission-errors", f"{snapshot_id}/{folder}", "/restore"],
        log,
        mounts=[(target, "/restore", False)],
        paths=paths,
        timeout=6 * 3600,
    )
    if rc or not target.is_dir():
        raise BackupError(f"{folder} could not be restored and checked: {output[-500:]}")


def check_root(directory: Path, paths: RuntimePaths) -> None:
    """Only a plain, top-level folder of the data root may be backed up or restored."""
    if directory.parent != paths.data or not _NAME.fullmatch(directory.name):
        raise BackupError("Refusing to use a folder outside the Mu3Lab data root.")
    if directory.is_symlink():
        raise BackupError(f"Refusing to use {directory.name}: it is a link, not a folder.")


def wipe(places: list[Path], log: Log, *, paths=None) -> None:
    """Delete restore leftovers, whose files belong to container users, in a networkless container."""
    paths = paths or RuntimePaths()
    present = [place for place in places if place.exists()]
    if not present:
        return
    for place in present:
        if place.parent != paths.data or not place.name.startswith(".") or place.is_symlink():
            raise BackupError("Refusing to delete a folder that is not a restore leftover.")
    rc, output = actions.docker_cmd(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--entrypoint",
            "rm",
            "-v",
            f"{paths.data}:/mu3lab-data",
            KOPIA_IMAGE,
            "-rf",
            "--",
            *(f"/mu3lab-data/{place.name}" for place in present),
        ],
        log,
        timeout=3600,
    )
    remaining = [place.name for place in present if place.exists()]
    if rc or remaining:
        raise BackupError(output[-300:] or "Some restore leftovers could not be deleted: " + ", ".join(remaining))
