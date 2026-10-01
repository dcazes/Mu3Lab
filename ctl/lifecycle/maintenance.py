"""Back up, restore and update one installed catalog app.

All three stop the app so its databases are copied consistently, and all
three end with the app back in the state it was in (running or stopped)
unless something failed. The step that swaps data or release, and the one
that puts it back, run to the end even if someone cancels the job.

An update always takes a fresh, integrity-checked backup first. If the new
release does not come up healthy, the backup and the previous release are put
back automatically, so a failed update costs a few minutes, not the app.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ctl import actions, backups, job_guard
from ctl.control_state import ControlState
from ctl.jobs import JobStore, job_params, redact
from ctl.lifecycle import image_updates
from ctl.lifecycle.health import wait_healthy
from ctl.lifecycle.uninstall import data_directories
from ctl.registry import Service

MAINTENANCE_ACTIONS = frozenset({"backup", "restore", "update"})
# Releases can migrate large databases on first start.
START_TIMEOUT = 900

Log = Callable[[str], None]


class _Failed(Exception):
    def __init__(self, stage: str, code: str, message: str) -> None:
        super().__init__(message)
        self.stage, self.code, self.message = stage, code, message


def _stop(service: Service, project: Path, root: Path, log: Log) -> None:
    from ctl.compute import compose_overrides
    from ctl.mcp_ops import sync_application

    if not sync_application(service.id, running=False, root=root, log=log):
        raise _Failed("stop_app", "mcp_stop_failed", f"A chat connector for {service.name} could not stop safely.")
    rc, output = actions.compose_action(project, "stop", log, extra_files=compose_overrides(service.id, project))
    if rc:
        raise _Failed("stop_app", "compose_failed", f"{service.name} could not be stopped: {output[-300:]}")


def _start(service: Service, project: Path, root: Path, log: Log) -> tuple[bool, str]:
    from ctl.compute import compose_overrides
    from ctl.mcp_ops import sync_application

    rc, output = actions.compose_up(
        project,
        log,
        timeout=START_TIMEOUT + 300,
        wait_timeout=START_TIMEOUT,
        extra_files=compose_overrides(service.id, project),
    )
    if rc:
        return False, output[-500:]
    healthy, detail = wait_healthy(service, timeout=START_TIMEOUT)
    if not healthy:
        return False, detail
    if not sync_application(service.id, running=True, root=root, log=log):
        log("One enabled chat connector needs attention after the app started.")
    return True, ""


def _was_running(state: ControlState | None, service: Service) -> bool:
    installation = state.installation(service.id) if state else None
    return not installation or installation["state"] != "stopped"


def _short(snapshot_id: str) -> str:
    return snapshot_id[:8]


def _backup(service: Service, project: Path, root: Path, step: Callable[[str, str], None], log: Log, running: bool):
    step("backup", f"Saving a backup of {service.name}.")
    with job_guard.uncancellable():
        if running:
            _stop(service, project, root, log)
        saved, error = None, ""
        try:
            saved = backups.snapshot(
                service.id,
                data_directories(service, root),
                "manual",
                log,
                version=image_updates.installed_version(service),
            )
        except (backups.BackupError, OSError) as exc:
            error = str(exc)
        if running:
            step("start_app", f"Starting {service.name} again.")
            started, detail = _start(service, project, root, log)
            if not started:
                raise _Failed("start_app", "health_check_failed", f"{service.name} did not start again: {detail}")
    if not saved:
        raise _Failed("backup", "backup_failed", error)
    return f"Backup {_short(saved['snapshot_id'])} saved and checked ({saved['files']} files)."


def _release_for(service: Service, version: str, root: Path, log: Log) -> dict | None:
    """Pinned images that run ``version``, or None for Mu3Lab's own checked-in release."""
    if not image_updates.is_newer(version, str(service.update.get("current_version", ""))):
        return None
    images = image_updates.plan(service, root, version)
    if not images:
        raise _Failed("download_images", "update_unsupported", f"Mu3Lab cannot tell which images make up {version}.")
    try:
        return {"version": version, "images": image_updates.download(images, log)}
    except RuntimeError as exc:
        raise _Failed("download_images", "image_pull_failed", f"Nothing changed: {exc}") from exc


