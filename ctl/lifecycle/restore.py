"""Restore one app from a backup, or put it back exactly as it was.

1. Kopia unpacks the chosen backup beside the live data, into
   ``.<folder>.restoring``, while the app keeps running. A failure here
   changes nothing.
2. Mu3Lab notes what it is about to change (``in_progress``) and stops the app.
3. Each live folder is renamed to ``.<folder>.original`` and the restored copy
   takes its place. A backup from another release also brings back that
   release's deployment.
4. The app starts and must pass its health check.
5. Healthy: the original folders are deleted. Anything failed in steps 2–4:
   the original folders and deployment go back, the app starts again, and
   the job says "The restore did not work, so the data was put back as it
   was", with Retry.

Nothing continues by itself after a crash or a power cut. The note from step
2 stays, so the app shows "Needs attention: a restore was interrupted" until
the owner chooses "Put my original data back" (``put_back``) or restores again
once it is back.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ctl import backups, job_guard
from ctl.jobs import redact
from ctl.lifecycle import app_control, app_releases, deployments, in_progress
from ctl.lifecycle.app_releases import Release
from ctl.lifecycle.uninstall import data_directories
from ctl.registry import Service
from ctl.runtime import RuntimePaths

RESTORING, ORIGINAL, DISCARD = "restoring", "original", "discard"

Log = Callable[[str], None]
Step = Callable[[str, str], None]


class RestoreFailed(RuntimeError):
    """The restore did not happen; ``code`` is the job's error code, the message is safe to show."""

    def __init__(self, message: str, code: str = "restore_failed") -> None:
        super().__init__(message)
        self.code = code


class PutBackFailed(RuntimeError):
    """Putting the app back did not finish; the note stays, so the app needs attention."""


def place(name: str, kind: str, paths: RuntimePaths) -> Path:
    return paths.data / f".{name}.{kind}"


