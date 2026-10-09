"""Mu3Lab :: ctl/backups.py

WHAT: Local Restic backups of one app's data folders, their restore, and
      read-only readiness inspection.
WHY: Git is not a database or personal-data backup system. Backups remain
     encrypted, local, service-scoped, and independently restorable.
HOW: App data belongs to container users (postgres, www-data), which the
     unprivileged worker cannot read. Restic therefore runs in a pinned,
     network-less container that sees only the repository, its password and
     the app's own folders under the data root. Callers stop the app first so
     databases are captured consistently.
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

RESTIC_IMAGE = "docker.io/restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510"
# Restic records the hostname; a container's is random, which would split
# every app's history into one group per run.
HOST = "mu3lab"
REASONS = frozenset({"manual", "pre-update", "pre-restore"})
_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{8,64}$")
_VERSION = re.compile(r"^[A-Za-z0-9._+-]{1,80}$")
_RELEASE_ID = re.compile(r"^[0-9a-f]{16}$")
# Every restic container carries this label so one that outlived its Docker
# CLI (a timeout, a worker restart) can be found before anything else runs.
CONTAINER_LABEL = "mu3lab.role=restic"
# Commands that change the repository or an app's data.
MUTATING = frozenset({"init", "backup", "check", "forget", "restore"})
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

    def restic_args(self) -> list[str]:
        """Return explicit retention flags for a future authenticated backup job."""
        return ["--keep-daily", str(self.daily), "--keep-weekly", str(self.weekly), "--keep-monthly", str(self.monthly)]


def readiness(paths: RuntimePaths = RuntimePaths(), retention: Retention = Retention()) -> dict:
    """Report verified backup facts, never infer protection from a directory.

    Restic creates a ``config`` file only after repository initialization.
    The repository counts as verified only while its last structural check
    passed within ``STALE_DAYS``; each app's own protection is ``protection``.
    """
    repository = paths.backups
    engine_available = shutil.which("docker") is not None
    repository_present = (repository / "config").is_file()
    check = records.get("backup", "repository", paths)
    checked_at = str(check.get("structure_checked_at", "")) if check.get("ok") else ""
    snapshot_ok = bool(records.get("backup", "verification", paths).get("snapshot_id"))
    integrity_ok = bool(checked_at) and not _older_than(checked_at, STALE_DAYS)
    if not engine_available or not repository_present:
        state = "not_configured"
    elif snapshot_ok and integrity_ok:
        state = "verified"
    else:
        state = "local_only"
    return {
        "engine": "restic",
        "available": engine_available,
        "repository_path": str(repository),
        "repository_present": repository_present,
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
    if not (paths.backups / "config").is_file():
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
    """Check the repository; with ``read_data`` (e.g. "5%") also read back that share of stored data."""
    paths = paths or RuntimePaths()
    args = ["check", *([f"--read-data-subset={read_data}"] if read_data else [])]
    rc, output = _restic(args, log, paths=paths, timeout=6 * 3600 if read_data else 3600)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    current = records.get("backup", "repository", paths)
    if rc:
        records.put("backup", "repository", {**current, "ok": False, "failed_at": now}, paths)
        raise BackupError(f"The backup repository check failed: {output[-500:]}")
    update = {"ok": True, "structure_checked_at": now}
    if read_data:
        update["data_checked_at"] = now
    records.put("backup", "repository", {**current, **update}, paths)


def _repository_password(paths: RuntimePaths) -> str:
    """Create the repository password once and keep it in the encrypted store.

    A repository that already exists is never given a new password: that
    would only lock Mu3Lab out of the backups it has.
    """
    with locked(paths.state / "backup-password.lock"):
        store = SecretStore(paths)
        password = store.get("platform", "backup-password")
        if password is None and (paths.backups / "config").is_file():
            raise BackupError(
                "The backup repository exists but Mu3Lab's password for it is missing, so it cannot be opened."
            )
        if password is None:
            password = secrets.token_urlsafe(48)
            store.put("platform", "backup-password", password)
        return password


def _restic(
    args: list[str],
    log: Log,
    *,
    mounts: Sequence[tuple[Path, str, bool]] = (),
    paths: RuntimePaths,
    timeout: int = 3600,
) -> tuple[int, str]:
    """Run one restic command against the local repository in its pinned container.

    The container is named and labelled. Before a command that changes data
    runs, any restic container still running from an earlier attempt is waited
    for, so a retried restore never races the previous one. If this command
    times out, its container is stopped before returning: killing the Docker
    CLI alone would leave restic writing.
    """
    if args and args[0] in MUTATING:
        # One data-changing restic command at a time, across every process.
        try:
            with resource_locks.hold(REPOSITORY_LOCK, paths=paths):
                survivor = _wait_for_survivors(log)
                if survivor:
                    return 1, survivor
                return _run_restic(args, log, mounts=mounts, paths=paths, timeout=timeout)
        except resource_locks.Busy:
            return 1, "Another backup task is still using the backup repository; try again later."
    return _run_restic(args, log, mounts=mounts, paths=paths, timeout=timeout)


def _run_restic(
    args: list[str], log: Log, *, mounts: Sequence[tuple[Path, str, bool]], paths: RuntimePaths, timeout: int
) -> tuple[int, str]:
    name = f"mu3lab-restic-{secrets.token_hex(6)}"
    with tempfile.TemporaryDirectory(prefix="mu3lab-restic-") as temporary:
        password_file = Path(temporary) / "password"
        write_atomic(password_file, _repository_password(paths).encode())
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
            "-v",
            f"{paths.backups}:/repo",
            "-v",
            f"{password_file}:/run/restic-password:ro",
            "-e",
            "RESTIC_REPOSITORY=/repo",
            "-e",
            "RESTIC_PASSWORD_FILE=/run/restic-password",
        ]
        for source, target, read_only in mounts:
            command.extend(["-v", f"{source}:{target}{':ro' if read_only else ''}"])
        command.extend([RESTIC_IMAGE, "--no-cache", "--retry-lock", "10m", *args])
        rc, output = actions.docker_cmd(command, log, timeout=timeout)
        if rc == 124:
            _stop_container(name, log)
        return rc, output


def _running_restic() -> list[str]:
    rc, output = actions.docker_cmd(
        ["docker", "ps", "--quiet", "--filter", f"label={CONTAINER_LABEL}"], lambda _line: None, timeout=30
    )
    return output.split() if rc == 0 else []


def _wait_for_survivors(log: Log) -> str:
    """Wait for earlier restic containers to exit; return an error if they don't."""
    deadline = time.monotonic() + SURVIVOR_WAIT_SECONDS
    running = _running_restic()
    if running:
        log("A backup command from an earlier attempt is still running; waiting for it to finish.")
    while running:
        if time.monotonic() >= deadline:
            return (
                "A backup command from an earlier attempt is still running, so nothing was changed. "
                "Try again once it has finished."
            )
        time.sleep(5)
        running = _running_restic()
    return ""