def _restore(
    service: Service,
    project: Path,
    root: Path,
    step: Callable[[str, str], None],
    log: Log,
    running: bool,
    snapshot_id: str,
):
    directories = data_directories(service, root)
    try:
        chosen = next((snap for snap in backups.snapshots(service.id, log) if snap["id"] == snapshot_id), None)
    except backups.BackupError as exc:
        raise _Failed("restore", "restore_failed", str(exc)) from exc
    if not chosen:
        raise _Failed("restore", "restore_failed", "That backup no longer exists.")
    installed = image_updates.installed_version(service)
    previous = image_updates.read(service.id)
    # Data from an older release goes back with that release: restoring the
    # backup taken before an update is how an update is undone.
    downgrade = bool(chosen["version"]) and image_updates.is_newer(installed, chosen["version"])
    release = None
    if downgrade:
        step("download_images", f"Downloading {service.name} {chosen['version']}, the release this backup is from.")
        release = _release_for(service, chosen["version"], root, log)
    with job_guard.uncancellable():
        step("stop_app", f"Stopping {service.name}.")
        _stop(service, project, root, log)
        step("backup", "Saving the current data first, so this restore can be undone.")
        try:
            safety = backups.snapshot(service.id, directories, "pre-restore", log, version=installed)
        except (backups.BackupError, OSError) as exc:
            if running:
                _start(service, project, root, log)
            raise _Failed("backup", "backup_failed", f"Nothing was restored: {exc}") from exc
        step("restore", f"Putting back backup {_short(snapshot_id)}.")
        try:
            backups.restore(service.id, snapshot_id, directories, log)
            if downgrade:
                image_updates.restore_previous(service.id, release)
        except (backups.BackupError, OSError) as exc:
            step("rollback", "Restore failed; putting the current data back.")
            try:
                backups.restore(service.id, safety["snapshot_id"], directories, log)
                image_updates.restore_previous(service.id, previous)
            except (backups.BackupError, OSError) as undo:
                raise _Failed(
                    "rollback",
                    "restore_rollback_failed",
                    f"The restore failed ({exc}) and so did undoing it ({undo}). "
                    f"Backup {_short(safety['snapshot_id'])} holds the data from just before; restore it to recover.",
                ) from undo
            if running:
                _start(service, project, root, log)
            raise _Failed("restore", "restore_failed", f"Nothing changed: {exc}") from exc
        if running:
            step("start_app", f"Starting {service.name}.")
            started, detail = _start(service, project, root, log)
            if not started:
                raise _Failed(
                    "start_app",
                    "health_check_failed",
                    f"The backup was restored but {service.name} did not start: {detail}. "
                    f"Backup {_short(safety['snapshot_id'])} holds the data from before the restore.",
                )
    when = chosen["time"][:16].replace("T", " ")
    moved = f" {service.name} is back on {chosen['version']}." if downgrade else ""
    return (
        f"Restored the backup from {when} UTC.{moved} "
        f"The data from just before is kept as backup {_short(safety['snapshot_id'])}."
    )


def _update(
    service: Service,
    project: Path,
    root: Path,
    step: Callable[[str, str], None],
    log: Log,
    running: bool,
    target: str,
):
    current = image_updates.installed_version(service)
    if not image_updates.is_newer(target, current):
        raise _Failed("check_release", "already_current", f"{service.name} is already on {current}.")
    images = image_updates.plan(service, root, target)
    if not images:
        raise _Failed("check_release", "update_unsupported", f"Mu3Lab cannot tell which images make up {target}.")
    step("download_images", f"Downloading {service.name} {target}. The app keeps running meanwhile.")
    try:
        pinned = image_updates.download(images, log)
    except RuntimeError as exc:
        raise _Failed("download_images", "image_pull_failed", f"Nothing changed: {exc}") from exc
    previous = image_updates.read(service.id)
    directories = data_directories(service, root)
    with job_guard.uncancellable():
        step("stop_app", f"Stopping {service.name}.")
        _stop(service, project, root, log)
        step("backup", f"Saving a backup of {service.name} {current}.")
        try:
            saved = backups.snapshot(service.id, directories, "pre-update", log, version=current)
        except (backups.BackupError, OSError) as exc:
            if running:
                _start(service, project, root, log)
            raise _Failed("backup", "backup_failed", f"Nothing was updated: {exc}") from exc
        step("apply_update", f"Starting {service.name} {target}. Upgrading its data can take a few minutes.")
        image_updates.write(service.id, target, pinned)
        started, detail = _start(service, project, root, log)
        if started:
            if not running:
                _stop(service, project, root, log)
            return (
                f"Updated {service.name} from {current} to {target}. "
                f"Backup {_short(saved['snapshot_id'])} holds the data from before."
            )
        step("rollback", f"{target} did not start; putting back {current} and its data.")
        try:
            _stop(service, project, root, log)
            backups.restore(service.id, saved["snapshot_id"], directories, log)
            image_updates.restore_previous(service.id, previous)
        except (_Failed, backups.BackupError, OSError) as exc:
            raise _Failed(
                "rollback",
                "update_rollback_failed",
                f"{target} did not start ({redact(detail)}) and putting back {current} failed ({exc}). "
                f"Backup {_short(saved['snapshot_id'])} holds the data from before the update.",
            ) from exc
        restarted, again = _start(service, project, root, log) if running else (True, "")
        if not restarted:
            raise _Failed(
                "rollback",
                "update_rollback_failed",
                f"{target} did not start, and {current} did not start again after its data was put back: {again}",
            )
        raise _Failed(
            "apply_update",
            "update_rolled_back",
            f"{service.name} {target} did not start ({redact(detail)[-300:]}). "
            f"Mu3Lab put back {current} with its data from just before; nothing was lost.",
        )


def execute(store: JobStore, state: ControlState | None, job: dict, service: Service, root: Path) -> None:
    from ctl.service_ops import project_path

    job_id, action = str(job["id"]), str(job["action"])
    actor = str(job.get("actor") or "worker")
    params = job_params(job)
    project = project_path(service, root)

    def log(line: str) -> None:
        store.append_event(job_id, "log", line)

    def step(stage: str, detail: str) -> None:
        store.append_event(job_id, "stage", f"{stage}: {detail}")

    store.transition(
        job_id, "running", actor=actor, detail=f"{action.title()} started for {service.name}.", step_id=action
    )
    running = _was_running(state, service)
    try:
        if service.stage != "optional" or not (project / "docker-compose.yml").is_file():
            raise _Failed("validate_service", "unsupported_action", f"{service.name} is not an installed catalog app.")
        if action == "backup":
            detail = _backup(service, project, root, step, log, running)
        elif action == "restore":
            detail = _restore(service, project, root, step, log, running, params.get("snapshot_id", ""))
        else:
            detail = _update(service, project, root, step, log, running, params.get("target_version", ""))
    except _Failed as failure:
        store.transition(
            job_id,
            "failed",
            actor=actor,
            detail=redact(failure.message),
            error_code=failure.code,
            step_id=failure.stage,
        )
        return
    if state:
        state.set_installation(service.id, "running" if running else "stopped", job_id=job_id, route_state="ready")
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
