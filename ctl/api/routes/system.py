"""Host, catalog, and platform-status endpoints."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from ctl import (
    __version__,
    backup_location,
    backup_service,
    disk_cleanup,
    platform_backup,
    power,
    recovery_sheet,
    self_update,
)
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, Member, Operator, OperatorMutation
from ctl.backups import readiness as backup_readiness
from ctl.compute import detect
from ctl.control_state import COMPUTE_MODES, ControlState
from ctl.provisioning import ProvisioningStore
from ctl.registry import RegistryError
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.service_state import tailnet_dns_name
from ctl.store import records

ROOT = Path(__file__).resolve().parents[3]

health_router = APIRouter(prefix="/api", tags=["health"], route_class=ContractRoute)
router = APIRouter(prefix="/api/v1", tags=["system"], route_class=ContractRoute)


@health_router.get("/health", response_model=models.Health, response_model_exclude_none=True)
def health() -> models.Health:
    """Liveness probe used by Caddy, systemd, and the installer."""
    return models.Health.model_validate({"ok": True, "version": __version__})


@router.get("/catalog", response_model=models.CatalogResponse, response_model_exclude_none=True)
def catalog(_member: Member) -> models.CatalogResponse:
    """App descriptions and the core suite, from the app manifests."""
    try:
        registry = load_registry()
    except RegistryError as exc:
        return models.CatalogResponse.model_validate({"ok": False, "profiles": [], "services": {}, "error": str(exc)})
    core = [service.id for service in registry.services if service.stage == "core" or service.manifest.tier == "core"]
    services = {
        service.id: {
            "summary": service.summary or service.setup_action,
            "tagline": service.tagline,
            "category": service.category,
            "stage_label": service.group,
            "resource_guidance": service.resource_guidance,
            "integrations": list(service.dependencies),
        }
        for service in registry.services
    }
    profile = {
        "id": "core-suite",
        "name": "Core suite",
        "description": "The core AI services for local models and embeddings, external provider connections, chat, and web research.",
        "services": core,
    }
    return models.CatalogResponse.model_validate({"ok": True, "profiles": [profile], "services": services})


@router.get("/system", response_model=models.SystemResponse, response_model_exclude_none=True)
def system(_member: Member) -> models.SystemResponse:
    return models.SystemResponse.model_validate(
        records.get("status", "system")
        or {
            "ok": True,
            "cpu_percent": 0,
            "docker_ready": False,
            "tailnet_dns_name": "",
            "runtime_root": str(RuntimePaths().root),
            "memory": {"total": 0, "used": 0, "percent": 0},
            "disk": {"total": 0, "used": 0, "percent": 0},
            "backup": {},
            "observed_at": "",
        }
    )


def _system_config_response() -> dict[str, JsonValue]:

    state = ControlState.runtime()
    configured = state.system_config() if state else {"compute_mode": "auto", "updated_at": "", "updated_by": ""}
    detected = detect()
    selected = str(configured["compute_mode"])
    available = ["auto", "cpu"]
    if detected not in available:
        available.append(detected)
    return {
        "ok": True,
        **configured,
        "resolved_compute_mode": detected if selected == "auto" else selected,
        "available_modes": available,
    }


@router.get("/system/config", response_model=models.SystemConfig, response_model_exclude_none=True)
def system_config(_operator: Operator) -> models.SystemConfig:
    return models.SystemConfig.model_validate(_system_config_response())


@router.put("/system/config", response_model=models.SystemConfig, response_model_exclude_none=True)
async def update_system_config(
    payload_model: models.ComputeRequest, request: Request, operator: OperatorMutation
) -> models.SystemConfig:
    mode = str((payload_model.model_dump()).get("compute_mode", ""))
    return models.SystemConfig.model_validate(await run_in_threadpool(_set_compute_mode, mode, operator))


def _set_compute_mode(mode: str, operator: IdentityData) -> dict[str, JsonValue]:
    if mode not in COMPUTE_MODES:
        raise ApiError(422, "compute_mode must be auto, cpu, nvidia, or amd")
    state = runtime.control_state()
    store = runtime.job_store()
    state.set_compute_mode(mode, operator["username"])
    store.record_audit(
        actor=operator["username"],
        event="system.compute_mode.changed",
        detail=f"System compute mode changed to {mode}.",
    )
    return _system_config_response()


@router.get("/system/update", response_model=models.Mu3LabUpdate, response_model_exclude_none=True)
async def mu3lab_update(_operator: Operator, refresh: bool = False) -> models.Mu3LabUpdate:
    """Whether GitHub has a newer Mu3Lab this copy can move to; fetches at most every few minutes."""
    return models.Mu3LabUpdate.model_validate(
        {"ok": True, **await run_in_threadpool(self_update.status, ROOT, refresh=refresh)}
    )


@router.get("/system/changelog", response_model=models.Mu3LabChangelog)
async def mu3lab_changelog(_operator: Operator) -> models.Mu3LabChangelog:
    """What changed in the installed release and the few before it (What's new)."""
    releases = await run_in_threadpool(self_update.history, ROOT)
    return models.Mu3LabChangelog.model_validate({"ok": True, "releases": releases})


@router.post("/system/update", response_model=models.JobResponse, response_model_exclude_none=True)
def start_mu3lab_update(request: Request, operator: OperatorMutation) -> models.JobResponse:
    """Queue the self-update. It restarts the dashboard and worker, so nothing else may be running."""
    store = runtime.job_store()
    key = runtime.idempotency_key(request)
    previous = store.by_idempotency_key(
        key or "",
        kind="update",
        service_id=self_update.SERVICE_ID,
        action=self_update.ACTION,
        actor=operator["username"],
        actor_subject=runtime.mutation_subject(operator),
        namespace="system.update",
    )
    if previous:
        return models.JobResponse.model_validate({"ok": True, "duplicate": True, "job": previous})
    current = self_update.status(ROOT)
    if current["blocked_reason"]:
        raise ApiError(409, current["blocked_reason"])
    if not current["available"]:
        raise ApiError(409, "Mu3Lab is already up to date.")
    busy = [job for job in store.active_jobs() if job["kind"] != "verification"]
    if busy:
        raise ApiError(409, "Wait for the running tasks to finish first; updating restarts Mu3Lab.")
    job = store.create(
        kind="update",
        service_id=self_update.SERVICE_ID,
        action=self_update.ACTION,
        actor=operator["username"],
        actor_subject=runtime.mutation_subject(operator),
        namespace="system.update",
        detail="Owner requested a Mu3Lab update.",
        idempotency_key=key,
    )
    return models.JobResponse.model_validate({"ok": True, "job": job})


@router.post("/system/restart", response_model=models.OkResponse, response_model_exclude_none=True)
def restart_mu3lab(background: BackgroundTasks, operator: OperatorMutation) -> models.OkResponse:
    """Restart the dashboard and worker once this answer is sent; the apps keep running.

    When the dashboard's code changed since its page was built, the page is rebuilt first.
    """
    store = runtime.job_store()
    if [job for job in store.active_jobs() if job["kind"] != "verification"]:
        raise ApiError(409, "Wait for the running tasks to finish first; restarting Mu3Lab stops them.")
    if not self_update.restarting.acquire(blocking=False):
        raise ApiError(409, "Mu3Lab is already restarting.")
    if not self_update.dashboard_outdated(ROOT):
        self_update.restarting.release()
        store.record_audit(actor=operator["username"], event="system.restarted", detail="Owner restarted Mu3Lab.")
        background.add_task(self_update.restart, lambda _line: None)
        return models.OkResponse(ok=True, message="Mu3Lab is restarting; the dashboard is back in a few seconds.")
    store.record_audit(
        actor=operator["username"],
        event="system.restarted",
        detail="Owner restarted Mu3Lab; the dashboard is rebuilt from its changed code first.",
    )

    def failed(detail: str) -> None:
        store.record_audit(
            actor=operator["username"],
            event="system.dashboard_build_failed",
            detail=f"The dashboard was not rebuilt, so the earlier page is kept. {detail}",
        )

    logger = logging.getLogger(__name__)
    background.add_task(self_update.rebuild_and_restart, ROOT, logger.info, failed)
    return models.OkResponse(
        ok=True,
        message="Rebuilding the dashboard with your code changes, then restarting. It's back in about a minute.",
    )


@router.get("/power", response_model=models.PowerStatus, response_model_exclude_none=True)
def power_status(identity: Member) -> models.PowerStatus:
    """Whether Mu3Lab is on; while it turns off, how far it has got.

    The wake page answers this same address once Mu3Lab is off, so the dashboard's page can
    follow the whole turn-off and then show the wake page.
    """
    status: dict[str, Any] = power.public(power.read())
    if identity.get("is_admin"):
        status["blockers"] = power.blockers()
    return models.PowerStatus.model_validate(status)


@router.post("/power/off", response_model=models.OkResponse, response_model_exclude_none=True)
def power_off(operator: OperatorMutation) -> models.OkResponse:
    """Turn the whole of Mu3Lab off: every app, sign-in, Caddy and this dashboard.

    Only the wake page stays on, at the dashboard's address, with a button to turn it on.
    It runs as its own systemd unit, because it stops the dashboard answering this request.
    """
    if problems := power.blockers():
        raise ApiError(409, problems[0])
    if power.read() is not None:
        raise ApiError(409, "Mu3Lab is already turning off.")
    try:
        power.launch("off")
    except power.PowerError as exc:
        raise ApiError(409, str(exc)) from exc
    runtime.job_store().record_audit(
        actor=operator["username"], event="system.turned_off", detail="Turned off all of Mu3Lab."
    )
    return models.OkResponse(ok=True, message="Mu3Lab is turning off.")


@router.post("/system/cleanup", response_model=models.JobResponse, response_model_exclude_none=True)
def free_up_space(payload: models.CleanupRequest, request: Request, operator: OperatorMutation) -> models.JobResponse:
    """Remove removed apps' leftovers and old app versions; their data only when asked."""
    store = runtime.job_store()
    key = runtime.idempotency_key(request)
    params = {"delete_data": "true" if payload.delete_data else "false"}
    previous = store.by_idempotency_key(
        key or "",
        kind="lifecycle",
        service_id=disk_cleanup.SERVICE_ID,
        action="cleanup",
        actor=operator["username"],
        actor_subject=runtime.mutation_subject(operator),
        namespace="system.cleanup",
        request=params,
    )
    if previous:
        return models.JobResponse.model_validate({"ok": True, "duplicate": True, "job": previous})
    if store.active_jobs():
        raise ApiError(409, "Wait for the running tasks to finish first.")
    job = store.create(
        kind="lifecycle",
        service_id=disk_cleanup.SERVICE_ID,
        action="cleanup",
        actor=operator["username"],
        actor_subject=runtime.mutation_subject(operator),
        namespace="system.cleanup",
        request=params,
        params=params,
        detail="Owner asked to free up space.",
        idempotency_key=key,
    )
    return models.JobResponse.model_validate({"ok": True, "job": job})


@router.get("/backups", response_model=models.BackupReadiness, response_model_exclude_none=True)
def backups(_member: Member) -> models.BackupReadiness:
    """Local encrypted-backup readiness; execution needs an authenticated job."""
    return models.BackupReadiness.model_validate({"ok": True, **backup_readiness()})


@router.get("/system/backups/platform", response_model=models.PlatformBackupStatus, response_model_exclude_none=True)
def platform_backup_status(_operator: Operator) -> models.PlatformBackupStatus:
    """Mu3Lab's own backup and each core app's, from their own evidence."""
    return models.PlatformBackupStatus.model_validate(
        {"ok": True, **platform_backup.status(ROOT), "recovery_sheet": recovery_sheet.status(RuntimePaths())}
    )


@router.get("/system/backups/schedule", response_model=models.BackupSchedule, response_model_exclude_none=True)
def backup_schedule_status(_operator: Operator) -> models.BackupSchedule:
    """When scheduled backups run, Kopia's next run, and what did not finish."""
    return models.BackupSchedule.model_validate({"ok": True, **backup_service.status()})


@router.put("/system/backups/schedule", response_model=models.BackupSchedule, response_model_exclude_none=True)
def update_backup_schedule(
    payload_model: models.BackupScheduleRequest, operator: OperatorMutation
) -> models.BackupSchedule:
    store = runtime.job_store()
    try:
        chosen = backup_service.save(payload_model.model_dump(), str(operator["username"]))
    except ValueError as exc:
        raise ApiError(422, str(exc)) from exc
    store.record_audit(
        actor=str(operator["username"]),
        event="backup.schedule_changed",
        detail=f"Scheduled backups: {chosen['frequency']}, weekday {chosen['weekday']}, {chosen['time']}.",
    )
    return models.BackupSchedule.model_validate({"ok": True, **backup_service.status()})


@router.get("/system/backups/location", response_model=models.BackupLocation, response_model_exclude_none=True)
def backup_location_status(_operator: Operator) -> models.BackupLocation:
    """Where the backups are, the drives they could move to, and the latest move."""
    paths = RuntimePaths()
    try:
        drives = backup_location.choices(paths)
        used = backup_location.used_bytes(paths)
    except backup_location.MoveError as exc:
        raise ApiError(503, str(exc)) from exc
    store = runtime.job_store()
    moves = store.jobs_for_service(backup_location.SERVICE_ID, limit=1)
    return models.BackupLocation.model_validate(
        {
            "ok": True,
            "path": str(backup_location.folder(paths)),
            "problem": backup_location.problem(paths),
            "used_bytes": used,
            "drives": drives,
            "move": moves[0] if moves else None,
        }
    )


@router.post("/system/backups/location", response_model=models.JobResponse, response_model_exclude_none=True)
def move_backups(
    payload: models.BackupLocationRequest, request: Request, operator: OperatorMutation
) -> models.JobResponse:
    """Move the backups to another drive (or back to the main one); a job that pauses backups while it copies."""
    store = runtime.job_store()
    key = runtime.idempotency_key(request)
    params = {"target": payload.target}
    identity: dict[str, Any] = {
        "kind": "lifecycle",
        "service_id": backup_location.SERVICE_ID,
        "action": backup_location.ACTION,
        "actor": operator["username"],
        "actor_subject": runtime.mutation_subject(operator),
        "namespace": "system.backup-location",
        "request": params,
    }
    previous = store.by_idempotency_key(key or "", **identity)
    if previous:
        return models.JobResponse.model_validate({"ok": True, "duplicate": True, "job": previous})
    if store.active_jobs():
        raise ApiError(409, "Wait for the running tasks to finish first.")
    drive = next((item for item in backup_location.choices() if item["id"] == payload.target), None)
    if drive is None:
        raise ApiError(404, "That drive is no longer connected.")
    if drive["current"]:
        raise ApiError(409, f"The backups are already on {drive['name']}.")
    if drive["problem"]:
        raise ApiError(422, drive["problem"])
    job = store.create(
        **identity,
        params=params,
        detail=f"Move the backups to {drive['name']} ({drive['path']}).",
        idempotency_key=key,
    )
    store.record_audit(
        actor=str(operator["username"]),
        event="backup.location_move_requested",
        detail=f"Move the backups to {drive['path']}.",
        job_id=str(job["id"]),
    )
    return models.JobResponse.model_validate({"ok": True, "job": job})


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


@router.post("/system/backups/recovery-sheet", response_model=models.RecoverySheet)
def open_recovery_sheet(operator: OperatorMutation) -> JSONResponse:
    """The backup password and where the backups are, for printing. Each opening is audited."""
    store = runtime.job_store()  # no audit log, no sheet
    sheet = recovery_sheet.contents(tailnet_dns_name(), str(operator["username"]), RuntimePaths())
    store.record_audit(
        actor=str(operator["username"]),
        event="backup.recovery_sheet_opened",
        detail="Opened the recovery sheet, which shows the backup password.",
    )
    body = models.RecoverySheet.model_validate({"ok": True, **sheet}).model_dump(mode="json")
    return JSONResponse(body, headers=_NO_STORE)


@router.post("/system/backups/platform", response_model=models.BackupStarted, response_model_exclude_none=True)
def start_platform_backup(operator: OperatorMutation) -> models.BackupStarted:
    """Ask Kopia's backup service to back up Mu3Lab itself and each core app now, one at a time."""
    if not backup_service.installed():
        raise ApiError(409, "Backups are off. Install Kopia backups from Apps first.", code="backup_service_missing")
    store = runtime.job_store()
    found = backup_service.sources(ROOT)
    core, _skipped = platform_backup.core_apps(load_registry(), ROOT)
    chosen = [platform_backup.backups.PLATFORM_SOURCE, *(service.id for service in core if service.id in found)]
    try:
        backup_service.trigger(chosen)
    except OSError as exc:
        raise ApiError(503, f"The backup service is not running, so nothing was backed up ({exc}).") from exc
    store.record_audit(
        actor=str(operator["username"]), event="backup.requested", detail="Back up Mu3Lab and its core apps now."
    )
    return models.BackupStarted(
        ok=True,
        detail="Mu3Lab saves its own settings first; then each core app stops for a moment while its data is copied.",
    )


@router.get("/provisioning", response_model=models.ProvisioningResponse, response_model_exclude_none=True)
def provisioning(_member: Member) -> models.ProvisioningResponse:
    """Durable first-run state, never transient browser progress."""
    store = ProvisioningStore.runtime()
    if store is None:
        return models.ProvisioningResponse.model_validate(
            {
                "ok": True,
                "available": False,
                "complete": False,
                "phases": [],
                "progress": {"completed": 0, "total": 0},
                "next_action": {"kind": "bootstrap", "label": "Run ./install"},
            }
        )
    return models.ProvisioningResponse.model_validate({"available": True, **store.summary(initialize=False)})
