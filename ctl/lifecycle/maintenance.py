"""Back up, restore and update one installed catalog app, durably.

All three stop the app so its databases are copied consistently, and all
three end with the app back in the state it was in (running or stopped)
unless something failed. The steps that swap data or release, and the ones
that put them back, run to the end even if someone cancels the job.

An update always takes a fresh, integrity-checked backup first. If the new
release does not come up healthy, the backup and the previous release are put
back automatically, so a failed update costs a few minutes, not the app.

Every step is recorded in the operation journal (``ctl.operations``) before
it runs, with what undoing it needs. A job reclaimed after its worker died
continues from that record:

- before anything changed: run again from the start;
- after the update's new release was applied: start it and check its health
  again; keep it if healthy, otherwise put the previous release and data back;
- after a restore began swapping data: finish the restore;
- while putting things back: put them back again (restoring a backup and
  rewriting a release record are both safe to repeat).

A job that ended without finishing its operation (cancelled while its worker
was down, or stopped by an unexpected error) gets a ``recover`` job, which
brings the app to a safe end instead of carrying on. If putting things back
fails, the operation needs attention: the app is blocked except for retrying
recovery or restoring a backup, and the backup to use is named.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ctl import actions, backups, job_guard
from ctl.control_state import ControlState
from ctl.engine.runtime import render_service
from ctl.jobs import JobConflict, JobStore, job_params, redact
from ctl.lifecycle import app_releases, deployments
from ctl.lifecycle.app_releases import Release
from ctl.lifecycle.health import wait_healthy
from ctl.lifecycle.uninstall import data_directories
from ctl.operations import Operation, OperationStore, State
from ctl.registry import Service
from ctl.runtime import RuntimePaths

JOURNALED_ACTIONS = frozenset({"backup", "restore", "update"})
MAINTENANCE_ACTIONS = JOURNALED_ACTIONS | {"recover"}
# Releases can migrate large databases on first start.
START_TIMEOUT = 900
# Phases in which neither the app's data nor its release has changed yet (staged
# files are undone from the deployment bundle; staged data is only deleted).
UNCHANGED = frozenset({"prepare", "stage_definition", "stage_restore", "stop_app", "backup", "backed_up"})
RECOVERY_ACTOR = "worker"
# Attempts (first run, reclaims and recoveries) before an operation needs a person.
MAX_ATTEMPTS = 5

Log = Callable[[str], None]


class _Failed(Exception):
    def __init__(self, stage: str, code: str, message: str, outcome: State = "failed") -> None:
        super().__init__(message)
        self.stage, self.code, self.message, self.outcome = stage, code, message, outcome


@dataclass
class _Run:
    """One worker attempt at one journaled operation."""

    service: Service
    project: Path
    root: Path
    journal: OperationStore
    op: Operation
    step: Callable[[str, str], None]
    log: Log

    @property
    def running(self) -> bool:
        return self.op.was_running

    def mark(self, phase: str, detail: str, **fields) -> None:
        """Journal the step, and what undoing it needs, before taking it."""
        self.op = self.journal.advance(self.op.id, phase, **fields)
        self.step(phase, detail)

    def directories(self) -> list[Path]:
        return data_directories(self.service, self.root)


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


def _tag(run: _Run, purpose: str) -> str:
    return f"{run.op.id[:12]}-{purpose[:1]}"


def _directories(run: _Run, storage: list[str] | None = None) -> list[Path]:
    """The data folders a deployment uses: its bundle's record, else the installed project's."""
    if storage is not None:
        return [RuntimePaths().data / name for name in storage]
    return run.directories()


def _capture(run: _Run) -> None:
    """Keep a verified copy of the whole current deployment before anything changes (R12)."""
    if run.op.bundle:
        return
    previous = run.op.previous_release
    bundle = deployments.capture(
        run.service,
        f"{run.op.id[:12]}-{previous.id if previous else 'none'}",
        release_id=previous.id if previous else "",
        storage=[path.name for path in run.directories()],
    )
    run.op = run.journal.advance(run.op.id, run.op.phase, bundle=bundle)


def _put_deployment_back(run: _Run) -> None:
    """The Compose files, overrides, rendered files, settings and release record from before."""
    if run.op.bundle:
        deployments.restore(run.service, run.op.bundle)
    else:
        app_releases.restore_previous(run.service.id, run.op.previous_release)


def _plan(run: _Run, purpose: str, snapshot_id: str, storage: list[str] | None = None) -> dict:
    plan = run.op.restore_plans.get(purpose)
    if plan:
        return plan
    plan = backups.plan_restore(
        run.service.id, snapshot_id, _directories(run, storage), run.log, tag=_tag(run, purpose)
    )
    run.op = run.journal.save_plan(run.op.id, purpose, plan)
    return plan


def _saver(run: _Run, purpose: str) -> Callable[[dict], None]:
    def save(plan: dict) -> None:
        run.op = run.journal.save_plan(run.op.id, purpose, plan)

    return save


def _tidy(run: _Run) -> None:
    """Delete the folders kept aside and any staged copies; only after the app is verified."""
    for purpose, plan in list(run.op.restore_plans.items()):
        try:
            backups.wipe(backups.leftovers(plan), run.log)
        except (backups.BackupError, OSError) as exc:
            run.log(f"Restore leftovers were not removed this time: {exc}")
            continue
        run.op = run.journal.save_plan(run.op.id, purpose, None)


def _abandon(run: _Run) -> None:
    """Undo whatever a step that failed before any data changed had done: staged files, a stop."""
    plan = run.op.restore_plans.get("restore")
    if plan and not any(state == "swapped" for state in plan["roots"].values()):
        _tidy(run)
    if run.op.bundle and run.op.kind == "update":
        _put_deployment_back(run)
    if run.running and run.op.phase in {"stop_app", "backup", "backed_up"}:
        _start(run.service, run.project, run.root, run.log)


def _housekeeping(run: _Run) -> None:
    """Check the repository and prune old backups, after the app is back up."""
    run.mark("housekeeping", "Checking the backup and tidying up old ones.")
    _tidy(run)
    try:
        backups.check(run.log)
    except (backups.BackupError, OSError) as exc:
        run.log(f"The backup repository could not be checked this time: {exc}")
    others = run.journal.protected(run.service.id, excluding=run.op.id)
    if others:
        run.log("Old backups are kept for now: another operation on this app may still need them.")
    else:
        try:
            backups.prune(run.service.id, run.log)
        except (backups.BackupError, OSError) as exc:
            run.log(f"Old backups were not pruned this time: {exc}")
    try:
        deployments.prune(run.service.id, run.journal.bundles(run.service.id))
    except OSError as exc:
        run.log(f"Old deployment copies were not removed this time: {exc}")


def _put_back_data(run: _Run) -> None:
    op = run.op
    restored = op.restore_plans.get("restore")
    if op.kind == "restore" and restored:
        # The data from before is still beside the restored copy: swap it back.
        try:
            backups.undo(restored, _saver(run, "restore"))
            return
        except (backups.BackupError, OSError) as exc:
            run.log(f"The folders from before could not be swapped back ({exc}); restoring the backup instead.")
    if op.kind == "update" and op.recovery_snapshot:
        # The new release may have migrated the data in place: put the backup back.
        storage = deployments.manifest(run.service.id, op.bundle)["storage"] if op.bundle else None
        plan = _plan(run, "rollback", op.recovery_snapshot, storage)
        plan = backups.stage(run.service.id, plan, run.log, _saver(run, "rollback"))
        backups.swap(plan, _saver(run, "rollback"))
    elif op.kind == "restore" and op.recovery_snapshot and restored:
        plan = _plan(run, "rollback", op.recovery_snapshot)
        plan = backups.stage(run.service.id, plan, run.log, _saver(run, "rollback"))
        backups.swap(plan, _saver(run, "rollback"))


def _put_back(run: _Run, code: str, cause: str) -> None:
    """Return the app to its data, deployment and release from just before the operation."""
    op = run.op
    before = op.previous_release.version if op.previous_release else "the previous release"
    evidence = (
        f" Backup {_short(op.recovery_snapshot)} holds the data from before; restore it to recover."
        if op.recovery_snapshot
        else ""
    )
    try:
        _stop(run.service, run.project, run.root, run.log)
        _put_back_data(run)
        _put_deployment_back(run)
    except (_Failed, backups.BackupError, deployments.BundleError, OSError) as exc:
        raise _Failed(
            "rollback", code, f"{cause}, and putting back {before} failed ({exc}).{evidence}", "needs_attention"
        ) from exc
    if run.running:
        restarted, again = _start(run.service, run.project, run.root, run.log)
        if not restarted:
            raise _Failed(
                "rollback",
                code,
                f"{cause}. {before} and its data were put back, but {run.service.name} did not start: {again}."
                f"{evidence}",
                "needs_attention",
            )
    _tidy(run)


def _backup(run: _Run) -> str:
    service = run.service
    saved, error = None, ""
    if run.op.phase == "housekeeping":
        _housekeeping(run)
        return f"Backup of {service.name} saved and checked."
    with job_guard.uncancellable():
        run.mark("backup", f"Saving a backup of {service.name}.")
        _capture(run)
        if run.running:
            _stop(service, run.project, run.root, run.log)
        try:
            # Checked once the app is running again, so the check adds no downtime.
            saved = backups.snapshot(
                service.id,
                run.directories(),
                "manual",
                run.log,
                version=app_releases.installed_version(service, run.root),
                release_id=_installed_id(service, run.root),
                prune_old=False,
                verify=False,
            )
        except (backups.BackupError, OSError) as exc:
            error = str(exc)
        if run.running:
            run.mark("start_app", f"Starting {service.name} again.")
            started, detail = _start(service, run.project, run.root, run.log)
            if not started:
                raise _Failed("start_app", "health_check_failed", f"{service.name} did not start again: {detail}")
        if not saved:
            raise _Failed("backup", "backup_failed", error)
        _housekeeping(run)
    return f"Backup {_short(saved['snapshot_id'])} saved ({saved['files']} files)."


def _refresh_definition(service: Service, root: Path, log: Log) -> None:
    """Copy the approved Compose definition, which may differ between releases. Raises on failure."""
    render_service(service, root)
    app_releases.align(service, root)


def _validate_definition(run: _Run) -> None:
    """The staged files must form a valid Compose project before the app is stopped."""
    from ctl.compute import compose_overrides

    rc, output = actions.compose_config(
        run.project, run.log, extra_files=compose_overrides(run.service.id, run.project)
    )
    if rc:
        raise _Failed(
            "stage_definition", "definition_invalid", f"The new release's settings are not valid: {output[-300:]}"
        )


def _installed_id(service: Service, root: Path) -> str:
    """The installed release's id, kept in history so a backup tagged with it stays restorable."""
    release = app_releases.installed(service, root)
    return app_releases.remember(service.id, release) if release else ""


