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
   the old one, install batches and pending chat approvals are retired,
   and setup progress starts again.

The rest of the normal install then runs on top. Once core setup has
succeeded, the worker starts each app that was running before
(``resume``). The whole flow is covered by tests but has not yet been
rehearsed on a second machine.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
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
# Folders inside the ``mu3lab`` backup (see ctl.backup_service.sources).
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


def _started(paths: RuntimePaths) -> dict:
    try:
        value = json.loads((paths.state / STARTED).read_text(encoding="utf-8"))
        if value.get("format") != 1 or not isinstance(value.get("owned"), dict):
            raise ValueError("unknown ownership record")
        return value
    except (OSError, ValueError, AttributeError):
        raise RebuildError(
            "The interrupted rebuild has no trustworthy ownership record. Files were preserved; manual recovery is required."
        ) from None


def _save_started(paths: RuntimePaths, value: dict) -> None:
    write_atomic(paths.state / STARTED, json.dumps(value, sort_keys=True).encode())


def _owned_path(paths: RuntimePaths, name: str) -> Path:
    if not isinstance(name, str):
        raise RebuildError("The rebuild ownership record names an unsafe path. Files were preserved.")
    parts = Path(name).parts
    allowed_state = {
        ".rebuild-control",
        "kopia",
        "mu3lab.db",
        "mu3lab.db-wal",
        "mu3lab.db-shm",
        "secrets.key",
        "mu3lab.request-key",
        "deployments",
    }
    valid = (
        name == "projects"
        or (len(parts) == 2 and parts[0] == "data" and bool(backups._NAME.fullmatch(parts[1])))
        or (len(parts) == 2 and parts[0] == "state" and parts[1] in allowed_state)
        or (len(parts) == 2 and parts[0] == "backups" and parts[1] in {"kopia", ".kopia-copy"})
    )
    target = paths.root / name
    if not valid or target.is_symlink() or target.resolve() != paths.root.resolve() / name:
        raise RebuildError("The rebuild ownership record names an unsafe path. Files were preserved.")
    return target


def _fingerprint(target: Path, log: Log) -> str:
    # tar reads as root, including container-owned files, links and metadata.
    # Only its digest leaves the container; no private file contents are logged.
    script = (
        "set -e; mkfifo /tmp/inventory; "
        f"tar -cf /tmp/inventory -C /owned -- {shlex.quote(target.name)} & writer=$!; "
        'sha256sum /tmp/inventory; wait "$writer"'
    )
    output = _root_shell(script, [(target.parent, "/owned", True)], log)
    digest = output.split()[0] if output.split() else ""
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RebuildError("Could not verify rebuild file ownership. Files were preserved.")
    return digest


def _own(paths: RuntimePaths, target: Path) -> None:
    name = str(target.relative_to(paths.root))
    target = _owned_path(paths, name)
    value = _started(paths)
    if name in value["owned"]:
        return
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise RebuildError(f"{target} already holds files; it was preserved.")
    value["owned"][name] = None  # crash before a completed inventory must stop cleanup
    _save_started(paths, value)


def _seal(paths: RuntimePaths, targets: list[Path], log: Log) -> None:
    value = _started(paths)
    for target in targets:
        name = str(target.relative_to(paths.root))
        if name in value["owned"]:
            value["owned"][name] = _fingerprint(target, log) if target.exists() else "absent"
    _save_started(paths, value)


def _clear_partial(paths: RuntimePaths, log: Log, source: Path | None = None) -> None:
    """Validate the entire ownership inventory before deleting any exact path."""
    started = _started(paths)
    if source is not None and started.get("source") != str(source.resolve()):
        raise RebuildError("The backup source changed since the interrupted rebuild. Files were preserved.")
    if source is not None and started.get("source_identity") != _fingerprint(source / REPOSITORY_FILE, log):
        raise RebuildError("The backup repository changed since the interrupted rebuild. Files were preserved.")
    targets = []
    for name, expected in started["owned"].items():
        target = _owned_path(paths, name)
        if not target.exists():
            continue
        if not expected or expected == "absent" or _fingerprint(target, log) != expected:
            raise RebuildError(
                f"{target} changed or its restore was interrupted before ownership could be verified. Files were preserved; manual recovery is required."
            )
        if source is not None and (source == target or target in source.parents):
            raise RebuildError("The backup source overlaps rebuild cleanup. Files were preserved.")
        targets.append(name)
    if targets:
        log("Removing only verified files owned by the interrupted rebuild.")
        _root_shell(
            "rm -rf -- " + " ".join(shlex.quote("/runtime/" + name) for name in targets),
            [(paths.root, "/runtime", False)],
            log,
        )


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
        "test ! -e /to/.kopia-copy && test ! -e /to/kopia && cp -a /from/. /to/.kopia-copy && mv /to/.kopia-copy /to/kopia",
        [(source, "/from", True), (paths.backups, "/to", False)],
        log,
    )


