"""Where the backups live: Mu3Lab's own folder, or a folder on another drive an administrator chose.

WHAT: The Kopia repository and the restores folder sit together in one backup
      folder: ``/srv/mu3lab/backups`` unless an administrator moved them to
      ``<drive>/mu3lab-backups`` from Settings > Security & backups. The choice
      is a small file in Mu3Lab's state (``backup-location.json``); with no
      file, the backups are in their usual place.
WHY:  A copy on the same drive as the data it protects is lost with that
      drive. Another drive is the household's cheapest protection against one.

A chosen folder carries a marker (``.mu3lab-backups``) naming this choice. A
drive mounted with ``nofail`` leaves an empty folder behind when it is
missing; without the marker Mu3Lab refuses to back up there, so it never
starts a new, empty repository on the main drive under the drive's mount
point. The backup service's Compose file never creates the folder either.

Moving runs as a worker job: the backup service stops, the repository is
copied as root (Kopia's files belong to root) and compared file by file, the
choice switches, Kopia checks every backup's structure in the new place, the
service starts again, and only then is the old copy removed. Any failure
before the switch leaves everything where it was.

The choice is not part of Mu3Lab's own backup: a rebuild on a new computer
starts with the backups in their usual place.
"""

from __future__ import annotations

import json
import os
import pwd
import secrets
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl import actions, process
from ctl.registry import Service
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic
from ctl.store import records

SERVICE_ID = "system:backup-location"
ACTION = "move"
DEFAULT_ID = "default"
FOLDER = "mu3lab-backups"
MARKER = ".mu3lab-backups"
POINTER = "backup-location.json"
# Filesystems that keep Linux owners and permissions: Kopia's files must stay root's and private.
FILESYSTEMS = frozenset({"ext4", "ext3", "xfs", "btrfs", "f2fs"})
# Room left over on the new drive after the copy, beyond 5% of the backups' size.
HEADROOM = 2 * 1024**3
SIZE = ("backup", "location-size")
SIZE_MAX_AGE = 3600
COPY_TIMEOUT = 12 * 3600

Log = Callable[[str], None]


class MoveError(RuntimeError):
    """The move stopped; the message is safe to show."""


# ---------------------------------------------------------------- the current place


def _pointer(paths: RuntimePaths) -> dict[str, str]:
    try:
        stored = json.loads((paths.state / POINTER).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict) or not stored.get("path") or not stored.get("id"):
        return {}
    return {"path": str(stored["path"]), "id": str(stored["id"]), "name": str(stored.get("name") or "")}


def folder(paths: RuntimePaths | None = None) -> Path:
    """The backup folder in use: it holds ``kopia`` (the repository) and ``restores``."""
    paths = paths or RuntimePaths()
    chosen = _pointer(paths)
    return Path(chosen["path"]) if chosen else paths.backups


def problem(paths: RuntimePaths | None = None) -> str:
    """Why nothing can be backed up right now: the chosen drive is missing. Empty when all is well."""
    paths = paths or RuntimePaths()
    chosen = _pointer(paths)
    if not chosen:
        return ""
    try:
        if (Path(chosen["path"]) / MARKER).read_text().strip() == chosen["id"]:
            return ""
    except OSError:
        pass
    drive = chosen["name"] or "The backup drive"
    return (
        f"{drive} is not connected, so no backups can be made or restored ({chosen['path']} is missing). "
        "Connect the drive, or choose another place under Settings > Security & backups."
    )


def drive(paths: RuntimePaths | None = None) -> dict[str, str] | None:
    """The other drive the backups are on (its mount point and name); None while they are on the main drive."""
    paths = paths or RuntimePaths()
    chosen = _pointer(paths)
    if not chosen or problem(paths):
        return None
    mount = Path(chosen["path"])
    while not os.path.ismount(mount) and mount != mount.parent:
        mount = mount.parent
    if _device(mount) == _device(paths.root):
        return None
    return {"mount": str(mount), "name": chosen["name"] or mount.name or str(mount)}


def _write_pointer(target: Path, name: str, marker: str, paths: RuntimePaths) -> None:
    body = {"path": str(target), "id": marker, "name": name, "changed_at": datetime.now(UTC).isoformat()}
    write_atomic(paths.state / POINTER, json.dumps(body, indent=2).encode())


def _clear_pointer(paths: RuntimePaths) -> None:
    (paths.state / POINTER).unlink(missing_ok=True)


