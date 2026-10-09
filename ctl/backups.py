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
import re
import secrets
import shutil
import tempfile
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
    Mu3Lab writes verification metadata only after a snapshot and ``restic
    check`` succeed.  Merely creating the parent folder therefore remains
    ``not_configured``.
    """
    repository = paths.backups
    engine_available = shutil.which("docker") is not None
    repository_present = (repository / "config").is_file()
    verification = _verification(paths)
    snapshot_ok = bool(verification.get("snapshot_id"))
    integrity_ok = bool(verification.get("integrity_checked_at"))
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
        "last_verified_at": str(verification.get("integrity_checked_at", "")),
        "off_device": False,
        "state": state,
    }


def _verification(paths: RuntimePaths) -> dict:
    return records.get("backup", "verification", paths)


def _repository_password(paths: RuntimePaths) -> str:
    """Create the repository password once and keep it in the encrypted store."""
    with locked(paths.state / "backup-password.lock"):
        store = SecretStore(paths)
        password = store.get("platform", "backup-password")
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
        if directory.parent != paths.data:
            raise BackupError("Refusing to back up a folder outside the Mu3Lab data root.")
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
    rc, output = _restic(["check"], log, paths=paths, timeout=3600)
    if rc:
        raise BackupError(f"The backup was saved but its integrity check failed: {output[-500:]}")
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


def restore(
    service_id: str,
    snapshot_id: str,
    data_directories: list[Path],
    log: Log,
    *,
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    """Make each of the app's (stopped) data folders match the snapshot exactly.

    Each folder is restored by its own run that can see only that folder, so
    ``--delete`` can never remove anything outside it.
    """
    paths = paths or RuntimePaths()
    if not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise BackupError("Unknown backup.")
    chosen = next((snap for snap in snapshots(service_id, log, paths) if snap["id"] == snapshot_id), None)
    if not chosen:
        raise BackupError("That backup does not belong to this app.")
    allowed = {f"/data/{directory.name}": directory for directory in data_directories if directory.parent == paths.data}
    unknown = [path for path in chosen["paths"] if path not in allowed]
    if unknown or not chosen["paths"]:
        raise BackupError("That backup was made for a different folder layout and cannot be restored here.")
    for target in chosen["paths"]:
        directory = allowed[target]
        directory.mkdir(parents=True, exist_ok=True)
        rc, output = _restic(
            ["restore", f"{snapshot_id}:{target}", "--target", target, "--delete"],
            log,
            mounts=[(directory, target, False)],
            paths=paths,
            timeout=6 * 3600,
        )
        if rc:
            raise BackupError(f"{directory.name} could not be restored: {output[-500:]}")
    return chosen