def _kopia(args: list[str], password: str, log: Log, paths: RuntimePaths, **kwargs) -> str:
    rc, output = backups._kopia(args, log, paths=paths, password=password, timeout=6 * 3600, **kwargs)
    if rc:
        raise RebuildError(output[-500:] or f"Kopia stopped with exit code {rc}.")
    return output


def latest_backups(listing: list[dict]) -> dict[str, dict]:
    """The newest backup of each source (``/data/<app>``).

    Backups an update made carry ``app`` and ``folders`` tags; those Kopia's
    backup service made carry none, so their folders are read from the
    backup itself once chosen (``folders`` is then empty here).
    """
    newest: dict[str, dict] = {}
    for item in listing:
        tags = item.get("tags") or {}
        path = str((item.get("source") or {}).get("path") or "")
        app = str(tags.get("tag:app") or (path.removeprefix("/data/") if path.startswith("/data/") else ""))
        folders = [name for name in str(tags.get("tag:folders") or "").split("+") if name]
        if (
            not app
            or not backups._NAME.fullmatch(app)
            or (tags and not folders)
            or any(not backups._NAME.fullmatch(name) for name in folders)
        ):
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
    for entry in found.values():
        if not entry["folders"]:
            try:
                entry["folders"] = backups.folders_in(entry["id"], log, paths, password=password)
            except backups.BackupError as exc:
                raise RebuildError(str(exc)) from exc
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
    authority = staging / "gateway-authority.db"
    target = paths.projects / "mcp-gateway" / "authority" / "authority.db"
    if target.exists() and not authority.is_file():
        raise RebuildError("The gateway backup has no consistent authority export. Manual recovery is required.")
    if authority.is_file() and not (staging / "gateway-arguments.key").is_file():
        raise RebuildError("The gateway backup has no matching encryption key. Manual recovery is required.")
    for name in ("mu3lab.db", "secrets.key"):
        os.replace(staging / name, paths.state / name)
    (paths.state / "secrets.key").chmod(0o600)
    request_key = staging / "mu3lab.request-key"
    if request_key.is_file():
        os.replace(request_key, paths.state / request_key.name)
        (paths.state / request_key.name).chmod(0o600)
    if (staging / "deployments").is_dir():
        os.replace(staging / "deployments", paths.state / "deployments")
    if authority.is_file():
        # The consistent copy, not whatever the projects folder caught mid-write.
        target.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            target.with_name(target.name + suffix).unlink(missing_ok=True)
        os.replace(authority, target)
        key = staging / "gateway-arguments.key"
        os.replace(key, target.parent / "arguments.key")
        (target.parent / "arguments.key").chmod(0o600)
        from gateway_authority import Authority

        Authority(target.parent, initialize=False).reset_after_restore()
    shutil.rmtree(staging, ignore_errors=True)