def _stop_container(name: str, log: Log) -> None:
    """Stop a restic container that outlived its command; restic exits cleanly on SIGTERM."""
    actions.docker_cmd(["docker", "stop", "--time", str(STOP_SECONDS), name], log, timeout=STOP_SECONDS + 30)


def _json_lines(output: str) -> list[Any]:
    """Docker merges restic's stderr into its output; keep only the JSON lines."""
    values = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(("{", "[")):
            try:
                values.append(json.loads(line))
            except ValueError:
                continue
    return values


def ensure_repository(log: Log, paths: RuntimePaths | None = None) -> None:
    paths = paths or RuntimePaths()
    if (paths.backups / "config").is_file():
        return
    rc, output = _restic(["init"], log, paths=paths, timeout=300)
    if rc or not (paths.backups / "config").is_file():
        raise BackupError(f"The backup repository could not be created: {output[-500:]}")


def sources(data_directories: list[Path], paths: RuntimePaths | None = None) -> list[tuple[Path, str]]:
    """Map each existing app data folder to its stable path inside snapshots."""
    paths = paths or RuntimePaths()
    found = []
    for directory in data_directories:
        _check_root(directory, paths)
        if directory.exists():
            found.append((directory, f"/data/{directory.name}"))
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
    """Snapshot the app's (stopped) data folders, verify the repository, apply retention.

    ``prune_old=False`` leaves retention to the caller: an update or restore
    prunes only once nothing it may still need to put back can be removed.
    """
    paths = paths or RuntimePaths()
    if reason not in REASONS:
        raise BackupError("unknown backup reason")
    mapped = sources(data_directories, paths)
    if not mapped:
        raise BackupError("This app has no data on this server to back up yet.")
    ensure_repository(log, paths)
    tags = [f"app:{service_id}", f"reason:{reason}"]
    if version and _VERSION.fullmatch(version):
        tags.append(f"version:{version}")
    if release_id and _RELEASE_ID.fullmatch(release_id):
        # The exact deployment the data belongs to; the version is only its label.
        tags.append(f"release:{release_id}")
    tag_args = [part for tag in tags for part in ("--tag", tag)]
    rc, output = _restic(
        ["backup", "--json", "--host", HOST, *tag_args, *(target for _source, target in mapped)],
        log,
        mounts=[(source, target, True) for source, target in mapped],
        paths=paths,
        timeout=6 * 3600,
    )
    summary = next(
        (
            item
            for item in reversed(_json_lines(output))
            if isinstance(item, dict) and item.get("message_type") == "summary"
        ),
        None,
    )
    if rc or not summary or not summary.get("snapshot_id"):
        raise BackupError(f"The backup did not complete: {output[-500:]}")
    snapshot_id = str(summary["snapshot_id"])
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
        "files": int(summary.get("total_files_processed") or 0),
        "bytes": int(summary.get("total_bytes_processed") or 0),
    }


