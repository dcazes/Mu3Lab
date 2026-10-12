"""Mu3Lab :: ctl/backups.py

WHAT: Local Kopia backups of one app's data folders, their staged restore,
      and read-only readiness inspection.
WHY: Git is not a database or personal-data backup system. Backups remain
     encrypted, local, service-scoped, and independently restorable.
HOW: App data belongs to container users (postgres, www-data), which the
     unprivileged worker cannot read. Kopia therefore runs in a pinned,
     network-less container that sees only the repository
     (``/srv/mu3lab/backups/kopia``), its own configuration
     (``/srv/mu3lab/state/kopia``), the password (a private env-file) and the
     app's own folders under the data root. Callers stop the app first so
     databases are captured consistently.

Facts checked against the pinned image (0.23.1), not assumed:

- Kopia applies a source's retention policy every time it takes a snapshot.
  Mu3Lab sets that policy to keep everything and applies its own retention
  (``expired``/``prune``), so it can skip pruning while an unfinished
  operation still needs a backup, exactly as before.
- Pinning or editing a snapshot rewrites its manifest and changes its id, so
  Mu3Lab never pins: ids stay stable in the journal and the dashboard.
- Restoring ``<id>/<folder>`` into an existing directory gives that directory
  the folder's own owner and mode and keeps every file's owner, so a database
  folder stays owned by its container user.
- With the password in the environment, Kopia writes no password file.

An older restic repository (``/srv/mu3lab/backups/config``) is never touched;
``readiness`` reports it so the owner knows it is there (decision: no
migration; see docs/backup-recovery.md for restoring from it by hand).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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
KEEP_LATEST = 3
REASONS = frozenset({"manual", "scheduled", "pre-update", "pre-restore", "platform"})
# The Kopia source for Mu3Lab's own state (control database, key, deployments).
PLATFORM_SOURCE = "mu3lab"
_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{16,64}$")
_VERSION = re.compile(r"^[A-Za-z0-9._+-]{1,80}$")
_RELEASE_ID = re.compile(r"^[0-9a-f]{16}$")
# Every backup container carries this label so one that outlived its Docker
# CLI (a timeout, a worker restart) can be found before anything else runs.
CONTAINER_LABEL = "mu3lab.role=backup"
SURVIVOR_WAIT_SECONDS = 1800
STOP_SECONDS = 30
# Backups and checks older than this are reported as stale.
STALE_DAYS = 8
# The scheduled check reads back this share of stored data at most this often.
DATA_CHECK_SUBSET = "5%"
DATA_CHECK_DAYS = 7
STAGED = "mu3lab-staged"
PREVIOUS = "mu3lab-previous"
DISCARD = "mu3lab-discard"
_TAG = re.compile(r"^[0-9a-z-]{4,40}$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
REPOSITORY_LOCK = "backup-repository"

Log = Callable[[str], None]


class BackupError(RuntimeError):
    """A backup step failed; the message is safe to show the person."""


@dataclass(frozen=True)
class Retention:
    """The conservative default local snapshot policy chosen for Mu3Lab."""

    daily: int = 7
    weekly: int = 4
    monthly: int = 12


def readiness(paths: RuntimePaths = RuntimePaths(), retention: Retention = Retention()) -> dict:
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
    snapshot_ok = bool(records.get("backup", "verification", paths).get("snapshot_id"))
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
        # Backups made with restic before Mu3Lab moved to Kopia; kept untouched.
        "legacy_repository": str(paths.backups) if (paths.backups / "config").is_file() else "",
        "retention": {"daily": retention.daily, "weekly": retention.weekly, "monthly": retention.monthly},
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
        "snapshot_id": str(record.get("snapshot_id", "")),
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
    """Read back a share of stored backup data about weekly, apart from any app's downtime."""

    def loop() -> None:
        while not stopping.wait(timeout=interval):
            try:
                if data_check_due():
                    log("Checking a sample of the stored backup data.")
                    check(log, read_data=DATA_CHECK_SUBSET)
                    # Reclaims the space of backups retention removed.
                    maintain(log)
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
    return args[:2] != ["snapshot", "list"]


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
    runs, any backup container still running from an earlier attempt is
    waited for, so a retried restore never races the previous one. If this
    command times out, its container is stopped before returning: killing the
    Docker CLI alone would leave Kopia writing.
    """
    if _mutating(args):
        # One data-changing Kopia command at a time, across every process.
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
    """Wait for earlier backup containers to exit; return an error if they don't."""
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
    else:
        rc, output = _kopia(
            ["repository", "create", "filesystem", "--path=/repo", *IDENTITY], log, paths=paths, timeout=300
        )
        if rc or not repository_present(paths):
            raise BackupError(f"The backup repository could not be created: {output[-500:]}")
    # Reassert this before every snapshot, including after interrupted creation.
    # Mu3Lab applies retention itself (``prune``), so Kopia never expires a snapshot an
    # unfinished operation still needs. Cache folders are kept: a restore must be exact.
    rc, output = _kopia(
        [
            "policy",
            "set",
            "--global",
            "--compression=zstd",
            "--ignore-cache-dirs=false",
            "--keep-latest=1000000",
            "--keep-hourly=0",
            "--keep-daily=0",
            "--keep-weekly=0",
            "--keep-monthly=0",
            "--keep-annual=0",
        ],
        log,
        paths=paths,
        timeout=300,
    )
    if rc:
        raise BackupError(f"The backup repository's policy could not be set: {output[-500:]}")


