"""Host, catalog, and platform-status endpoints."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import psutil
import yaml
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from ctl import __version__, self_update
from ctl.actions import docker_argv
from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, Member, Operator, OperatorMutation
from ctl.backups import readiness as backup_readiness
from ctl.control_state import COMPUTE_MODES, ControlState
from ctl.provisioning import ProvisioningStore
from ctl.registry import RegistryError
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.service_state import container_memory, tailnet_serve_status, tailscale_status

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "catalog.yaml"

health_router = APIRouter(prefix="/api", tags=["health"])
router = APIRouter(prefix="/api/v1", tags=["system"])


@health_router.get("/health")
def health() -> dict[str, Any]:
    """Liveness probe used by Caddy, systemd, and the installer."""
    return {"ok": True, "version": __version__}


@router.get("/catalog")
def catalog(_member: Member) -> dict[str, Any]:
    """Project bundle definitions and UI copy from the typed service manifest."""
    empty = {"ok": False, "profiles": [], "services": {}}
    try:
        raw = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return empty | {"error": str(exc)}
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return empty | {"error": "catalog schema is invalid"}
    try:
        registry = load_registry()
    except RegistryError as exc:
        return empty | {"error": str(exc)}
    services = {
        service.id: {
            "summary": service.summary or service.setup_action,
            "tagline": service.tagline,
            "category": service.category,
            "stage_label": f"{service.maturity.title()} · {service.stage}",
            "resource_guidance": service.resource_guidance,
            "integrations": list(service.dependencies),
        }
        for service in registry.services
    }
    return {"ok": True, "profiles": raw.get("profiles", []), "services": services}


@router.get("/integrations")
def integrations(_member: Member) -> dict[str, Any]:
    """Expose reviewed wiring declarations only; no credentials or mutations."""
    try:
        registry = load_registry()
    except RegistryError as exc:
        return {"ok": False, "error": str(exc), "integrations": []}
    return {
        "ok": True,
        "policy": "free-first",
        "integrations": [
            {"source": "ollama", "destination": "litellm", "kind": "model"},
            {"source": "freellmapi", "destination": "litellm", "kind": "optional_model"},
            {"source": "litellm", "destination": "lobehub", "kind": "model"},
        ],
        "blocked": [service.public() for service in registry.services if service.is_blocked],
    }


def _worker_state() -> str:
    """systemd's word for the background worker ("active", "failed", ...), or "unknown"."""
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", "mu3lab-worker.service"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    state = proc.stdout.strip()
    # No user session bus (e.g. a development run) prints nothing useful.
    return state if state in {"active", "activating", "deactivating", "inactive", "failed"} else "unknown"


@router.get("/system")
def system(_member: Member) -> dict[str, Any]:
    """Read-only host capacity and private-network status for the dashboard."""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(RuntimePaths().root.parent))
    try:
        docker = (
            subprocess.run(docker_argv(["docker", "info"]), capture_output=True, text=True, timeout=5).returncode == 0
        )
    except (OSError, subprocess.SubprocessError):
        docker = False
    tailscale = tailscale_status()
    tailscale["serve"] = tailnet_serve_status()
    return {
        "ok": True,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "uptime_seconds": max(0, int(time.time() - psutil.boot_time())),
        "memory": {"total": memory.total, "used": memory.used, "percent": memory.percent},
        "disk": {"total": disk.total, "used": disk.used, "percent": disk.percent},
        "docker_ready": docker,
        "container_memory": container_memory() if docker else {},
        "worker_state": _worker_state(),
        "tailnet_dns_name": tailscale["dns_name"],
        "tailscale": tailscale,
        "runtime_root": str(RuntimePaths().root),
        "backup": backup_readiness(),
    }


def _system_config_response() -> dict[str, Any]:
    from ctl.compute import detect

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


@router.get("/system/config")
def system_config(_operator: Operator) -> dict[str, Any]:
    return _system_config_response()


@router.put("/system/config")
async def update_system_config(request: Request, operator: OperatorMutation) -> dict[str, Any]:
    mode = str((await runtime.json_body(request)).get("compute_mode", ""))
    return await run_in_threadpool(_set_compute_mode, mode, operator)


def _set_compute_mode(mode: str, operator: IdentityData) -> dict[str, Any]:
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


@router.get("/system/update")
async def mu3lab_update(_operator: Operator, refresh: bool = False) -> dict[str, Any]:
    """Whether GitHub has a newer Mu3Lab this copy can move to; fetches at most every few minutes."""
    return {"ok": True, **await run_in_threadpool(self_update.status, ROOT, refresh=refresh)}


@router.post("/system/update")
def start_mu3lab_update(request: Request, operator: OperatorMutation) -> dict[str, Any]:
    """Queue the self-update. It restarts the dashboard and worker, so nothing else may be running."""
    store = runtime.job_store()
    key = runtime.idempotency_key(request)
    previous = store.by_idempotency_key(key or "")
    if previous:
        return {"ok": True, "duplicate": True, "job": previous}
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
        detail="Owner requested a Mu3Lab update.",
        idempotency_key=key,
    )
    return {"ok": True, "job": job}


@router.get("/backups")
def backups(_member: Member) -> dict[str, Any]:
    """Local encrypted-backup readiness; execution needs an authenticated job."""
    return {"ok": True, **backup_readiness()}


@router.get("/provisioning")
def provisioning(_member: Member) -> dict[str, Any]:
    """Durable first-run state, never transient browser progress."""
    store = ProvisioningStore.runtime()
    if store is None:
        return {
            "ok": True,
            "available": False,
            "complete": False,
            "phases": [],
            "progress": {"completed": 0, "total": 0},
            "next_action": {"kind": "bootstrap", "label": "Run ./install"},
        }
    store.reconcile_runtime()
    return {"available": True, **store.summary()}
