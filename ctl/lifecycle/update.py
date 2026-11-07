"""Update one app to the release Mu3Lab approves, or put it back exactly as it was.

1. Download the new release's images while the app keeps running.
2. Save a copy of the whole deployment and note the update (``in_progress``).
3. Prepare and check the new release's files, still while the app runs.
4. Stop the app and back up its data (the backup an undo puts back).
5. Start the new release and wait for its health check.
6. Healthy: done. Anything failed in steps 3–5: the deployment from before
   goes back, the data goes back from the backup taken in step 4 (the new
   release may have migrated it), and the app starts again.

Nothing continues by itself after a crash or a power cut. The note stays, so
the app shows "Needs attention: an update was interrupted" until the owner
chooses "Undo update" (``undo``, the default) or, once the new release was
in place, "Keep new version" (``keep``).

Mu3Lab's own changes to an app's files, with the same images (``files_only``),
take the same steps. A start after the files changed (the new ones, or the old
ones put back) rebuilds the images Mu3Lab builds from source, so the files in
place are the ones that run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ctl import actions, backups, database_dumps, job_guard
from ctl.engine.runtime import render_service
from ctl.jobs import redact
from ctl.lifecycle import app_control, app_releases, deployments, in_progress, restore
from ctl.lifecycle.app_releases import Release
from ctl.lifecycle.uninstall import backed_up, data_directories
from ctl.registry import Service
from ctl.runtime import RuntimePaths

Log = Callable[[str], None]
Step = Callable[[str, str], None]


class UpdateFailed(RuntimeError):
    """The update did not happen; ``code`` is the job's error code, the message is safe to show."""

    def __init__(self, message: str, code: str = "update_failed") -> None:
        super().__init__(message)
        self.code = code


def _validate(service: Service, root: Path, log: Log) -> None:
    from ctl.lifecycle import compose_settings

    folder = app_control.project(service, root)
    env, extra = compose_settings.settings(service, folder)
    rc, output = actions.compose_config(folder, log, env=env, extra_files=extra)
    if rc:
        raise RuntimeError(f"The new release's settings are not valid: {output[-300:]}")


