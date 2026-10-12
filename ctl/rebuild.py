"""Rebuild Mu3Lab on a new computer from a copy of its backups (``./install.sh --restore``).

The owner brings two things from the recovery sheet: the backup password and
a copy of the backups folder (``/srv/mu3lab/backups/kopia`` from the old
computer). The installer asks for both before it starts, then runs this
step right after the data folders exist and before anything creates new
secrets, a new database or new app data:

1. Refuse if this computer already has Mu3Lab data; a rebuild never
   overwrites anything.
2. Copy the backups into place and open them with the typed password. A
   wrong password stops here, before anything else changes.
3. Restore Mu3Lab's own state from the newest ``mu3lab`` backup: the control
   database, the encryption key, deployment copies, the tool gateway's
   database and the runtime projects. Mu3Lab's own encrypted store, and so
   every app's passwords and keys, come back with them.
4. Restore each app's data folders from that app's newest backup, so sign-in,
   the password vault, chat and every app start with their old data.
5. Tidy the restored database for a new computer: jobs that were running on
   the old one are cancelled, and setup progress starts again, so the
   dashboard never claims a step it has not seen succeed here.

The rest of the normal install then runs on top. Once core setup has
succeeded, the worker starts each app that was running before
(``resume``). The whole flow is covered by tests but has not yet been
rehearsed on a second machine.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl import actions, backups
from ctl.recovery_sheet import password_check
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic

Log = Callable[[str], None]
MARKER = "rebuild.json"
# Written before a rebuild changes anything; removed only when it has finished.
STARTED = "rebuild-started.json"
REPOSITORY_FILE = "kopia.repository.f"
ACTOR = "rebuild"
# Folders inside the ``mu3lab`` backup (see platform_backup._back_up_platform).
CONTROL, PROJECTS = "control", "projects"


class RebuildError(Exception):
    """A rebuild cannot go on; the message says what to do."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def check_source(folder: str) -> tuple[Path | None, str]:
    """The backups folder the owner typed, or why it cannot be used. Needs no Docker."""
    path = Path(folder.strip()).expanduser()
    if not folder.strip():
        return None, "Type the folder that holds the copied backups."
    if not path.is_dir():
        return None, f"{path} is not a folder on this computer."
    try:
        present = (path / REPOSITORY_FILE).exists()
    except PermissionError:
        # Copied as root (for example straight from /srv/mu3lab); the rebuild reads it as root too.
        present = True
    if not present:
        nested = path / "kopia"
        if nested.is_dir() and (nested / REPOSITORY_FILE).exists():
            return nested, ""
        return None, f"{path} does not look like Mu3Lab backups: {REPOSITORY_FILE} is missing."
    return path.resolve(), ""


def describe_password(password: str) -> str:
    """The recovery sheet's check code for a typed password, so the owner can compare."""
    return password_check(password)


def _marker(paths: RuntimePaths) -> Path:
    return paths.state / MARKER