def _release_for(service: Service, snapshot: dict, root: Path, log: Log, variant: str) -> Release:
    """The exact release a backup was saved from, downloaded. Raises _Failed."""
    version, release_id = str(snapshot.get("version") or ""), str(snapshot.get("release_id") or "")
    target = app_releases.approved(service, root, variant)
    try:
        if release_id:
            release = (
                target if target.id == release_id else app_releases.from_history(service.id, release_id=release_id)
            )
        else:
            release = target if target.version == version else app_releases.from_history(service.id, version)
    except app_releases.AmbiguousRelease:
        raise _Failed(
            "download_images",
            "release_ambiguous",
            f"This server ran more than one deployment of {service.name} {version}, and this older backup does not "
            "say which, so it can't be restored safely.",
        ) from None
    if not release:
        raise _Failed(
            "download_images",
            "release_unknown",
            f"Mu3Lab no longer knows which images make up {service.name} {version}, so it can't restore that backup.",
        )
    try:
        app_releases.download(release.images, log)
    except RuntimeError as exc:
        raise _Failed("download_images", "image_pull_failed", f"Nothing changed: {exc}") from exc
    return release


def _save_before(run: _Run, reason: str, label: str) -> None:
    """Stop the app and save the backup an undo would put back."""
    service = run.service
    _capture(run)
    run.mark("stop_app", f"Stopping {service.name}.")
    _stop(service, run.project, run.root, run.log)
    run.mark("backup", label)
    previous = run.op.previous_release
    try:
        saved = backups.snapshot(
            service.id,
            run.directories(),
            reason,
            run.log,
            version=previous.version if previous else "",
            release_id=app_releases.remember(service.id, previous) if previous else "",
            prune_old=False,
        )
    except (backups.BackupError, OSError) as exc:
        _abandon(run)
        verb = "updated" if reason == "pre-update" else "restored"
        raise _Failed("backup", "backup_failed", f"Nothing was {verb}: {exc}") from exc
    run.mark(
        "backed_up", f"Backup {_short(saved['snapshot_id'])} saved and checked.", recovery_snapshot=saved["snapshot_id"]
    )


