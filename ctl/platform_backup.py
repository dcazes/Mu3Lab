"""Back up Mu3Lab itself and its core apps, so a lost machine can be rebuilt (R14).

App backups hold each app's data only; its code comes from Mu3Lab's git
checkout and its images from their registries. What git deliberately does not
hold is each installation's own state, and without it restored data cannot be
opened (a database folder is useless without its password):

- the control database (people, settings, every app's generated secrets and
  configuration, release history, jobs) copied with SQLite's backup API, so
  it is consistent while the dashboard keeps running;
- the encryption key that opens those secrets;
- the saved deployment copies (R12) and the tool gateway's database;
- the runtime projects (rendered Compose files and settings).

These go to the Kopia source ``mu3lab``. Then each core app with data
(sign-in, password vault, chat, model gateways) is stopped in reverse
dependency order, backed up as its own source, and started again in
dependency order: the household is without sign-in and chat for those few
minutes, as the owner chose. A marker records which apps this job stopped,
so a worker that dies mid-way starts them again when it comes back. Apps
whose manifest says their data can be fetched again (downloaded models) are
left out, with the reason shown.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl import backups, job_guard, resource_locks
from ctl.control_state import ControlState
from ctl.jobs import JobStore, redact
from ctl.lifecycle import maintenance
from ctl.lifecycle.uninstall import data_directories
from ctl.registry import Registry, Service, load
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic

SERVICE_ID = "mu3lab-platform"
ACTION = "backup"
MARKER = "platform-backup.json"
EXPORT = "platform-export"

Log = Callable[[str], None]


class _Failed(Exception):
    def __init__(self, stage: str, code: str, message: str) -> None:
        super().__init__(message)
        self.stage, self.code, self.message = stage, code, message


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def core_apps(registry: Registry, root: Path) -> tuple[list[Service], list[tuple[Service, str]]]:
    """Core and foundation apps to back up, in dependency order, and those left out with why."""
    candidates = {service.id: service for service in registry.services if service.stage != "optional"}
    ordered: list[Service] = []

    def visit(service: Service, seen: set[str]) -> None:
        if service.id in seen or service in ordered:
            return
        seen.add(service.id)
        for dependency in service.dependencies:
            if dependency in candidates:
                visit(candidates[dependency], seen)
        ordered.append(service)

    for service in sorted(candidates.values(), key=lambda item: item.id):
        visit(service, set())
    included, skipped = [], []
    for service in ordered:
        if not data_directories(service, root):
            continue  # no data of its own (the ingress)
        if not service.manifest.backup.include:
            skipped.append((service, service.manifest.backup.reason or "Its data can be fetched again."))
        else:
            included.append(service)
    return included, skipped


def _sqlite_copy(source: Path, target: Path) -> None:
    """A consistent copy of a live SQLite database (WAL included), private from its first byte."""
    target.touch(mode=0o600)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as live, sqlite3.connect(target) as copy:
        live.backup(copy)
    target.chmod(0o600)


def export_control_plane(paths: RuntimePaths) -> Path:
    """Write Mu3Lab's own state into a private folder for the ``mu3lab`` backup source."""
    target = paths.state / EXPORT
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(mode=0o700)
    database = paths.state / "mu3lab.db"
    if database.is_file():
        _sqlite_copy(database, target / "mu3lab.db")
    key = paths.state / "secrets.key"
    if key.is_file():
        write_atomic(target / "secrets.key", key.read_bytes())
    deployments = paths.state / "deployments"
    if deployments.is_dir():
        shutil.copytree(deployments, target / "deployments", symlinks=False)
    authority = paths.projects / "mcp-gateway" / "authority" / "authority.db"
    if authority.is_file():
        _sqlite_copy(authority, target / "gateway-authority.db")
    from ctl import __version__

    manifest = {"created_at": _now(), "mu3lab_version": __version__, "files": sorted(_listing(target))}
    write_atomic(target / "export.json", json.dumps(manifest, indent=2, sort_keys=True).encode())
    return target


def _listing(folder: Path) -> list[str]:
    return [str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file()]


def _marker(paths: RuntimePaths) -> Path:
    return paths.state / MARKER


def _record_stopped(paths: RuntimePaths, stopped: list[str]) -> None:
    write_atomic(_marker(paths), json.dumps({"stopped": stopped, "at": _now()}).encode())


def _start_all(services: list[Service], root: Path, log: Log) -> list[str]:
    """Start each app again in dependency order; return those that did not come up healthy."""
    from ctl.service_ops import project_path

    failed = []
    for service in services:
        started, detail = maintenance.start_app(service, project_path(service, root), root, log)
        if not started:
            failed.append(f"{service.name} ({redact(detail)[-200:]})")
    return failed


def recover(root: Path, log: Log, paths: RuntimePaths | None = None) -> None:
    """After a worker restart: start any core app a platform backup stopped and never restarted."""
    paths = paths or RuntimePaths()
    marker = _marker(paths)
    try:
        stopped = json.loads(marker.read_text(encoding="utf-8")).get("stopped", [])
    except (OSError, ValueError):
        return
    registry = load()
    services = [registry.get(service_id) for service_id in stopped]
    log("Starting the core apps an interrupted Mu3Lab backup had stopped.")
    failed = _start_all(services, root, log)
    if failed:
        log(f"These apps did not start again after an interrupted backup: {', '.join(failed)}")
    marker.unlink(missing_ok=True)