def sources(data_directories: list[Path], paths: RuntimePaths | None = None) -> list[Path]:
    """The app's data folders that exist on this server."""
    paths = paths or RuntimePaths()
    found = []
    for directory in data_directories:
        _check_root(directory, paths)
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
    retention: Retention = Retention(),
    prune_old: bool = True,
    verify: bool = True,
) -> dict[str, Any]:
    """Snapshot the app's (stopped) data folders as one source, verify, apply retention.

    Each folder is mounted read-only at ``/data/<app>/<folder>`` and the
    source ``/data/<app>`` is snapshotted, so an app has one history.
    ``prune_old=False`` leaves retention to the caller: an update or restore
    prunes only once nothing it may still need to put back can be removed.
    """
    paths = paths or RuntimePaths()
    if service_id == PLATFORM_SOURCE:
        raise BackupError("That name is reserved for Mu3Lab's own backup.")
    folders = sources(data_directories, paths)
    if not folders:
        raise BackupError("This app has no data on this server to back up yet.")
    return snapshot_source(
        service_id,
        [(folder.name, folder) for folder in folders],
        reason,
        log,
        version=version,
        release_id=release_id,
        paths=paths,
        retention=retention,
        prune_old=prune_old,
        verify=verify,
    )


def snapshot_source(
    service_id: str,
    folders: list[tuple[str, Path]],
    reason: str,
    log: Log,
    *,
    version: str = "",
    release_id: str = "",
    paths: RuntimePaths | None = None,
    retention: Retention = Retention(),
    prune_old: bool = True,
    verify: bool = True,
) -> dict[str, Any]:
    """Snapshot named folders as the source ``/data/<service_id>``; ``snapshot`` checks app folders first."""
    paths = paths or RuntimePaths()
    if reason not in REASONS or not _NAME.fullmatch(service_id):
        raise BackupError("unknown backup reason")
    if not folders or any(not _NAME.fullmatch(name) or not path.is_dir() for name, path in folders):
        raise BackupError("Refusing to back up an unexpected folder.")
    ensure_repository(log, paths)
    tags = [f"app:{service_id}", f"reason:{reason}", f"folders:{'+'.join(name for name, _path in folders)}"]
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
        mounts=[(path, f"/data/{service_id}/{name}", True) for name, path in folders],
        paths=paths,
        timeout=6 * 3600,
    )
    manifest = _json(output, dict)
    if rc or not manifest or not manifest.get("id"):
        raise BackupError(f"The backup did not complete: {output[-500:]}")
    summary = (manifest.get("rootEntry") or {}).get("summ") or {}
    if int(summary.get("numFailed") or 0):
        raise BackupError(f"The backup skipped {summary['numFailed']} unreadable files, so it is not complete.")
    snapshot_id = str(manifest["id"])
    completed = datetime.now(UTC).isoformat(timespec="seconds")
    previous = records.get("backup-apps", service_id, paths)
    records.put(
        "backup-apps",
        service_id,
        {
            **previous,
            "snapshot_id": snapshot_id,
            "release_id": release_id,
            "reason": reason,
            "completed_at": completed,
        },
        paths,
    )
    if verify:
        try:
            check(log, paths=paths)
        except BackupError as exc:
            raise BackupError(f"The backup was saved but its integrity check failed: {str(exc)[-500:]}") from exc
    records.put(
        "backup",
        "verification",
        {
            "snapshot_id": snapshot_id,
            "service_id": service_id,
            "integrity_checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        paths,
    )
    if prune_old:
        prune(service_id, log, paths=paths, retention=retention)
    return {
        "snapshot_id": snapshot_id,
        "files": int(summary.get("files") or 0),
        "bytes": int(summary.get("size") or 0),
    }


def expired(listing: list[dict], retention: Retention, keep_latest: int = KEEP_LATEST) -> list[str]:
    """Snapshot ids the policy no longer keeps: the newest ``keep_latest``, then one per day, week and month."""
    ordered = sorted(listing, key=lambda snap: snap["time"], reverse=True)
    kept = {snap["id"] for snap in ordered[:keep_latest]}
    buckets = (
        (retention.daily, lambda when: when.strftime("%Y-%m-%d")),
        (retention.weekly, lambda when: "{:04d}-{:02d}".format(*when.isocalendar()[:2])),
        (retention.monthly, lambda when: when.strftime("%Y-%m")),
    )
    for count, key in buckets:
        seen: list[str] = []
        for snap in ordered:
            try:
                bucket = key(datetime.fromisoformat(snap["time"].replace("Z", "+00:00")))
            except ValueError:
                kept.add(snap["id"])  # an unreadable time is never a reason to delete
                continue
            if bucket in seen:
                continue
            if len(seen) >= count:
                break
            seen.append(bucket)
            kept.add(snap["id"])
    return [snap["id"] for snap in ordered if snap["id"] not in kept]


def prune(service_id: str, log: Log, *, paths: RuntimePaths | None = None, retention: Retention = Retention()) -> bool:
    """Apply the retention policy to one app's snapshots.

    Retention is housekeeping: a failure leaves an extra snapshot, never a
    missing one. Kopia reclaims the space at its next full maintenance.
    """
    paths = paths or RuntimePaths()
    try:
        listing = snapshots(service_id, log, paths)
    except BackupError as exc:
        log(f"Old backups were not pruned this time: {exc}")
        return False
    from ctl.operations import OperationStore

    journal = OperationStore.runtime(paths)
    protected = journal.protected(service_id) if journal else set()
    removable = [snapshot for snapshot in expired(listing, retention) if snapshot not in protected]
    if not removable:
        return True
    rc, output = _kopia(["snapshot", "delete", *removable, "--delete"], log, paths=paths)
    if rc:
        log(f"Old backups were not pruned this time: {output[-300:]}")
    return rc == 0


def maintain(log: Log, paths: RuntimePaths | None = None) -> None:
    """Kopia's full maintenance: reclaims the space of deleted snapshots."""
    paths = paths or RuntimePaths()
    if not repository_present(paths):
        return
    _connect(log, paths)
    rc, output = _kopia(["maintenance", "run", "--full"], log, paths=paths, timeout=6 * 3600)
    if rc:
        raise BackupError(f"Backup repository maintenance failed: {output[-300:]}")


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
        if tags.get("tag:app") != service_id:
            continue
        snapshot_id = str(item.get("id", ""))
        folders = [name for name in str(tags.get("tag:folders", "")).split("+") if name]
        result.append(
            {
                "id": snapshot_id,
                "short_id": snapshot_id[:8],
                "time": str(item.get("startTime", "")),
                "reason": str(tags.get("tag:reason") or "manual"),
                "version": str(tags.get("tag:version") or ""),
                "release_id": str(tags.get("tag:release") or ""),
                "paths": [f"/data/{name}" for name in folders],
            }
        )
    return sorted(result, key=lambda snap: snap["time"], reverse=True)


def _check_root(directory: Path, paths: RuntimePaths) -> None:
    """Only a plain, top-level folder of the data root may be backed up or restored."""
    if directory.parent != paths.data or not _NAME.fullmatch(directory.name):
        raise BackupError("Refusing to use a folder outside the Mu3Lab data root.")
    if directory.is_symlink():
        raise BackupError(f"Refusing to use {directory.name}: it is a link, not a folder.")


def _place(name: str, kind: str, tag: str, paths: RuntimePaths) -> Path:
    return paths.data / f".{name}.{kind}-{tag}"


def _has_content(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except PermissionError:
        return True  # unreadable to this user: owned by a container user, so it holds data


def plan_restore(
    service_id: str,
    snapshot_id: str,
    data_directories: list[Path],
    log: Log,
    *,
    tag: str,
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    """Check that a backup can replace the app's whole data set, and plan the staged restore.

    Every folder the deployment uses must be in the backup, unless it has no
    data on this server either; a backup made for another folder layout is
    refused, so a restore can never leave old and new data side by side.
    """
    paths = paths or RuntimePaths()
    if not _SNAPSHOT_ID.fullmatch(snapshot_id) or not _TAG.fullmatch(tag):
        raise BackupError("Unknown backup.")
    for directory in data_directories:
        _check_root(directory, paths)
    chosen = next((snap for snap in snapshots(service_id, log, paths) if snap["id"] == snapshot_id), None)
    if not chosen:
        raise BackupError("That backup does not belong to this app.")
    allowed = {f"/data/{directory.name}": directory.name for directory in data_directories}
    unknown = [path for path in chosen["paths"] if path not in allowed]
    if unknown or not chosen["paths"]:
        raise BackupError("That backup was made for a different folder layout and cannot be restored here.")
    saved = {allowed[path] for path in chosen["paths"]}
    missing = sorted(name for name in allowed.values() if name not in saved)
    incomplete = [name for name in missing if _has_content(paths.data / name)]
    if incomplete:
        raise BackupError(
            f"That backup does not include {', '.join(incomplete)}, which this app uses now; "
            "restoring it would mix old and new data, so it was not restored."
        )
    return {
        "snapshot_id": snapshot_id,
        "tag": tag,
        "roots": {name: "planned" for name in sorted(saved)},
        "absent": sorted(name for name in saved if not (paths.data / name).exists()),
        "not_in_backup": missing,
    }


def stage(
    service_id: str, plan: dict[str, Any], log: Log, save: Callable[[dict[str, Any]], None], *, paths=None
) -> dict[str, Any]:
    """Restore each folder beside the live one; the live data is untouched.

    Kopia restores ``<snapshot>/<folder>`` into the staging folder itself,
    which then takes the folder's own owner and mode (a database folder stays
    owned by its container user). Every block read is authenticated, so a
    damaged backup fails here rather than reaching the live data.
    """
    paths = paths or RuntimePaths()
    tag = plan["tag"]
    for name, state in sorted(plan["roots"].items()):
        if state != "planned":
            continue
        staging = _place(name, STAGED, tag, paths)
        if staging.exists():
            wipe([staging], log, paths=paths)  # a partial attempt from before
        staging.mkdir(mode=0o700)
        _connect(log, paths)
        rc, output = _kopia(
            ["snapshot", "restore", "--no-ignore-permission-errors", f"{plan['snapshot_id']}/{name}", "/restore"],
            log,
            mounts=[(staging, "/restore", False)],
            paths=paths,
            timeout=6 * 3600,
        )
        if rc or not staging.is_dir():
            raise BackupError(f"{name} could not be restored and checked: {output[-500:]}")
        plan = {**plan, "roots": {**plan["roots"], name: "staged"}}
        save(plan)
    return plan


def swap(plan: dict[str, Any], save: Callable[[dict[str, Any]], None], *, paths=None) -> dict[str, Any]:
    """Put every staged folder in place, keeping each live one aside. Safe to repeat.

    Renames on one filesystem are each atomic, but a set of them is not;
    the journal and the folders' names together say how far it got.
    """
    paths = paths or RuntimePaths()
    tag = plan["tag"]
    for name, state in sorted(plan["roots"].items()):
        if state == "swapped":
            continue
        live, previous = paths.data / name, _place(name, PREVIOUS, tag, paths)
        staged = _place(name, STAGED, tag, paths)
        if not staged.exists():
            # The second rename may have committed before the journal write.
            if state == "staged" and live.is_dir() and (previous.is_dir() or name in plan.get("absent", [])):
                plan = {**plan, "roots": {**plan["roots"], name: "swapped"}}
                save(plan)
                continue
            raise BackupError(f"The restored copy of {name} is missing, so the restore cannot finish.")
        if live.exists() and not previous.exists():
            os.rename(live, previous)
        if live.exists():
            raise BackupError(f"{name} changed while it was being restored.")
        os.rename(staged, live)
        plan = {**plan, "roots": {**plan["roots"], name: "swapped"}}
        save(plan)
    return plan


def undo(plan: dict[str, Any], save: Callable[[dict[str, Any]], None], *, paths=None) -> dict[str, Any]:
    """Put every folder kept aside back in place. Safe to repeat."""
    paths = paths or RuntimePaths()
    tag = plan["tag"]
    for name in sorted(plan["roots"], reverse=True):
        live, previous = paths.data / name, _place(name, PREVIOUS, tag, paths)
        discard = _place(name, DISCARD, tag, paths)
        if previous.exists():
            if live.exists():
                os.rename(live, discard)
            os.rename(previous, live)
        elif (
            name in plan.get("absent", [])
            and plan["roots"][name] in {"staged", "swapped"}
            and not _place(name, STAGED, tag, paths).exists()
            and live.exists()
        ):
            os.rename(live, discard)  # the folder did not exist before the restore
        plan = {**plan, "roots": {**plan["roots"], name: "undone"}}
        save(plan)
    return plan


def leftovers(plan: dict[str, Any], *, paths=None) -> list[Path]:
    paths = paths or RuntimePaths()
    return [
        place
        for name in plan.get("roots", {})
        for kind in (STAGED, PREVIOUS, DISCARD)
        if (place := _place(name, kind, plan["tag"], paths)).exists()
    ]


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


def restore(
    service_id: str,
    snapshot_id: str,
    data_directories: list[Path],
    log: Log,
    *,
    paths: RuntimePaths | None = None,
    tag: str = "",
    save: Callable[[dict[str, Any]], None] | None = None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage, verify and swap in a backup of the app's (stopped) data folders, then tidy up.

    For callers without a journal; ``ctl.lifecycle.maintenance`` runs the
    steps itself so it can keep the previous folders until the app is healthy.
    """
    paths = paths or RuntimePaths()
    save = save or (lambda _plan: None)
    plan = plan or plan_restore(
        service_id, snapshot_id, data_directories, log, tag=tag or secrets.token_hex(4), paths=paths
    )
    try:
        plan = stage(service_id, plan, log, save, paths=paths)
        plan = swap(plan, save, paths=paths)
    except (BackupError, OSError):
        undo(plan, save, paths=paths)
        wipe(leftovers(plan, paths=paths), log, paths=paths)
        raise
    wipe(leftovers(plan, paths=paths), log, paths=paths)
    return plan