def update(
    service: Service,
    root: Path,
    requested: str,
    log: Log,
    step: Step,
    *,
    job_id: str,
    running: bool,
    paths: RuntimePaths | None = None,
) -> str:
    paths = paths or RuntimePaths()
    if restore.leftovers(service, root, paths) or in_progress.get(service.id, paths):
        raise UpdateFailed(
            f"Nothing changed: an earlier restore or update of {service.name} was interrupted. Put it back first.",
            "recovery_required",
        )
    state = app_releases.status(service, root)
    previous = app_releases.installed(service, root, paths)
    target = app_releases.approved(service, root, previous.variant if previous else "cpu")
    if target.version != requested:
        raise UpdateFailed(
            f"Mu3Lab now approves {service.name} {target.version} instead of {requested}. Check for updates again.",
            "approval_changed",
        )
    if not state["update_available"]:
        raise UpdateFailed(f"{service.name} is already up to date.", "already_current")
    current = previous.version if previous else "the installed release"
    if state["files_only"]:
        step("download_images", f"Checking {service.name}'s images. The app keeps running meanwhile.")
    else:
        step("download_images", f"Downloading {service.name} {target.version}. The app keeps running meanwhile.")
    try:
        app_releases.download(target.images, log)
    except RuntimeError as exc:
        raise UpdateFailed(f"Nothing changed: {exc}", "image_pull_failed") from exc
    with job_guard.uncancellable():
        bundle = deployments.capture(
            service,
            f"update-{job_id[:12]}",
            release_id=app_releases.remember(service.id, previous, paths) if previous else "",
            storage=[folder.name for folder in data_directories(service, root)],
            paths=paths,
        )
        note = in_progress.begin(
            service.id,
            "update",
            job_id,
            paths,
            bundle=bundle,
            was_running=running,
            previous=previous.as_dict() if previous else None,
            target=target.as_dict(),
        )
        step("stage_definition", "Preparing the new release's settings and files.")
        try:
            render_service(service, root)
            app_releases.align(service, root)
            _validate(service, root, log)
        except (OSError, ValueError, RuntimeError) as exc:
            deployments.restore(service, bundle, paths)
            in_progress.clear(service.id, paths)
            raise UpdateFailed(f"Nothing changed: {exc}", "definition_invalid") from exc
        step("backup", f"Stopping {service.name} and saving a backup of {current}.")
        saved: dict[str, Any] | None = None
        try:
            app_control.stop(service, root, log)
            # Data that is only downloaded again (Ollama's models) is never backed up, here either.
            folders = backed_up(service, data_directories(service, root)) if service.manifest.backup.include else []
            # An app with no data folders on this server (LiveKit, Kopia) has nothing a backup could hold;
            # the deployment copy above is all an undo needs.
            if backups.sources(folders, paths):
                # Its databases are dumped as for any backup, so this one restores the same way.
                database_dumps.write(service, root, log, paths)
                saved = backups.snapshot(
                    service.id,
                    folders,
                    "pre-update",
                    log,
                    version=previous.version if previous else "",
                    release_id=app_releases.remember(service.id, previous, paths) if previous else "",
                    paths=paths,
                )
            elif not service.manifest.backup.include:
                why = service.manifest.backup.reason or "It can be downloaded again."
                log(f"No backup of {service.name} is saved first. {why}")
            else:
                log(f"{service.name} keeps no data on this server, so there is nothing to back up first.")
        except (app_control.StopFailed, backups.BackupError, database_dumps.DumpFailed, OSError) as exc:
            deployments.restore(service, bundle, paths)
            in_progress.clear(service.id, paths)
            if running:
                app_control.start(service, root, log)
            raise UpdateFailed(f"Nothing was updated: {exc}", "backup_failed") from exc
        note = in_progress.update(service.id, paths, snapshot_id=saved["snapshot_id"] if saved else "", applied=True)
        kept = f" Backup {saved['snapshot_id'][:8]} holds the data from before." if saved else ""
        step("apply_update", f"Starting {service.name} {target.version}. Upgrading its data can take a few minutes.")
        app_releases.write(service.id, target, paths)
        started, detail = app_control.start(service, root, log, build=True)
        if started:
            if not running:
                app_control.stop(service, root, log)
            in_progress.clear(service.id, paths)
            _refresh_route(service, root, log)
            _refresh_connectors(service, root, log, running=running)
            try:
                deployments.prune(service.id, set(), paths)
            except OSError as exc:
                log(f"Old deployment copies were not removed this time: {exc}")
            moved = (
                f"Updated {service.name} with Mu3Lab's latest changes to it"
                if state["files_only"]
                else f"Updated {service.name}'s supporting services"
                if state["supporting_only"]
                else f"Updated {service.name} from {current} to {target.version}"
            )
            return f"{moved}.{kept}"
        step("put_back", f"{target.version} did not start; putting back {current} and its data.")
        cause = redact(detail)[-300:]
        try:
            undo(service, root, log, note, paths)
        except restore.PutBackFailed as failed:
            raise UpdateFailed(
                f"{service.name} {target.version} did not start ({cause}), and putting back {current} did not "
                f"finish: {failed}.{kept}",
                "update_put_back_failed",
            ) from None
    raise UpdateFailed(
        f"{service.name} {target.version} did not start ({cause}), so Mu3Lab put back {current} with its data and "
        "settings from just before; nothing was lost.",
        "update_rolled_back",
    )