def _restore(run: _Run) -> str:
    service = run.service
    snapshot_id = run.op.chosen_snapshot
    previous = run.op.previous_release
    installed = previous.version if previous else ""
    moved = when = ""
    if run.op.phase in {"prepare", "stage_restore"}:
        try:
            chosen = next((snap for snap in backups.snapshots(service.id, run.log) if snap["id"] == snapshot_id), None)
        except backups.BackupError as exc:
            raise _Failed("restore", "restore_failed", str(exc)) from exc
        if not chosen:
            raise _Failed("restore", "restore_failed", "That backup no longer exists.")
        when = chosen["time"][:16].replace("T", " ")
        # Data goes back with the release it was saved from: restoring the backup
        # taken before an update is how an update is undone. A backup naming its
        # release moves to exactly that deployment, even at the same version label.
        release = None
        storage = None
        chosen_id = str(chosen.get("release_id") or "")
        switch = (
            chosen_id != previous.id
            if chosen_id and previous
            else bool(chosen["version"]) and chosen["version"] != installed
        )
        if switch:
            run.step(
                "download_images", f"Downloading {service.name} {chosen['version']}, the release this backup is from."
            )
            release = _release_for(service, chosen, run.root, run.log, previous.variant if previous else "cpu")
            moved = f" {service.name} is back on {chosen['version']}."
            # The deployment that release ran with decides which folders must be in the backup.
            bundle = deployments.latest_for_release(service.id, release.id)
            if bundle:
                storage = list(deployments.manifest(service.id, bundle)["storage"])
        try:
            run.mark("stage_restore", f"Unpacking backup {_short(snapshot_id)} beside the current data.")
            if release:
                run.op = run.journal.advance(run.op.id, run.op.phase, target_release=release)
            plan = _plan(run, "restore", snapshot_id, storage)
            # Staged while the app still runs: nothing live changes until the swap.
            backups.stage(service.id, plan, run.log, _saver(run, "restore"))
        except backups.BackupError as exc:
            _abandon(run)
            raise _Failed("restore", "restore_failed", f"Nothing changed: {exc}") from exc
    with job_guard.uncancellable():
        if run.op.phase in {"stage_restore", "stop_app", "backup"}:
            _save_before(run, "pre-restore", "Saving the current data first, so this restore can be undone.")
        if run.op.phase == "rollback":
            _put_back(run, "restore_rollback_failed", "The restore was interrupted")
            raise _Failed(
                "restore",
                "restore_failed",
                "The restore was interrupted; the data from just before it was put back.",
                "rolled_back",
            )
        if run.op.phase in {"backed_up", "swap"}:
            run.mark("swap", f"Putting backup {_short(snapshot_id)} in place.")
            try:
                backups.swap(run.op.restore_plans["restore"], _saver(run, "restore"))
                _switch_deployment(run)
            except (backups.BackupError, deployments.BundleError, OSError) as exc:
                run.mark("rollback", "The restore failed; putting the current data back.")
                _put_back(run, "restore_rollback_failed", f"The restore failed ({exc})")
                raise _Failed("restore", "restore_failed", f"Nothing changed: {exc}", "rolled_back") from exc
        if run.running and run.op.phase in {"swap", "start_app"}:
            run.mark("start_app", f"Starting {service.name}.")
            started, detail = _start(service, run.project, run.root, run.log)
            if not started:
                # The data from before is still beside it: never leave an app that will not start.
                run.mark("rollback", f"{service.name} did not start on the restored data; putting back what it had.")
                _put_back(
                    run, "restore_rollback_failed", f"{service.name} did not start on the restored data ({detail})"
                )
                raise _Failed(
                    "start_app",
                    "restore_rolled_back",
                    f"{service.name} did not start on the restored backup ({redact(detail)[-300:]}), "
                    "so Mu3Lab put back the data it had before; nothing was lost.",
                    "rolled_back",
                )
        backups.record_restore_tested(service.id)
        _housekeeping(run)
    restored = f"Restored the backup from {when} UTC." if when else f"Restored backup {_short(snapshot_id)}."
    return f"{restored}{moved} The data from just before is kept as backup {_short(run.op.recovery_snapshot)}."


