"""Host, catalog, and platform-status endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from ctl import __version__, self_update
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


@router.get("/backups", response_model=models.BackupReadiness, response_model_exclude_none=True)
def backups(_member: Member) -> models.BackupReadiness:
    """Local encrypted-backup readiness; execution needs an authenticated job."""
    return models.BackupReadiness.model_validate({"ok": True, **backup_readiness()})


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
