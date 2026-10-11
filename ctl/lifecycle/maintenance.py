"""Worker jobs that restore, update, or put back one installed app, and the backup service's stops and starts.

- ``restore`` and ``update``: see ``ctl.lifecycle.restore`` and ``ctl.lifecycle.update``.
  Each either finishes, or puts the app back as it was and says so.
- ``recover``: after a restore or update was interrupted, put the app back as
  it was ("Put my original data back", "Undo update").
- ``keep_update``: after an interrupted update whose new release was already
  in place, keep it if it starts healthy.
- ``backup_stop`` and ``backup_start``: Kopia's backup service asks for an
  app to be stopped just before its backup and started again just after
  (``ctl.backup_service``); they run here so only the worker runs Docker.

A job is never continued after its worker died: a restore or update job
claimed a second time fails as interrupted, and its note
(``ctl.lifecycle.in_progress``) leaves the app waiting for the owner.
"""

from __future__ import annotations

from pathlib import Path

from ctl import backup_service, job_guard
from ctl.control_state import ControlState
from ctl.jobs import JobStore, job_params, redact
from ctl.lifecycle import app_control, in_progress, restore, update
from ctl.registry import Service

MAINTENANCE_ACTIONS = frozenset({"restore", "update", "recover", "keep_update"})
BACKUP_ACTIONS = frozenset({"backup_stop", "backup_start"})


class _Failed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _backup_hold(service: Service, root: Path, action: str, log) -> str:
    if action == "backup_stop":
        try:
            app_control.stop(service, root, log)
        except app_control.StopFailed as exc:
            raise _Failed("stop_failed", str(exc)) from exc
        return f"{service.name} is stopped for its backup."
    started, detail = app_control.start(service, root, log)
    if not started:
        raise _Failed("health_check_failed", f"{service.name} did not start again after its backup: {detail[-300:]}")
    return f"{service.name} is running again after its backup."


def _recover(service: Service, root: Path, action: str, active: set[str], log) -> str:
    note = in_progress.interrupted(service.id, active)
    if not note:
        leftovers = restore.leftovers(service, root)
        if not leftovers:
            raise _Failed("nothing_to_recover", f"{service.name} has nothing left to put back.")
        # Folders kept aside with no note, for example by an older Mu3Lab.
        note = {"kind": "restore", "folders": leftovers, "was_running": True}
    try:
        if note.get("kind") == "update":
            return (update.keep if action == "keep_update" else update.undo)(service, root, log, note)
        if action == "keep_update":
            raise _Failed("nothing_to_keep", "Only an interrupted update can be kept.")
        return restore.put_back(service, root, log, note)
    except restore.PutBackFailed as exc:
        raise _Failed("put_back_failed", f"{service.name} still needs attention: {exc}") from exc


def execute(store: JobStore, state: ControlState | None, job: dict, service: Service, root: Path) -> None:
    job_id, action = str(job["id"]), str(job["action"])
    actor = str(job.get("actor") or "worker")

    def log(line: str) -> None:
        store.append_event(job_id, "log", line)

    def step(stage: str, detail: str) -> None:
        store.append_event(job_id, "stage", f"{stage}: {detail}")

    reclaimed = store.claims(job_id) > 1
    store.transition(
        job_id, "running", actor=actor, detail=f"{action.title()} started for {service.name}.", step_id=action
    )
    running = app_control.was_running(state, service)
    try:
        if action in BACKUP_ACTIONS:
            detail = _backup_hold(service, root, action, log)
        elif reclaimed:
            # Never carry on after a crash: the owner decides (see the module docstring).
            raise _Failed(
                "interrupted",
                f"This {action} was interrupted when Mu3Lab's worker stopped. Nothing continues by itself; "
                f"{service.name}'s Backups card offers to put it back as it was.",
            )
        elif backup_service.holding(service.id):
            raise _Failed("backup_running", f"Nothing changed: {service.name} is being backed up; try again shortly.")
        elif service.stage != "optional" or not (app_control.project(service, root) / "docker-compose.yml").is_file():
            raise _Failed("unsupported_action", f"{service.name} is not an installed catalog app.")
        elif action in {"recover", "keep_update"}:
            active = {str(item["id"]) for item in store.active_jobs() if str(item["id"]) != job_id}
            with job_guard.uncancellable():
                detail = _recover(service, root, action, active, log)
        elif action == "restore":
            detail = restore.restore(
                service, root, job_params(job).get("snapshot_id", ""), log, step, job_id=job_id, running=running
            )
        else:
            detail = update.update(
                service, root, job_params(job).get("target_version", ""), log, step, job_id=job_id, running=running
            )
    except (_Failed, restore.RestoreFailed, update.UpdateFailed) as failure:
        message = failure.message if isinstance(failure, _Failed) else str(failure)
        store.transition(job_id, "failed", actor=actor, detail=redact(message), error_code=failure.code, step_id=action)
        return
    if state and action not in BACKUP_ACTIONS:
        state.set_installation(service.id, "running" if running else "stopped", job_id=job_id, route_state="ready")
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