def _switch_deployment(run: _Run) -> None:
    """Move to the deployment the restored data belongs to: its whole bundle when kept, else its images."""
    target = run.op.target_release
    if not target:
        return
    bundle = deployments.latest_for_release(run.service.id, target.id)
    if bundle and bundle != run.op.bundle:
        deployments.restore(run.service, bundle)
        run.log("The settings and files that release ran with were put back too.")
    else:
        run.log("That release's own deployment files were not kept; its images run with the current files.")
    app_releases.write(run.service.id, target)


def _stage_definition(run: _Run) -> None:
    """Render and check the new release's files while the app still runs; undo them on failure."""
    _capture(run)
    run.mark("stage_definition", "Preparing the new release's settings and files.")
    try:
        _refresh_definition(run.service, run.root, run.log)
        _validate_definition(run)
    except (_Failed, OSError, ValueError, RuntimeError) as exc:
        _put_deployment_back(run)
        message = exc.message if isinstance(exc, _Failed) else str(exc)
        raise _Failed("stage_definition", "definition_invalid", f"Nothing changed: {message}") from exc


def _apply(run: _Run) -> tuple[bool, str]:
    """Switch to the recorded target release and start it; safe to repeat."""
    target = run.op.target_release
    assert target is not None
    run.mark(
        "apply_update", f"Starting {run.service.name} {target.version}. Upgrading its data can take a few minutes."
    )
    app_releases.write(run.service.id, target)
    return _start(run.service, run.project, run.root, run.log)