def _replace_data(service: Service, root: Path, snapshot_id: str, log: Log, paths: RuntimePaths) -> None:
    """Put the data from ``snapshot_id`` in place of the (stopped) app's data."""
    chosen = restore._chosen(service, snapshot_id, log, paths)
    names, _absent = restore.plan(chosen, backed_up(service, data_directories(service, root)), paths)
    restore._wipe(
        [restore.place(name, kind, paths) for name in names for kind in ("restoring", "original")], log, paths
    )
    for name in names:
        staging = restore.place(name, "restoring", paths)
        staging.mkdir(mode=0o700)
        backups.restore_folder(snapshot_id, name, staging, log, paths)
    dumps = database_dumps.prepare(
        service, root, {name: restore.place(name, "restoring", paths) for name in names}, log, paths
    )
    for name in names:
        live, aside = paths.data / name, restore.place(name, "discard", paths)
        restore._wipe([aside], log, paths)
        if live.exists():
            live.rename(aside)
        restore.place(name, "restoring", paths).rename(live)
    restore._wipe([restore.place(name, "discard", paths) for name in names], log, paths)
    database_dumps.load(service, root, dumps, log, paths)


def undo(service: Service, root: Path, log: Log, note: dict[str, Any], paths: RuntimePaths | None = None) -> str:
    """Put the deployment and data from before an update back, and start the app if it ran."""
    paths = paths or RuntimePaths()
    try:
        app_control.stop(service, root, log)
        if note.get("bundle"):
            deployments.restore(service, str(note["bundle"]), paths)
        elif note.get("previous"):
            app_releases.write(service.id, Release.from_dict(note["previous"]), paths)
        if note.get("snapshot_id"):
            # The new release may have migrated the data in place.
            _replace_data(service, root, str(note["snapshot_id"]), log, paths)
    except (
        app_control.StopFailed,
        deployments.BundleError,
        backups.BackupError,
        database_dumps.DumpFailed,
        restore.RestoreFailed,
        OSError,
    ) as exc:
        raise restore.PutBackFailed(str(exc)) from exc
    in_progress.clear(service.id, paths)
    if note.get("was_running", True):
        started, detail = app_control.start(service, root, log, build=True)
        if not started:
            return f"The previous release and its data are back, but {service.name} did not start: {detail[-300:]}"
    return f"{service.name} is back to how it was before the update."


def keep(service: Service, root: Path, log: Log, note: dict[str, Any], paths: RuntimePaths | None = None) -> str:
    """After an interrupted update whose new release was already in place: keep it if it starts healthy."""
    paths = paths or RuntimePaths()
    if not note.get("applied") or not note.get("target"):
        raise restore.PutBackFailed("The update had not reached the new release yet; undo it instead.")
    app_releases.write(service.id, Release.from_dict(note["target"]), paths)
    started, detail = app_control.start(service, root, log, build=True)
    if not started:
        raise restore.PutBackFailed(f"The new release did not start: {redact(detail)[-300:]}. Undo the update instead.")
    if not note.get("was_running", True):
        app_control.stop(service, root, log)
    in_progress.clear(service.id, paths)
    _refresh_route(service, root, log)
    _refresh_connectors(service, root, log, running=note.get("was_running", True))
    return f"{service.name} kept its new release; it started healthy."


def _refresh_route(service: Service, root: Path, log: Log) -> None:
    """Point the app's private address at its new release (a release may move its port: qBittorrent to 8097)."""
    if service.private_https_port is None:
        return
    from ctl.registry import load as load_registry
    from ctl.routes import apply as apply_route

    routed, detail = apply_route(load_registry(), service, root, log)
    if not routed:
        log(f"{service.name}'s private address was not refreshed: {redact(detail)}")


def _refresh_connectors(service: Service, root: Path, log: Log, *, running: bool) -> None:
    """Give the app's chat connectors the new release's files (they say where the app listens)."""
    from ctl.mcp_ops import refresh_files

    try:
        refresh_files(service.id, running=running, root=root, log=log)
    except OSError as exc:
        log(f"{service.name}'s chat connectors kept their previous files: {redact(str(exc))}")