def prune(service_id: str, log: Log, *, paths: RuntimePaths | None = None, retention: Retention = Retention()) -> bool:
    """Apply the retention policy to one app's snapshots.

    Retention is housekeeping: a failure leaves an extra snapshot, never a
    missing one.
    """
    paths = paths or RuntimePaths()
    rc, output = _restic(
        [
            "forget",
            "--prune",
            "--host",
            HOST,
            "--tag",
            f"app:{service_id}",
            "--keep-last",
            "3",
            *retention.restic_args(),
        ],
        log,
        paths=paths,
    )
    if rc:
        log(f"Old backups were not pruned this time: {output[-300:]}")
    return rc == 0


def _tag(tags: list[str], name: str) -> str:
    return next((tag.split(":", 1)[1] for tag in tags if tag.startswith(f"{name}:")), "")


def snapshots(service_id: str, log: Log = lambda _line: None, paths: RuntimePaths | None = None) -> list[dict]:
    """The app's snapshots, newest first. An uninitialized repository has none."""
    paths = paths or RuntimePaths()
    if not (paths.backups / "config").is_file():
        return []
    rc, output = _restic(
        ["snapshots", "--json", "--host", HOST, "--tag", f"app:{service_id}"], log, paths=paths, timeout=120
    )
    listing = next((item for item in _json_lines(output) if isinstance(item, list)), None)
    if rc or listing is None:
        raise BackupError(f"Backups could not be listed: {output[-300:]}")
    result = []
    for item in listing:
        tags = [str(tag) for tag in item.get("tags") or []]
        result.append(
            {
                "id": str(item.get("id", "")),
                "short_id": str(item.get("short_id") or str(item.get("id", ""))[:8]),
                "time": str(item.get("time", "")),
                "reason": _tag(tags, "reason") or "manual",
                "version": _tag(tags, "version"),
                "release_id": _tag(tags, "release"),
                "paths": [str(path) for path in item.get("paths") or []],
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
    """Restore each folder beside the live one and verify it; the live data is untouched.

    Restoring the parent path keeps each folder's own owner and mode (a
    database folder must stay owned by its container user).
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
        rc, output = _restic(
            ["restore", plan["snapshot_id"], "--target", "/restore", "--include", f"/data/{name}", "--verify"],
            log,
            mounts=[(staging, "/restore", False)],
            paths=paths,
            timeout=6 * 3600,
        )
        if rc or not (staging / "data" / name).is_dir():
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
        staged = _place(name, STAGED, tag, paths) / "data" / name
        if not staged.exists():
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
        elif name in plan.get("absent", []) and plan["roots"][name] == "swapped" and live.exists():
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
            RESTIC_IMAGE,
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