def prepare_database(database: Path) -> dict[str, int]:
    """Make the old computer's database right for this one: no leftover work, setup progress afresh."""
    now = _now()
    with closing(sqlite3.connect(database)) as conn, conn:
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
        if "install_batches" in tables:
            conn.execute(
                "UPDATE install_batches SET state='cancelled', updated_at=? "
                "WHERE state IN ('queued','running','paused','resetting','reset_failed','completed_with_failures')",
                (now,),
            )
            conn.execute(
                "UPDATE install_batch_items SET state='cancelled', completed_at=?, download_state='' "
                "WHERE state IN ('pending','queued','running','resetting','reset_pending','reset_failed','blocked_by_dependency')",
                (now,),
            )
        if "write_confirmations" in tables:
            conn.execute("DELETE FROM write_confirmations")
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
    source = source.resolve()
    if (
        source == paths.root.resolve()
        or source in paths.root.resolve().parents
        or any(
            source == folder.resolve() or folder.resolve() in source.parents
            for folder in (paths.data, paths.projects, paths.state)
        )
    ):
        raise RebuildError(
            "The backup source overlaps rebuild destinations. Move it outside data, projects and state first."
        )
    repository = (paths.backups / "kopia").resolve()
    if source != repository and source in repository.parents:
        raise RebuildError(
            "The backup source contains the destination repository. Move it outside the runtime root first."
        )
    if status(paths).get("restored_at"):
        return status(paths)
    for folder in (paths.data, paths.projects, paths.state, paths.backups):
        folder.mkdir(parents=True, exist_ok=True)
    if interrupted(paths):
        _clear_partial(paths, log, source)
        for folder in (paths.data, paths.projects, paths.state, paths.backups):
            folder.mkdir(parents=True, exist_ok=True)
    found = existing_data(paths, source)
    if found:
        raise RebuildError(
            "This computer already has Mu3Lab data, and a rebuild never overwrites it: "
            + ", ".join(found)
            + ". Rebuild on a fresh computer, or run ./uninstall.sh --everything here first."
        )
    copied = source != (paths.backups / "kopia").resolve()
    write_atomic(
        paths.state / STARTED,
        json.dumps(
            {
                "format": 1,
                "owned": {},
                "started_at": _now(),
                "source": str(source),
                "source_identity": _fingerprint(source / REPOSITORY_FILE, log),
                "copied": copied,
            }
        ).encode(),
    )
    if copied:
        _own(paths, paths.backups / ".kopia-copy")
        _own(paths, paths.backups / "kopia")
    _copy_repository(source, paths, log)
    _seal(paths, [paths.backups / ".kopia-copy", paths.backups / "kopia"], log)
    _own(paths, paths.state / "kopia")
    try:
        snapshots = _open(password, paths, log)
    finally:
        _seal(paths, [paths.state / "kopia"], log)
    platform = snapshots.pop(backups.PLATFORM_SOURCE)
    if CONTROL not in platform["folders"]:
        raise RebuildError("The backup of Mu3Lab itself is incomplete: it has no settings folder.")
    log(f"Restoring Mu3Lab's own settings and secrets (backup {platform['id'][:8]} from {platform['time']}).")
    staging = paths.state / ".rebuild-control"
    _own(paths, staging)
    _restore_folder(platform["id"], CONTROL, staging, password, paths, log)
    _seal(paths, [staging], log)
    if PROJECTS in platform["folders"]:
        _own(paths, paths.projects)
        _restore_folder(platform["id"], PROJECTS, paths.projects, password, paths, log)
        _seal(paths, [paths.projects], log)
    try:
        old_uid = (staging / "export.json").stat().st_uid
    except OSError as exc:
        raise RebuildError("The backup of Mu3Lab itself has no export.json; it cannot be trusted.") from exc
    _owner_to_this_user([staging, paths.projects], old_uid, log)
    _seal(paths, [staging, paths.projects], log)
    control_targets = [
        paths.state / name
        for name in ("mu3lab.db", "mu3lab.db-wal", "mu3lab.db-shm", "secrets.key", "mu3lab.request-key", "deployments")
    ]
    for target in control_targets:
        _own(paths, target)
    _place_control(staging, paths)
    stored = backups.repository_password(paths)
    if stored != password:
        raise RebuildError("The restored settings hold a different backup password than the one typed.")
    tidy = prepare_database(paths.state / "mu3lab.db")
    _seal(paths, [staging, paths.projects, *control_targets], log)
    restored = []
    for app_id, snapshot in sorted(snapshots.items()):
        log(f"Restoring {app_id}'s data (backup {snapshot['id'][:8]} from {snapshot['time']}).")
        for folder in snapshot["folders"]:
            target = paths.data / folder
            if target.exists() and any(target.iterdir()):
                raise RebuildError(f"{target} already holds data; a rebuild never overwrites it.")
            _own(paths, target)
            _restore_folder(snapshot["id"], folder, target, password, paths, log)
            _seal(paths, [target], log)
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
    done = set(marker.get("started") or [])
    waiting = set(marker.get("busy") or [])
    queued, busy = [], []
    for service in load().services:
        if service.id in done:
            continue
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
        except JobConflict:
            # Another job for it is still waiting; try again on the next pass rather than forget it.
            busy.append(service.id)
            if service.id not in waiting:
                log(f"{service.name} has another job waiting; it is started after the rebuild once that finishes.")
        except ValueError as exc:
            log(f"{service.name} was not started after the rebuild: {exc}")
    started = sorted(done | set(queued))
    write_atomic(
        _marker(paths),
        json.dumps({**marker, "apps_started": not busy, "started": started, "busy": busy}, indent=2).encode(),
    )
    if queued:
        log(f"Starting {len(queued)} apps that were running before the rebuild.")
    return queued