def status(paths: RuntimePaths | None = None) -> dict[str, Any]:
    paths = paths or RuntimePaths()
    try:
        return json.loads(_marker(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def interrupted(paths: RuntimePaths | None = None) -> bool:
    """A rebuild started here and did not finish, so what it restored so far is incomplete."""
    paths = paths or RuntimePaths()
    return (paths.state / STARTED).exists() and not status(paths).get("restored_at")


def _clear_partial(paths: RuntimePaths, log: Log) -> None:
    """Remove what an interrupted rebuild restored; only it wrote here, since it refuses a computer with data.

    The backups folder is removed only when the rebuild made it as a copy of
    backups kept elsewhere; backups the owner put in place are never touched.
    """
    log("Removing what the interrupted rebuild had restored, then starting again.")
    try:
        started = json.loads((paths.state / STARTED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        started = {}
    copy = " /backups/kopia /backups/.kopia-copy" if started.get("copied") else ""
    _root_shell(
        f"rm -rf /data/* /data/.[!.]* /projects/* /projects/.[!.]*{copy} "
        "/state/mu3lab.db /state/mu3lab.db-wal /state/mu3lab.db-shm /state/secrets.key "
        "/state/deployments /state/.rebuild-control /state/kopia",
        [(paths.data, "/data", False), (paths.projects, "/projects", False), (paths.backups, "/backups", False),
         (paths.state, "/state", False)],
        log,
    )  # fmt: skip


def existing_data(paths: RuntimePaths, source: Path | None = None) -> list[str]:
    """What on this computer a rebuild would have to overwrite; it refuses if anything.

    The backups being restored from may already sit in their usual place.
    """
    found = []
    if (paths.state / "mu3lab.db").exists() or (paths.state / "secrets.key").exists():
        found.append(str(paths.state))
    for folder in (paths.data, paths.projects):
        try:
            if folder.is_dir() and any(folder.iterdir()):
                found.append(str(folder))
        except PermissionError:
            found.append(str(folder))
    repository = paths.backups / "kopia"
    if source is None or source != repository.resolve():
        try:
            if repository.is_dir() and any(repository.iterdir()):
                found.append(str(repository))
        except PermissionError:
            found.append(str(repository))
    return found


def _root_shell(script: str, mounts: list[tuple[Path, str, bool]], log: Log, *, timeout: int = 6 * 3600) -> str:
    """Run a short shell script as root in the pinned Kopia image, networkless, seeing only ``mounts``."""
    command = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh"]
    for source, target, read_only in mounts:
        command.extend(["-v", f"{source}:{target}{':ro' if read_only else ''}"])
    command.extend([backups.KOPIA_IMAGE, "-c", script])
    rc, output = actions.docker_cmd(command, log, timeout=timeout)
    if rc:
        raise RebuildError(f"A file operation in the rebuild failed: {output[-400:]}")
    return output


def _copy_repository(source: Path, paths: RuntimePaths, log: Log) -> None:
    target = paths.backups / "kopia"
    if source == target.resolve():
        return  # the owner already copied them into place
    paths.backups.mkdir(parents=True, exist_ok=True)
    log(f"Copying the backups from {source} (this can take a while for large backups).")
    _root_shell(
        "rm -rf /to/.kopia-copy && cp -a /from/. /to/.kopia-copy && rm -rf /to/kopia && mv /to/.kopia-copy /to/kopia",
        [(source, "/from", True), (paths.backups, "/to", False)],
        log,
    )


def _kopia(args: list[str], password: str, log: Log, paths: RuntimePaths, **kwargs) -> str:
    rc, output = backups._kopia(args, log, paths=paths, password=password, timeout=6 * 3600, **kwargs)
    if rc:
        raise RebuildError(output[-500:] or f"Kopia stopped with exit code {rc}.")
    return output


def latest_backups(listing: list[dict]) -> dict[str, dict]:
    """The newest backup of each source, by its ``app`` tag."""
    newest: dict[str, dict] = {}
    for item in listing:
        tags = item.get("tags") or {}
        app = str(tags.get("tag:app") or "")
        folders = [name for name in str(tags.get("tag:folders") or "").split("+") if name]
        if not app or not folders or not backups._NAME.fullmatch(app):
            continue
        entry = {"id": str(item.get("id", "")), "time": str(item.get("startTime", "")), "folders": folders}
        if app not in newest or entry["time"] > newest[app]["time"]:
            newest[app] = entry
    return newest


def _open(password: str, paths: RuntimePaths, log: Log) -> dict[str, dict]:
    try:
        _kopia(["repository", "connect", "filesystem", "--path=/repo", *backups.IDENTITY], password, log, paths)
    except RebuildError as exc:
        text = str(exc).lower()
        if "password" in text or "invalid" in text or "decrypt" in text:
            raise RebuildError(
                "The backups did not accept that password. Check it against your recovery sheet "
                "(the check codes must match) and run ./install.sh --restore again."
            ) from exc
        raise RebuildError(f"The backups could not be opened: {exc}") from exc
    listing = backups._json(_kopia(["snapshot", "list", "--all", "--json"], password, log, paths), list) or []
    found = latest_backups(listing)
    if backups.PLATFORM_SOURCE not in found:
        raise RebuildError(
            "These backups hold no backup of Mu3Lab itself, so the old settings and passwords cannot come back. "
            "On the old computer, press Back up now under Security & backups, or let a scheduled backup run."
        )
    return found


def _restore_folder(snapshot_id: str, folder: str, target: Path, password: str, paths: RuntimePaths, log: Log):
    """Restore one backed-up folder into ``target``, which must be new or empty."""
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    _kopia(
        ["snapshot", "restore", "--no-ignore-permission-errors", f"{snapshot_id}/{folder}", "/restore"],
        password,
        log,
        paths,
        mounts=[(target, "/restore", False)],
    )


def _owner_to_this_user(folders: list[Path], old_uid: int, log: Log) -> None:
    """Mu3Lab's own files belonged to the old computer's user; give them to this one. App users keep theirs."""
    uid, gid = os.getuid(), os.getgid()
    if old_uid == uid:
        return
    mounts = [(folder, f"/f{index}", False) for index, folder in enumerate(folders)]
    targets = " ".join(f"/f{index}" for index in range(len(folders)))
    _root_shell(f"find {targets} -uid {old_uid} -exec chown -h {uid}:{gid} {{}} +", mounts, log)


def _place_control(staging: Path, paths: RuntimePaths) -> None:
    for name in ("mu3lab.db", "secrets.key"):
        if not (staging / name).is_file():
            raise RebuildError(f"The backup of Mu3Lab itself has no {name}; nothing was put in place.")
    for name in ("mu3lab.db", "secrets.key"):
        os.replace(staging / name, paths.state / name)
    (paths.state / "secrets.key").chmod(0o600)
    request_key = staging / "mu3lab.request-key"
    if request_key.is_file():
        os.replace(request_key, paths.state / request_key.name)
        (paths.state / request_key.name).chmod(0o600)
    if (staging / "deployments").is_dir():
        shutil.rmtree(paths.state / "deployments", ignore_errors=True)
        os.replace(staging / "deployments", paths.state / "deployments")
    authority = staging / "gateway-authority.db"
    if authority.is_file():
        # The consistent copy, not whatever the projects folder caught mid-write.
        target = paths.projects / "mcp-gateway" / "authority" / "authority.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(authority, target)
    shutil.rmtree(staging, ignore_errors=True)


def prepare_database(database: Path) -> dict[str, int]:
    """Make the old computer's database right for this one: no leftover work, setup progress afresh."""
    now = _now()
    with sqlite3.connect(database) as conn:
        active = conn.execute(
            "SELECT id FROM jobs WHERE state IN ('queued', 'running', 'waiting_for_confirmation')"
        ).fetchall()
        for (job_id,) in active:
            conn.execute(
                "UPDATE jobs SET state = 'cancelled', updated_at = ?, lease_owner = '', lease_expires_at = '', "
                "detail = 'Stopped by moving Mu3Lab to a new computer.' WHERE id = ?",
                (now, job_id),
            )
            conn.execute(
                "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                (job_id, ACTOR, "job.cancelled", now, "Stopped by moving Mu3Lab to a new computer."),
            )
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        reset = 0
        if "provisioning_steps" in tables:
            reset = conn.execute("DELETE FROM provisioning_steps").rowcount
        if "job_processes" in tables:
            conn.execute("DELETE FROM job_processes")  # commands of the old computer's worker
        conn.execute(
            "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (NULL, ?, ?, ?, ?)",
            (ACTOR, "platform.rebuilt", now, "Mu3Lab was restored from its backups on a new computer."),
        )
    return {"cancelled": len(active), "reset": reset}


def restore(source: Path, password: str, log: Log, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Bring the old computer's Mu3Lab back onto this one. See the module docstring for the order."""
    paths = paths or RuntimePaths()
    if status(paths).get("restored_at"):
        return status(paths)
    for folder in (paths.data, paths.projects, paths.state, paths.backups):
        folder.mkdir(parents=True, exist_ok=True)
    if interrupted(paths):
        _clear_partial(paths, log)
    found = existing_data(paths, source)
    if found:
        raise RebuildError(
            "This computer already has Mu3Lab data, and a rebuild never overwrites it: "
            + ", ".join(found)
            + ". Rebuild on a fresh computer, or run ./uninstall.sh --everything here first."
        )
    copied = source != (paths.backups / "kopia").resolve()
    write_atomic(
        paths.state / STARTED, json.dumps({"started_at": _now(), "source": str(source), "copied": copied}).encode()
    )
    _copy_repository(source, paths, log)
    snapshots = _open(password, paths, log)
    platform = snapshots.pop(backups.PLATFORM_SOURCE)
    if CONTROL not in platform["folders"]:
        raise RebuildError("The backup of Mu3Lab itself is incomplete: it has no settings folder.")
    log(f"Restoring Mu3Lab's own settings and secrets (backup {platform['id'][:8]} from {platform['time']}).")
    staging = paths.state / ".rebuild-control"
    shutil.rmtree(staging, ignore_errors=True)
    _restore_folder(platform["id"], CONTROL, staging, password, paths, log)
    if PROJECTS in platform["folders"]:
        _restore_folder(platform["id"], PROJECTS, paths.projects, password, paths, log)
    try:
        old_uid = (staging / "export.json").stat().st_uid
    except OSError as exc:
        raise RebuildError("The backup of Mu3Lab itself has no export.json; it cannot be trusted.") from exc
    _owner_to_this_user([staging, paths.projects], old_uid, log)
    _place_control(staging, paths)
    stored = backups.repository_password(paths)
    if stored != password:
        raise RebuildError("The restored settings hold a different backup password than the one typed.")
    tidy = prepare_database(paths.state / "mu3lab.db")
    restored = []
    for app_id, snapshot in sorted(snapshots.items()):
        log(f"Restoring {app_id}'s data (backup {snapshot['id'][:8]} from {snapshot['time']}).")
        for folder in snapshot["folders"]:
            target = paths.data / folder
            if target.exists() and any(target.iterdir()):
                raise RebuildError(f"{target} already holds data; a rebuild never overwrites it.")
            _restore_folder(snapshot["id"], folder, target, password, paths, log)
        restored.append(app_id)
    result = {
        "restored_at": _now(),
        "platform_backup": platform["id"],
        "platform_time": platform["time"],
        "apps": restored,
        "cancelled_jobs": tidy["cancelled"],
        "apps_started": False,
    }
    write_atomic(_marker(paths), json.dumps(result, indent=2).encode())
    (paths.state / STARTED).unlink(missing_ok=True)
    log(f"Restored Mu3Lab and {len(restored)} apps. Setup now continues as usual.")
    return result


def resume(store, root: Path, log: Log, paths: RuntimePaths | None = None) -> list[str]:
    """After core setup succeeds on the rebuilt computer, start each app that was running on the old one."""
    from ctl.control_state import ControlState
    from ctl.jobs import JobConflict
    from ctl.registry import load

    paths = paths or RuntimePaths()
    marker = status(paths)
    if not marker.get("restored_at") or marker.get("apps_started"):
        return []
    core_done = any(
        job["service_id"] == "core-suite" and job["state"] == "succeeded" and job["created_at"] > marker["restored_at"]
        for job in store.jobs(200)
    )
    if not core_done:
        return []
    control = ControlState.runtime(paths)
    queued = []
    for service in load().services:
        installed = control.installation(service.id) if control else None
        if service.stage != "optional" or not installed or installed["state"] != "running":
            continue
        if not (paths.projects / service.id / "docker-compose.yml").is_file():
            continue
        try:
            store.create(
                kind="lifecycle",
                service_id=service.id,
                action="start",
                actor=ACTOR,
                detail="Starting the app again after rebuilding Mu3Lab on this computer.",
                idempotency_key=f"rebuild:{marker['restored_at']}:{service.id}",
            )
            queued.append(service.id)
        except (JobConflict, ValueError) as exc:
            log(f"{service.name} was not started after the rebuild: {exc}")
    write_atomic(_marker(paths), json.dumps({**marker, "apps_started": True, "started": queued}, indent=2).encode())
    if queued:
        log(f"Starting {len(queued)} apps that were running before the rebuild.")
    return queued