# ---------------------------------------------------------------- the drives to choose from


def _block_devices(run) -> list[dict[str, Any]]:
    """Every mounted filesystem, with the physical drive it is on."""
    columns = "NAME,TYPE,FSTYPE,LABEL,MOUNTPOINTS,SIZE,FSAVAIL,MODEL,MAJ:MIN"
    try:
        result = run(["lsblk", "--json", "--bytes", "-o", columns], timeout=15)
        tree = json.loads(result.stdout or "{}").get("blockdevices") or []
    except (OSError, ValueError) as exc:
        raise MoveError(f"The drives could not be listed: {exc}") from exc
    found: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], disk: dict[str, Any]) -> None:
        for mount in node.get("mountpoints") or []:
            if mount and mount.startswith("/"):
                found.append({**node, "mount": mount, "disk": disk.get("name"), "disk_model": disk.get("model")})
        for child in node.get("children") or []:
            walk(child, disk)

    for disk in tree:
        walk(disk, disk)
    return found


def _device(path: Path) -> int:
    while not path.exists():
        path = path.parent
    return path.stat().st_dev


def _majmin(device: int) -> str:
    return f"{os.major(device)}:{os.minor(device)}"


def _owner() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def choices(paths: RuntimePaths | None = None, run=process.completed) -> list[dict[str, Any]]:
    """The places backups can live: the main drive first, then each other mounted drive.

    Each says why it can't be chosen (``problem``), whether it is the drive
    the apps' data is on, and whether it is mounted only while someone is
    signed in to the desktop (scheduled backups at night would miss it).
    """
    paths = paths or RuntimePaths()
    current = folder(paths)
    devices = _block_devices(run)
    data_device = _majmin(_device(paths.data))
    data_disk = next((item["disk"] for item in devices if item.get("maj:min") == data_device), None)
    usage = shutil.disk_usage(paths.root if paths.root.exists() else "/")
    main = next((item for item in devices if item.get("maj:min") == data_device), {})
    result = [
        {
            "id": DEFAULT_ID,
            "name": "This computer's main drive",
            "detail": str(main.get("disk_model") or "").strip(),
            "mount": str(main.get("mount") or "/"),
            "path": str(paths.backups),
            "size_bytes": usage.total,
            "free_bytes": usage.free,
            "current": current == paths.backups,
            "same_drive_as_data": True,
            "desktop_only": False,
            "problem": "",
        }
    ]
    seen = {data_device}
    for item in sorted(devices, key=lambda entry: entry["mount"]):
        mount = item["mount"]
        if item.get("maj:min") in seen or mount.startswith(("/boot", "/snap", "/var/snap")):
            continue
        seen.add(str(item.get("maj:min")))
        fstype = str(item.get("fstype") or "")
        target = Path(mount) / FOLDER
        reason = ""
        if fstype not in FILESYSTEMS:
            reason = (
                f"Its format ({fstype or 'unknown'}) can't keep Linux file permissions, so the encrypted backups "
                "would not stay private. Use a drive formatted as ext4."
            )
        elif not os.access(target if target.exists() else mount, os.W_OK):
            reason = f"Mu3Lab can't write there. To allow it, run in a terminal: sudo chown {_owner()} {mount}"
        try:
            free = shutil.disk_usage(mount).free
        except OSError:
            free = int(item.get("fsavail") or 0)
        result.append(
            {
                "id": mount,
                "name": str(item.get("label") or Path(mount).name or mount),
                "detail": str(item.get("disk_model") or "").strip(),
                "mount": mount,
                "path": str(target),
                "size_bytes": int(item.get("size") or 0),
                "free_bytes": free,
                "current": current == target,
                "same_drive_as_data": item.get("disk") == data_disk,
                "desktop_only": mount.startswith(("/media/", "/run/media/")),
                "problem": reason,
            }
        )
    return result


# ---------------------------------------------------------------- root file work, in the pinned Kopia image


def _root_shell(script: str, mounts: list[tuple[Path, str, bool]], log: Log, *, timeout: int = 600) -> str:
    """A short shell script as root in the pinned Kopia image, networkless, seeing only ``mounts``."""
    from ctl.backups import KOPIA_IMAGE

    command = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh"]
    for source, target, read_only in mounts:
        command.extend(["-v", f"{source}:{target}{':ro' if read_only else ''}"])
    command.extend([KOPIA_IMAGE, "-c", script])
    rc, output = actions.docker_cmd(command, log, timeout=timeout)
    if rc:
        raise MoveError(f"A file operation on the backups failed: {output.strip()[-400:]}")
    return output