def _update(run: _Run, supporting_only: bool) -> str:
    service = run.service
    target = run.op.target_release
    assert target is not None
    previous = run.op.previous_release
    current = previous.version if previous else "the installed release"
    if run.op.phase == "prepare":
        run.step("download_images", f"Downloading {service.name} {target.version}. The app keeps running meanwhile.")
        try:
            app_releases.download(target.images, run.log)
        except RuntimeError as exc:
            raise _Failed("download_images", "image_pull_failed", f"Nothing changed: {exc}") from exc
    with job_guard.uncancellable():
        if run.op.phase in {"prepare", "stage_definition"}:
            _stage_definition(run)
        if run.op.phase in {"stage_definition", "stop_app", "backup"}:
            _save_before(run, "pre-update", f"Saving a backup of {service.name} {current}.")
        detail = "the worker stopped while the update was being put back"
        if run.op.phase in {"backed_up", "apply_update"}:
            # On a resumed attempt this is the health re-check: a release that
            # comes up healthy is kept; anything else is rolled back.
            started, detail = _apply(run)
            if started:
                if not run.running:
                    _stop(service, run.project, run.root, run.log)
                _housekeeping(run)
                moved = (
                    f"Updated {service.name}'s supporting services"
                    if supporting_only
                    else f"Updated {service.name} from {current} to {target.version}"
                )
                return f"{moved}. Backup {_short(run.op.recovery_snapshot)} holds the data from before."
            run.mark("rollback", f"{target.version} did not start; putting back {current} and its data.")
        if run.op.phase == "housekeeping":
            _housekeeping(run)
            return (
                f"Updated {service.name} to {target.version}. "
                f"Backup {_short(run.op.recovery_snapshot)} holds the data from before."
            )
        _put_back(run, "update_rollback_failed", f"{target.version} did not start ({redact(detail)[-300:]})")
    raise _Failed(
        "apply_update",
        "update_rolled_back",
        f"{service.name} {target.version} did not start ({redact(detail)[-300:]}). "
        f"Mu3Lab put back {current} with its data and settings from just before; nothing was lost.",
        "rolled_back",
    )