def _has_content(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except PermissionError:
        return True  # unreadable to this user: owned by a container user, so it holds data


def _chosen(service: Service, snapshot_id: str, log: Log, paths: RuntimePaths) -> dict[str, Any]:
    try:
        chosen = next((snap for snap in backups.snapshots(service.id, log, paths) if snap["id"] == snapshot_id), None)
    except backups.BackupError as exc:
        raise RestoreFailed(f"Nothing changed: {exc}") from exc
    if not chosen:
        raise RestoreFailed("Nothing changed: that backup no longer exists.")
    if not chosen["paths"]:
        try:
            chosen["paths"] = [f"/data/{name}" for name in backups.folders_in(snapshot_id, log, paths)]
        except backups.BackupError as exc:
            raise RestoreFailed(f"Nothing changed: {exc}") from exc
    return chosen


def plan(chosen: dict[str, Any], folders: list[Path], paths: RuntimePaths) -> tuple[list[str], list[str]]:
    """The folders to restore and those that do not exist yet; refuses a backup for another layout.

    Every folder the deployment uses must be in the backup, unless it has no
    data on this server either, so a restore never leaves old and new data
    side by side.
    """
    for directory in folders:
        backups.check_root(directory, paths)
    allowed = {f"/data/{directory.name}": directory.name for directory in folders}
    unknown = [path for path in chosen["paths"] if path not in allowed]
    if unknown or not chosen["paths"]:
        raise RestoreFailed("Nothing changed: that backup was made for a different folder layout.")
    saved = sorted({allowed[path] for path in chosen["paths"]})
    incomplete = [name for name in allowed.values() if name not in saved and _has_content(paths.data / name)]
    if incomplete:
        raise RestoreFailed(
            f"Nothing changed: that backup does not include {', '.join(sorted(incomplete))}, which this app uses "
            "now, and restoring it would mix old and new data."
        )
    return saved, [name for name in saved if not (paths.data / name).exists()]


def _target(
    service: Service, chosen: dict[str, Any], root: Path, log: Log, step: Step, paths: RuntimePaths
) -> Release | None:
    """The release the backup was saved from when it differs from the installed one, downloaded."""
    previous = app_releases.installed(service, root, paths)
    version, release_id = str(chosen.get("version") or ""), str(chosen.get("release_id") or "")
    if release_id and previous:
        switch = release_id != previous.id
    else:
        switch = bool(version) and version != (previous.version if previous else "")
    if not switch:
        return None
    step("download_images", f"Downloading {service.name} {version}, the release this backup is from.")
    target = app_releases.approved(service, root, previous.variant if previous else "cpu")
    try:
        if release_id:
            release = (
                target
                if target.id == release_id
                else app_releases.from_history(service.id, release_id=release_id, paths=paths)
            )
        else:
            release = (
                target if target.version == version else app_releases.from_history(service.id, version, paths=paths)
            )
    except app_releases.AmbiguousRelease:
        raise RestoreFailed(
            f"Nothing changed: this server ran more than one deployment of {service.name} {version}, and this "
            "older backup does not say which.",
            "release_ambiguous",
        ) from None
    if not release:
        raise RestoreFailed(
            f"Nothing changed: Mu3Lab no longer knows which images make up {service.name} {version}.",
            "release_unknown",
        )
    try:
        app_releases.download(release.images, log)
    except RuntimeError as exc:
        raise RestoreFailed(f"Nothing changed: {exc}", "image_pull_failed") from exc
    return release


def _wipe(places: list[Path], log: Log, paths: RuntimePaths) -> None:
    try:
        backups.wipe(places, log, paths=paths)
    except (backups.BackupError, OSError) as exc:
        log(f"Leftover restore folders were not removed this time: {exc}")


def _switch(service: Service, target: Release, log: Log, paths: RuntimePaths) -> None:
    """Move to the deployment the restored data belongs to: its whole saved copy when kept, else its images."""
    bundle = deployments.latest_for_release(service.id, target.id, paths)
    if bundle:
        deployments.restore(service, bundle, paths)
        log("The settings and files that release ran with were put back too.")
    else:
        log("That release's own deployment files were not kept; its images run with the current files.")
    app_releases.write(service.id, target, paths)


def leftovers(service: Service, root: Path, paths: RuntimePaths | None = None) -> list[str]:
    """Folders of this app kept aside by a restore that did not finish."""
    paths = paths or RuntimePaths()
    return [folder.name for folder in data_directories(service, root) if place(folder.name, ORIGINAL, paths).exists()]


def restore(
    service: Service,
    root: Path,
    snapshot_id: str,
    log: Log,
    step: Step,
    *,
    job_id: str,
    running: bool,
    paths: RuntimePaths | None = None,
) -> str:
    """Put a backup in place; on any failure put the app back as it was. Raises RestoreFailed."""
    paths = paths or RuntimePaths()
    if leftovers(service, root, paths) or in_progress.get(service.id, paths):
        raise RestoreFailed(
            f"Nothing changed: an earlier restore or update of {service.name} was interrupted. "
            "Put the original data back first.",
            "recovery_required",
        )
    chosen = _chosen(service, snapshot_id, log, paths)
    target = _target(service, chosen, root, log, step, paths)
    storage = None
    if target:
        # The deployment that release ran with decides which folders must be in the backup.
        bundle = deployments.latest_for_release(service.id, target.id, paths)
        if bundle:
            storage = [paths.data / name for name in deployments.manifest(service.id, bundle, paths)["storage"]]
    names, absent = plan(chosen, storage or data_directories(service, root), paths)
    when = chosen["time"][:16].replace("T", " ")
    step("stage_restore", f"Unpacking the backup from {when} UTC beside the current data.")
    _wipe([place(name, kind, paths) for name in names for kind in (RESTORING, DISCARD)], log, paths)
    try:
        for name in names:
            staging = place(name, RESTORING, paths)
            staging.mkdir(mode=0o700)
            backups.restore_folder(snapshot_id, name, staging, log, paths)
    except (backups.BackupError, OSError) as exc:
        _wipe([place(name, RESTORING, paths) for name in names], log, paths)
        raise RestoreFailed(f"Nothing changed: {exc}") from exc
    with job_guard.uncancellable():
        previous = app_releases.installed(service, root, paths)
        bundle = deployments.capture(
            service,
            f"restore-{job_id[:12]}",
            release_id=app_releases.remember(service.id, previous, paths) if previous else "",
            storage=[folder.name for folder in data_directories(service, root)],
            paths=paths,
        )
        note = in_progress.begin(
            service.id, "restore", job_id, paths, folders=names, absent=absent, bundle=bundle, was_running=running
        )
        try:
            if running:
                step("stop_app", f"Stopping {service.name}.")
                app_control.stop(service, root, log)
            step("swap", "Putting the restored data in place.")
            for name in names:
                live = paths.data / name
                if live.exists():
                    os.rename(live, place(name, ORIGINAL, paths))
                os.rename(place(name, RESTORING, paths), live)
            if target:
                _switch(service, target, log, paths)
            if running:
                step("start_app", f"Starting {service.name} on the restored data.")
                started, detail = app_control.start(service, root, log)
                if not started:
                    raise RestoreFailed(f"{service.name} did not start on the restored data: {redact(detail)[-300:]}")
        except (
            RestoreFailed,
            app_control.StopFailed,
            backups.BackupError,
            deployments.BundleError,
            OSError,
            RuntimeError,
        ) as exc:
            step("put_back", f"The restore did not work; putting {service.name}'s data back.")
            try:
                put_back(service, root, log, note, paths)
            except PutBackFailed as failed:
                raise RestoreFailed(
                    f"The restore did not work ({exc}), and putting the data back did not finish: {failed}",
                    "restore_put_back_failed",
                ) from exc
            raise RestoreFailed(
                f"The restore did not work, so {service.name}'s data was put back as it was. ({exc})",
                "restore_rolled_back",
            ) from exc
        in_progress.clear(service.id, paths)
        step("housekeeping", "Removing the data from before the restore.")
        _wipe([place(name, ORIGINAL, paths) for name in names], log, paths)
        if running:
            backups.record_restore_tested(service.id, paths)
        try:
            deployments.prune(service.id, set(), paths)
        except OSError as exc:
            log(f"Old deployment copies were not removed this time: {exc}")
    moved = f" {service.name} is back on {target.version}." if target else ""
    stays = "" if running else f" {service.name} was stopped before, so it stays stopped."
    return f"Restored the backup from {when} UTC.{moved}{stays}"


def put_back(service: Service, root: Path, log: Log, note: dict[str, Any], paths: RuntimePaths | None = None) -> str:
    """Put the data and deployment from before a restore back in place, and start the app if it ran.

    Used when a restore fails and after an interrupted one. Safe to repeat:
    each folder is either still in place, kept aside, or restored over it.
    """
    paths = paths or RuntimePaths()
    names, absent = list(note.get("folders") or []), set(note.get("absent") or [])
    try:
        app_control.stop(service, root, log)
        _wipe([place(name, DISCARD, paths) for name in names], log, paths)
        for name in names:
            live, original = paths.data / name, place(name, ORIGINAL, paths)
            if original.exists():
                if live.exists():
                    os.rename(live, place(name, DISCARD, paths))
                os.rename(original, live)
            elif name in absent and live.exists():
                os.rename(live, place(name, DISCARD, paths))  # the folder did not exist before
        if note.get("bundle"):
            deployments.restore(service, str(note["bundle"]), paths)
    except (app_control.StopFailed, deployments.BundleError, OSError) as exc:
        raise PutBackFailed(str(exc)) from exc
    in_progress.clear(service.id, paths)
    _wipe([place(name, kind, paths) for name in names for kind in (DISCARD, RESTORING)], log, paths)
    if note.get("was_running", True):
        started, detail = app_control.start(service, root, log)
        if not started:
            return f"The original data is back, but {service.name} did not start: {redact(detail)[-300:]}"
    return f"{service.name}'s original data is back in place."