def used_bytes(paths: RuntimePaths | None = None, *, fresh: bool = False, log: Log = lambda _line: None) -> int:
    """How much space the backups take; measured as root at most once an hour unless ``fresh``."""
    paths = paths or RuntimePaths()
    cached = records.get(*SIZE, paths)
    where = str(folder(paths))
    if not fresh and cached.get("path") == where and time.time() - float(cached.get("at") or 0) < SIZE_MAX_AGE:
        return int(cached.get("bytes") or 0)
    repository = folder(paths) / "kopia"
    if problem(paths) or not repository.is_dir():
        return 0
    output = _root_shell("du -sb /repo | cut -f1", [(repository, "/repo", True)], lambda _line: None)
    measured = int(output.strip().splitlines()[-1]) if output.strip() else 0
    records.put(*SIZE, {"path": where, "bytes": measured, "at": time.time()}, paths)
    log(f"The backups take {measured / 1e9:.1f} GB.")
    return measured


# ---------------------------------------------------------------- moving (worker)


def _running_service(paths: RuntimePaths, log: Log) -> Service | None:
    """The backup service, when it is installed and running."""
    from ctl import backup_service
    from ctl.registry import RegistryError, load

    if not backup_service.installed(paths) or not backup_service._container(log):
        return None
    try:
        return load().get(backup_service.service_id())
    except (RegistryError, ValueError, KeyError):
        return None


def _render(root: Path, paths: RuntimePaths, log: Log) -> None:
    """Write the backup folder in use into the backup service's settings, so its next start mounts it."""
    from ctl import backup_service
    from ctl.engine.runtime import render_service
    from ctl.registry import RegistryError, load

    if not backup_service.installed(paths):
        return
    try:
        render_service(load().get(backup_service.service_id()), root, copy_files=False)
    except (RegistryError, ValueError, KeyError, OSError) as exc:
        log(f"The backup service's settings could not be rewritten: {exc}")


def _backups_busy(paths: RuntimePaths) -> bool:
    from ctl import backup_service

    if records.names(backup_service.HOLDS, paths):
        return True
    try:
        status = backup_service.source_status(paths)
    except OSError:
        return False  # the server is not answering: nothing is running in it
    return any(item.get("status") not in {"IDLE", "PAUSED"} for item in status.values())