def _recover(run: _Run) -> tuple[State, str]:
    """Bring an operation whose job ended early, or whose undo failed, to a safe end."""
    op, service = run.op, run.service
    if op.state == "needs_attention" or op.phase == "rollback":
        run.mark("rollback", f"Putting back {service.name}'s data and release from before.")
        code = "update_rollback_failed" if op.kind == "update" else "restore_rollback_failed"
        _put_back(run, code, f"The earlier {op.kind} could not be undone")
        state: State = "resolved" if op.state == "needs_attention" else "rolled_back"
        return state, f"{service.name} is back to how it was before the {op.kind}."
    if op.phase == "prepare":
        return "cancelled", f"The {op.kind} stopped before anything changed."
    if op.phase in UNCHANGED or op.kind == "backup":
        _abandon(run)
        if run.running and op.phase not in {"stop_app", "backup", "backed_up"} and op.kind == "backup":
            started, detail = _start(service, run.project, run.root, run.log)
            if not started:
                raise _Failed("start_app", "health_check_failed", f"{service.name} did not start again: {detail}")
        return "cancelled", f"The {op.kind} stopped before {service.name}'s data or release changed."
    if op.kind == "update" and op.phase in {"apply_update", "housekeeping"}:
        started, detail = _apply(run)
        if started:
            if not run.running:
                _stop(service, run.project, run.root, run.log)
            return "succeeded", f"{service.name} came up healthy on its new release, so the update was kept."
        run.mark("rollback", "The new release is not healthy; putting back the previous one and its data.")
        _put_back(run, "update_rollback_failed", f"The interrupted update did not come up healthy ({detail})")
        return "rolled_back", f"The interrupted update was undone; {service.name} is back to how it was before."
    if op.kind == "restore" and op.phase in {"start_app", "housekeeping"}:
        if run.running:
            started, detail = _start(service, run.project, run.root, run.log)
            if not started:
                run.mark("rollback", "The restored data does not start; putting back the data from before.")
                _put_back(run, "restore_rollback_failed", f"{service.name} did not start ({detail})")
                return "rolled_back", f"The restored data did not start, so {service.name} is back to how it was."
        _tidy(run)
        return "succeeded", "The restore had finished; the app was started again."
    # A restore that stopped while swapping data: the data from just before goes back.
    run.mark("rollback", "The restore was interrupted; putting back the data from just before.")
    _put_back(run, "restore_rollback_failed", "The interrupted restore could not be undone")
    return "rolled_back", f"The interrupted restore was undone; {service.name} is back to how it was before."


def _begin(
    store: JobStore, journal: OperationStore, job: dict, service: Service, root: Path, running: bool
) -> tuple[Operation, bool, bool]:
    """The job's operation and whether it is resumed. Validates a new update first."""
    job_id, action = str(job["id"]), str(job["action"])
    existing = journal.for_job(job_id)
    previous = existing.previous_release if existing else app_releases.installed(service, root)
    target = None
    supporting_only = False
    if action == "update" and not existing:
        state = app_releases.status(service, root)
        target = app_releases.approved(service, root, previous.variant if previous else "cpu")
        requested = job_params(job).get("target_version", "")
        if target.version != requested:
            raise _Failed(
                "check_release",
                "approval_changed",
                f"Mu3Lab now approves {service.name} {target.version} instead of {requested}. Check for updates again.",
            )
        if not state["update_available"]:
            raise _Failed("check_release", "already_current", f"{service.name} is already up to date.")
        supporting_only = bool(state["supporting_only"])
    elif existing and existing.target_release and existing.previous_release:
        supporting_only = existing.target_release.version == existing.previous_release.version
    op, resumed = journal.begin(
        job_id,
        service.id,
        action,  # type: ignore[arg-type]
        was_running=running,
        previous_release=previous,
        target_release=target,
        chosen_snapshot=job_params(job).get("snapshot_id", "") if action == "restore" else "",
    )
    return op, resumed, supporting_only