def _back_up_platform(paths: RuntimePaths, log: Log) -> dict[str, Any]:
    export = export_control_plane(paths)
    folders = [("control", export)]
    if paths.projects.is_dir():
        folders.append(("projects", paths.projects))
    try:
        return backups.snapshot_source(
            backups.PLATFORM_SOURCE, folders, "platform", log, prune_old=False, verify=False, paths=paths
        )
    finally:
        shutil.rmtree(export, ignore_errors=True)


def _back_up_core(
    services: list[Service],
    root: Path,
    state: ControlState | None,
    step: Callable[[str, str], None],
    log: Log,
    paths: RuntimePaths,
) -> tuple[list[str], list[str]]:
    """Stop, back up and restart the core apps; return (saved names, problems)."""
    from ctl.service_ops import project_path

    running = [service for service in services if maintenance.was_running(state, service)]
    saved: list[str] = []
    problems: list[str] = []
    stopped: list[Service] = []
    with job_guard.uncancellable():
        try:
            for service in reversed(running):
                step("stop_app", f"Stopping {service.name} for its backup.")
                # Recorded before stopping, so a crash here still restarts it.
                _record_stopped(paths, [item.id for item in [*stopped, service]])
                maintenance.stop_app(service, project_path(service, root), root, log)
                stopped.append(service)
            for service in services:
                step("backup", f"Saving {service.name}'s data.")
                try:
                    backups.snapshot(
                        service.id,
                        data_directories(service, root),
                        "platform",
                        log,
                        prune_old=False,
                        verify=False,
                        paths=paths,
                    )
                    saved.append(service.name)
                except (backups.BackupError, OSError) as exc:
                    problems.append(f"{service.name} was not backed up: {exc}")
        except maintenance.StopFailed as failure:
            problems.append(failure.message)
        finally:
            order = [service for service in services if service in stopped]
            if order:
                step("start_app", "Starting the core apps again.")
            failed = _start_all(order, root, log)
            problems.extend(f"{name} did not start again" for name in failed)
            if not failed:
                _marker(paths).unlink(missing_ok=True)
    return saved, problems


def execute_claimed(store: JobStore, job: dict, worker_id: str, root: Path) -> None:
    job_id, actor = str(job["id"]), str(job.get("actor") or worker_id)

    def log(line: str) -> None:
        store.append_event(job_id, "log", line)

    def step(stage: str, detail: str) -> None:
        store.append_event(job_id, "stage", f"{stage}: {detail}")

    store.transition(job_id, "running", actor=actor, detail="Backing up Mu3Lab.", step_id="platform")
    paths = RuntimePaths()
    try:
        registry = load()
        services, skipped = core_apps(registry, root)
        with resource_locks.hold(*(f"app:{service.id}" for service in services)):
            step("platform", "Saving Mu3Lab's own settings, secrets and deployments.")
            try:
                platform = _back_up_platform(paths, log)
            except (backups.BackupError, OSError, sqlite3.Error) as exc:
                raise _Failed(
                    "platform", "platform_backup_failed", f"Mu3Lab's own state was not backed up: {exc}"
                ) from exc
            saved, problems = _back_up_core(services, root, ControlState.runtime(), step, log, paths)
        step("housekeeping", "Checking the backups and tidying up old ones.")
        try:
            backups.check(log, paths=paths)
        except backups.BackupError as exc:
            problems.append(str(exc))
        for source in [backups.PLATFORM_SOURCE, *(service.id for service in services)]:
            backups.prune(source, log, paths=paths)
    except resource_locks.Busy as busy:
        store.transition(
            job_id, "failed", actor=actor, detail=f"Nothing was changed: {busy}.", error_code="resource_busy"
        )
        return
    except _Failed as failure:
        store.transition(
            job_id, "failed", actor=actor, detail=failure.message, error_code=failure.code, step_id=failure.stage
        )
        return
    left_out = "".join(f" {service.name} is left out: {reason}" for service, reason in skipped)
    detail = (
        f"Saved Mu3Lab's own state (backup {platform['snapshot_id'][:8]}) and {len(saved)} core apps"
        f"{': ' + ', '.join(saved) if saved else ''}.{left_out}"
    )
    if problems:
        store.transition(
            job_id,
            "failed",
            actor=actor,
            detail=redact(f"{detail} Problems: {'; '.join(problems)}"),
            error_code="platform_backup_incomplete",
            step_id="backup",
        )
        return
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")


def status(root: Path, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """What the dashboard shows about Mu3Lab's own backup and each core app's."""
    paths = paths or RuntimePaths()
    services, skipped = core_apps(load(), root)
    return {
        "platform": backups.protection(backups.PLATFORM_SOURCE, paths),
        "core_apps": [
            {"id": service.id, "name": service.name, "included": True, "reason": "",
             "protection": backups.protection(service.id, paths)}
            for service in services
        ]
        + [
            {"id": service.id, "name": service.name, "included": False, "reason": reason, "protection": None}
            for service, reason in skipped
        ],
    }  # fmt: skip