def move(target_id: str, root: Path, log: Log, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Move the backups to the chosen place; returns where they went and how much was copied."""
    from ctl import backups, resource_locks
    from ctl.lifecycle import app_control

    paths = paths or RuntimePaths()
    if problem(paths):
        raise MoveError(problem(paths) + " Move them only once the drive is back.")
    chosen = next((item for item in choices(paths) if item["id"] == target_id), None)
    if chosen is None:
        raise MoveError("That drive is no longer connected.")
    if chosen["current"]:
        raise MoveError(f"The backups are already on {chosen['name']}.")
    if chosen["problem"]:
        raise MoveError(chosen["problem"])
    old, new = folder(paths), Path(chosen["path"])
    pointer = paths.state / POINTER
    previous = pointer.read_bytes() if pointer.is_file() else None
    if _backups_busy(paths):
        raise MoveError("A backup is running right now. Try again once it has finished.")

    with resource_locks.hold(backups.REPOSITORY_LOCK, paths=paths):
        size = used_bytes(paths, fresh=True, log=log)
        needed = int(size * 1.05) + HEADROOM
        if chosen["free_bytes"] < needed:
            raise MoveError(
                f"{chosen['name']} has {chosen['free_bytes'] / 1e9:.0f} GB free; the backups need about "
                f"{needed / 1e9:.0f} GB there."
            )
        new.mkdir(mode=0o750, exist_ok=True)
        _root_shell(
            "rm -rf /to/.kopia-copy /to/.restores-copy; "
            "if [ -e /to/kopia ] && [ -n \"$(ls -A /to/kopia)\" ]; then echo 'already holds backups' >&2; exit 3; fi",
            [(new, "/to", False)],
            log,
        )

        service = _running_service(paths, log)
        if service:
            log("Stopping the backup service while the backups are copied.")
            try:
                app_control.stop(service, root, log)
            except app_control.StopFailed as exc:
                raise MoveError(str(exc)) from exc

        switched = False
        try:
            log(f"Copying {size / 1e9:.1f} GB of backups to {new}. This can take a while.")
            mounts = [(old, "/from", True), (new, "/to", False)]
            _root_shell(
                "set -e; "
                "if [ -d /from/kopia ]; then cp -a /from/kopia /to/.kopia-copy; else mkdir /to/.kopia-copy; fi; "
                "if [ -d /from/restores ]; then cp -a /from/restores /to/.restores-copy; "
                "else mkdir /to/.restores-copy; fi; sync",
                mounts,
                log,
                timeout=COPY_TIMEOUT,
            )
            log("Comparing every copied file with the original.")
            _root_shell(
                "set -e; "
                # Every file's name and size; with no repository yet, both sides are the empty copy.
                "list() { cd \"$1\" && find . -type f -printf '%P %s\\n' | sort | sha256sum; }; "
                "src=/from/kopia; [ -d $src ] || src=/to/.kopia-copy; "
                "a=$(list $src); b=$(list /to/.kopia-copy); "
                '[ "$a" = "$b" ] || { echo \'the copy does not match the original\' >&2; exit 4; }; '
                "rm -rf /to/kopia /to/restores; mv /to/.kopia-copy /to/kopia; mv /to/.restores-copy /to/restores",
                mounts,
                log,
                timeout=COPY_TIMEOUT,
            )
            if chosen["id"] == DEFAULT_ID:
                _clear_pointer(paths)
            else:
                marker = secrets.token_hex(16)
                write_atomic(new / MARKER, f"{marker}\n".encode())
                _write_pointer(new, chosen["name"], marker, paths)
            switched = True
            _render(root, paths, log)
            if backups.repository_present(paths):
                log("Checking every backup in the new place.")
                backups.check(log, paths=paths)
        except (MoveError, backups.BackupError, OSError) as exc:
            if switched:
                log("Going back to the backups in their old place.")
                if previous is None:
                    _clear_pointer(paths)
                else:
                    write_atomic(pointer, previous)
                _render(root, paths, log)
            try:
                tidy = "rm -rf /to/.kopia-copy /to/.restores-copy" + (
                    "; rm -rf /to/kopia /to/restores" if switched else ""
                )
                _root_shell(tidy, [(new, "/to", False)], log)
                (new / MARKER).unlink(missing_ok=True)
            except (MoveError, OSError) as tidy_failed:
                log(f"The partial copy in {new} could not be removed: {tidy_failed}")
            if service:
                app_control.start(service, root, log)
            raise MoveError(str(exc)) from exc

        if service:
            log("Starting the backup service on the new drive.")
            healthy, why = app_control.start(service, root, log)
            if not healthy:
                log(f"The backup service did not start yet: {why}")

        log(f"Removing the old copy from {old}.")
        try:
            _root_shell(
                "rm -rf /from/kopia /from/restores /from/.mu3lab-backups"
                + (
                    f"; mkdir -m 750 /from/restores; chown {os.getuid()}:{os.getgid()} /from/restores"
                    if old == paths.backups
                    else ""
                ),
                [(old, "/from", False)],
                log,
                timeout=COPY_TIMEOUT,
            )
            if old != paths.backups:
                old.rmdir()
        except (MoveError, OSError) as exc:
            # The move itself is done; the old copy only takes space now.
            log(f"The old copy in {old} could not be removed completely, so delete it yourself: {exc}")
        records.put(*SIZE, {"path": str(new), "bytes": size, "at": time.time()}, paths)
    return {"name": chosen["name"], "path": str(new), "bytes": size}


def execute_claimed(store, job: dict, worker_id: str, root: Path) -> None:
    """The "Move backups" job."""
    from ctl.jobs import job_params, redact

    job_id = str(job["id"])
    actor = str(job.get("actor") or worker_id)
    target = str(job_params(job).get("target") or "")
    store.transition(job_id, "running", actor=actor, detail="Moving the backups.", step_id="move")
    try:
        done = move(target, root, lambda line: store.append_event(job_id, "log", line))
    except (MoveError, OSError) as exc:
        store.transition(job_id, "failed", actor=actor, detail=redact(str(exc)), error_code="backup_move_failed")
        return
    from ctl import resource_usage

    resource_usage.disk_stale.set()  # the charts show the backups on their new drive soon
    store.transition(
        job_id,
        "succeeded",
        actor=actor,
        detail=f"Backups moved to {done['name']} ({done['path']}): {done['bytes'] / 1e9:.1f} GB copied and checked.",
        step_id="complete",
    )