def _recovery_target(journal: OperationStore, job: dict, service: Service) -> Operation:
    requested = job_params(job).get("operation_id", "")
    op = journal.get(requested) if requested else journal.needing_attention(service.id)
    if op is None or op.service_id != service.id or op.state not in {"active", "needs_attention"}:
        raise _Failed("validate_service", "nothing_to_recover", f"{service.name} has nothing left to recover.")
    return op


def execute(store: JobStore, state: ControlState | None, job: dict, service: Service, root: Path) -> None:
    from ctl.service_ops import project_path

    job_id, action = str(job["id"]), str(job["action"])
    actor = str(job.get("actor") or "worker")
    project = project_path(service, root)
    journal = OperationStore(store.database)

    def log(line: str) -> None:
        store.append_event(job_id, "log", line)

    def step(stage: str, detail: str) -> None:
        store.append_event(job_id, "stage", f"{stage}: {detail}")

    store.transition(
        job_id, "running", actor=actor, detail=f"{action.title()} started for {service.name}.", step_id=action
    )
    run: _Run | None = None
    final: State = "succeeded"
    try:
        if service.stage != "optional" or not (project / "docker-compose.yml").is_file():
            raise _Failed("validate_service", "unsupported_action", f"{service.name} is not an installed catalog app.")
        if action == "recover":
            run = _Run(service, project, root, journal, _recovery_target(journal, job, service), step, log)
            with job_guard.uncancellable():
                final, detail = _recover(run)
        else:
            op, resumed, supporting_only = _begin(store, journal, job, service, root, _was_running(state, service))
            run = _Run(service, project, root, journal, op, step, log)
            if resumed:
                log(f"Continuing this {action} after the worker restarted (it had reached: {op.phase}).")
            if action == "backup":
                detail = _backup(run)
            elif action == "restore":
                detail = _restore(run)
            else:
                detail = _update(run, supporting_only)
    except _Failed as failure:
        if run is not None:
            journal.finish(run.op.id, failure.outcome, failure.message)
        store.transition(
            job_id,
            "failed",
            actor=actor,
            detail=redact(failure.message),
            error_code=failure.code,
            step_id=failure.stage,
        )
        return
    except job_guard.JobInterrupted as interruption:
        # Only the download can be cancelled; the operation then never changed anything.
        if run is not None and interruption.reason == job_guard.CANCELLED and run.op.phase == "prepare":
            journal.finish(run.op.id, "cancelled", "Cancelled before anything changed.")
        raise
    assert run is not None
    journal.finish(run.op.id, final, detail)
    if final in {"succeeded", "resolved", "rolled_back"} and action in {"restore", "recover"}:
        # A later restore or recovery that worked settles any earlier failed undo.
        journal.resolve(service.id, f"Settled by {action} job {job_id}.")
    if state:
        state.set_installation(service.id, "running" if run.running else "stopped", job_id=job_id, route_state="ready")
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")


def _busy(store: JobStore, service_id: str) -> bool:
    return any(job["service_id"] == service_id for job in store.active_jobs())


def reconcile(store: JobStore, log: Log) -> int:
    """Queue a recovery job for every operation whose job ended without finishing it."""
    journal = OperationStore(store.database)
    queued = 0
    for op in journal.orphaned():
        if _busy(store, op.service_id):
            continue  # another job holds the app; try again on the next pass
        attempt = journal.count_attempt(op.id)
        if attempt > MAX_ATTEMPTS:
            # Recovery itself keeps stopping without an answer: stop guessing.
            journal.finish(
                op.id,
                "needs_attention",
                f"Mu3Lab could not finish the interrupted {op.kind} after {MAX_ATTEMPTS} tries.",
            )
            log(f"An interrupted {op.kind} for {op.service_id} needs attention.")
            continue
        try:
            store.create(
                kind="lifecycle",
                service_id=op.service_id,
                action="recover",
                actor=RECOVERY_ACTOR,
                actor_subject=f"system:{RECOVERY_ACTOR}",
                namespace="maintenance.recover",
                detail=f"Finishing an interrupted {op.kind} safely.",
                idempotency_key=f"recover:{op.id}:{attempt}",
                params={"operation_id": op.id},
            )
        except JobConflict:
            continue  # another job holds the app; try again on the next pass
        queued += 1
        log(f"Queued recovery of an interrupted {op.kind} for {op.service_id}.")
    return queued
