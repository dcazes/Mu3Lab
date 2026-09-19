"""Mu3Lab :: ctl/app.py

WHAT: Real-dashboard backend (port 8787). It exposes read-only host, identity,
      catalog, lifecycle, job, and backup projections plus the authenticated
      core-suite setup action, and serves the routed React shell.
WHY:  Card ③ must end with something answering on :8787, or Caddy and
      Tailscale Serve stack on an empty port. Health + static is the smallest
      honest "dashboard works".
RUN:  Started by mu3lab-ctl.service:
      `.venv/bin/uvicorn ctl.app:app --host 127.0.0.1 --port 8787`
      Dev: same command from the repo root (needs .venv + requirements).
DEBUG: `curl -f http://127.0.0.1:8787/api/health` must print {"ok":true,...}.
      Unknown /api/* paths return JSON 404 (never the SPA page, so clients
      can't mistake HTML for data).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import hmac
import hashlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import psutil
import yaml

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ctl import __version__  # noqa: F401 (re-exported for /api/health)
from ctl.backups import readiness as backup_readiness
from ctl.core_setup import CORE_ORDER, plan as core_plan, start as start_core_setup
from ctl.jobs import JobStore, redact
from ctl.provisioning import ProvisioningStore
from ctl.registry import RegistryError, load as load_registry
from ctl.runtime import RuntimePaths
from ctl.releases import latest as latest_release
from ctl.mcp_registry import snapshot as mcp_snapshot
from ctl.mcp_catalog import load as load_mcp_catalog
from ctl import mcp_config
from ctl.service_state import compose_states, status as service_status, tailnet_dns_name, tailnet_serve_ports
from ctl.service_ops import SUPPORTED_ACTIONS, allowed_actions, project_path
from ctl.control_state import COMPUTE_MODES, ControlState
from ctl import actions
from ctl import service_config
from ctl.provider_catalog import catalog as provider_catalog, get as get_provider, prefix_warning

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dashboard" / "dist"
CATALOG = ROOT / "catalog.yaml"

app = FastAPI(title="Mu3Lab control plane", version=__version__)


def _ingress_token() -> str:
    """Read the local proxy-hop token without exposing it through the API."""
    try:
        from ctl.secrets import read_runtime_env
        return read_runtime_env(ROOT / ".env").get("MU3LAB_INGRESS_TOKEN", "")
    except OSError:
        return ""


def _trusted_proxy(request: Request) -> bool:
    expected = _ingress_token()
    supplied = request.headers.get("x-mu3lab-proxy-token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _csrf_token(request: Request) -> str:
    """Bind a non-secret request token to the verified Authentik session JWT."""
    if not _trusted_proxy(request):
        return ""
    session = request.headers.get("x-authentik-jwt", "")
    control_secret = ""
    try:
        from ctl.secrets import read_runtime_env
        control_secret = read_runtime_env(ROOT / ".env").get("MU3LAB_CTL_TOKEN", "")
    except OSError:
        pass
    if not session or not control_secret:
        return ""
    return hmac.new(control_secret.encode(), session.encode(), hashlib.sha256).hexdigest()


def _mutation_allowed(request: Request) -> bool:
    """Require same-origin browser intent and a token bound to this SSO session."""
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.netloc.lower() != host.lower():
        return False
    expected = _csrf_token(request)
    supplied = request.headers.get("x-mu3lab-csrf", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


@app.get("/api/health")
def health() -> dict:
    """Liveness probe (Caddy, systemd, install verify all use this)."""
    return {"ok": True, "version": __version__}


@app.get("/api/status")
def status() -> dict:
    """Compatibility summary derived from the curated registry, not constants."""
    services = _service_snapshot()["services"]
    return {"version": __version__, "components": [
        {"id": item["id"], "label": item["name"], "detail": item["detail"]}
        for item in services
    ]}


def _service_snapshot(request: Request | None = None) -> dict:
    """Read the curated catalog and project live, non-mutating service health."""
    try:
        registry = load_registry()
    except RegistryError as exc:
        return {"ok": False, "error": str(exc), "services": []}
    dns_name = tailnet_dns_name()
    route_ports = tailnet_serve_ports()
    project_states = compose_states()
    store = JobStore.runtime()
    jobs = store.jobs(limit=100) if store else []
    control_state = ControlState.runtime()
    operator = bool(request and identity(request)["writes_enabled"])
    with ThreadPoolExecutor(max_workers=min(8, len(registry.services))) as pool:
        statuses = list(pool.map(
            lambda service: service_status(service, dns_name, ROOT, route_ports, project_states),
            registry.services))
    result = []
    for service, item in zip(registry.services, statuses, strict=True):
        # Traversing the protected dashboard through Authentik is stronger
        # evidence than a static manifest route flag.
        if service.id == "authentik" and operator and item["health_state"] == "healthy":
            item.update({"state": "ready", "lifecycle_state": "ready",
                         "setup_state": "configured", "route_state": "verified",
                         "route_ready": True, "user_action": "Open securely"})
        latest = next((job for job in jobs if job["service_id"] == service.id), None)
        installation = control_state.installation(service.id) if control_state else None
        missing_config = service_config.missing_required(service)
        if missing_config and service.stage == "optional" and not installation:
            item["state"] = "config_required"
            item["lifecycle_state"] = "config_required"
            item["detail"] = "Complete the required app configuration before installation."
            item["missing_configuration"] = missing_config
        if installation and service.stage == "optional":
            persisted = str(installation["state"])
            if persisted in {"queued", "installing", "starting", "verifying", "failed", "config_required"}:
                item["state"] = persisted
                item["lifecycle_state"] = persisted
            item["installation"] = installation
            item["route_state"] = installation["route_state"]
            item["last_error"] = installation.get("last_error", {})
        item["last_job"] = latest
        item["last_job_id"] = str(latest["id"]) if latest else ""
        item["allowed_actions"] = allowed_actions(service, str(item["state"])) if operator else []
        result.append(item)
    return {"ok": True, "version": __version__, "tailnet_dns_name": dns_name,
            "runtime": RuntimePaths().as_dict(), "services": result}


@app.get("/api/services")
@app.get("/api/v1/services")
def list_services(request: Request) -> dict:
    return _service_snapshot(request)


@app.get("/api/integrations")
def integrations() -> dict:
    """Expose reviewed wiring declarations only; no credentials or mutations."""
    try:
        registry = load_registry()
    except RegistryError as exc:
        return {"ok": False, "error": str(exc), "integrations": []}
    return {"ok": True, "policy": "free-first", "integrations": [
        {"source": "ollama", "destination": "litellm", "kind": "model"},
        {"source": "freellmapi", "destination": "litellm", "kind": "optional_model"},
        {"source": "litellm", "destination": "open-webui", "kind": "model"},
    ], "blocked": [service.public() for service in registry.services if service.is_blocked]}


@app.get("/api/catalog")
def catalog() -> dict:
    """Project bundle definitions and UI copy from the typed service manifest."""
    try:
        raw = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {"ok": False, "error": str(exc), "profiles": [], "services": {}}
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return {"ok": False, "error": "catalog schema is invalid", "profiles": [], "services": {}}
    try:
        registry = load_registry()
    except RegistryError as exc:
        return {"ok": False, "error": str(exc), "profiles": [], "services": {}}
    services = {service.id: {
        "summary": service.identity_note or service.setup_action,
        "category": service.category,
        "stage_label": f"{service.maturity.title()} · {service.stage}",
        "resource_guidance": service.resource_guidance,
        "integrations": list(service.dependencies),
    } for service in registry.services}
    return {"ok": True, "profiles": raw.get("profiles", []), "services": services}


@app.get("/api/system")
def system() -> dict:
    """Read-only host capacity and private-network status for the dashboard."""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(RuntimePaths().root.parent))
    try:
        docker = subprocess.run(["docker", "info"], capture_output=True,
                                text=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        docker = False
    return {
        "ok": True,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory": {"total": memory.total, "used": memory.used,
                   "percent": memory.percent},
        "disk": {"total": disk.total, "used": disk.used, "percent": disk.percent},
        "docker_ready": docker,
        "tailnet_dns_name": tailnet_dns_name(),
        "runtime_root": str(RuntimePaths().root),
        "backup": backup_readiness(),
    }


def _detected_compute_mode() -> str:
    """Resolve the host accelerator without changing drivers or containers."""
    from ctl.compute import detect
    return detect()


def _system_config_response() -> dict:
    state = ControlState.runtime()
    configured = state.system_config() if state else {
        "compute_mode": "auto", "updated_at": "", "updated_by": "",
    }
    detected = _detected_compute_mode()
    selected = str(configured["compute_mode"])
    available = ["auto", "cpu"]
    if detected not in available:
        available.append(detected)
    return {"ok": True, **configured,
            "resolved_compute_mode": detected if selected == "auto" else selected,
            "available_modes": available}


@app.get("/api/v1/system/config")
def system_config(request: Request) -> dict:
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    return _system_config_response()


@app.put("/api/v1/system/config")
async def update_system_config(request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    try:
        payload = await request.json()
        mode = str(payload.get("compute_mode", ""))
    except (ValueError, TypeError, AttributeError):
        return JSONResponse({"ok": False, "error": "invalid JSON configuration"}, status_code=400)
    if mode not in COMPUTE_MODES:
        return JSONResponse({"ok": False,
                             "error": "compute_mode must be auto, cpu, nvidia, or amd"},
                            status_code=422)
    state = ControlState.runtime()
    store = JobStore.runtime()
    if state is None or store is None:
        return JSONResponse({"ok": False, "error": "runtime state is not initialized"}, status_code=503)
    state.set_compute_mode(mode, identity_data["username"])
    store.record_audit(actor=identity_data["username"], event="system.compute_mode.changed",
                       detail=f"System compute mode changed to {mode}.")
    return _system_config_response()


@app.get("/api/backups")
@app.get("/api/v1/backups")
def backups() -> dict:
    """Return local encrypted-backup readiness; execution needs an authenticated job."""
    return {"ok": True, **backup_readiness()}


@app.get("/api/provisioning")
@app.get("/api/v1/provisioning")
def provisioning() -> dict:
    """Return durable first-run state, never transient browser progress."""
    store = ProvisioningStore.runtime()
    if store is None:
        return {"ok": True, "available": False, "complete": False, "phases": []}
    return {"available": True, **store.summary()}


@app.get("/api/identity")
@app.get("/api/v1/identity")
def identity(request: Request) -> dict:
    """Trust Authentik headers only across the authenticated Caddy hop."""
    trusted = _trusted_proxy(request)
    username = request.headers.get("x-authentik-username", "").strip() if trusted else ""
    # Authentik's proxy provider serializes groups with a pipe delimiter.
    groups = tuple(value.strip() for value in request.headers.get("x-authentik-groups", "").split("|")
                   if value.strip()) if trusted else ()
    authenticated = bool(username)
    operator = authenticated and bool({"mu3lab-operators", "authentik Admins"}.intersection(groups))
    if operator:
        # This is machine evidence that an authenticated operator traversed
        # Tailscale, Caddy, Authentik, and reached the real dashboard.
        try:
            from ctl import bootstrap_state
            bootstrap_state.confirm("dashboard_protection")
        except OSError:
            pass
    return {
        "ok": True,
        "control_plane_auth": "authentik_forward_auth" if authenticated else "not_configured",
        "username": username,
        "groups": list(groups),
        "detail": ("Authenticated through Authentik." if operator else
                   "Tailnet access is private, but Authentik protection and operator role mapping are not configured yet."),
        "writes_enabled": operator,
    }


@app.get("/api/v1/session")
def session(request: Request) -> dict:
    """Issue browser-readable CSRF material only after verified proxy identity."""
    identity_data = identity(request)
    token = _csrf_token(request)
    if not identity_data["writes_enabled"] or not token:
        return JSONResponse({"ok": False, "error": "operator session required"}, status_code=403)
    return {"ok": True, "csrf_token": token}


@app.get("/api/setup/core")
@app.get("/api/v1/setup/core")
def core_setup() -> dict:
    """Describe the mandatory suite without claiming it is runnable early."""
    try:
        registry = load_registry()
    except RegistryError as exc:
        return {"ok": False, "ready_to_run": False, "error": str(exc), "services": []}
    execution = core_plan(ROOT)
    store = JobStore.runtime()
    current_job = next((job for job in (store.jobs() if store else [])
                        if job["service_id"] == "core-suite"), None)
    provisioning_store = ProvisioningStore.runtime()
    provisioned = provisioning_store.summary() if provisioning_store else None
    waiting = (provisioned or {}).get("waiting") if provisioned else None
    waiting_for_provider = bool(current_job and current_job.get("state") == "waiting_for_confirmation"
                                and waiting and waiting.get("phase_id") == "configuration")
    return {"ok": True, "ready_to_run": execution["ready"] and not waiting_for_provider,
            "services": list(CORE_ORDER),
            "missing_manifests": execution["missing"],
            "capacity": execution["capacity"], "provisioning": provisioned,
            "current_job": current_job,
            "next_action": ("Add an inference provider to finish chat verification" if waiting_for_provider else
                            "Set up the core application suite" if execution["ready"] else
                            "Core service manifests are still being prepared")}


@app.get("/api/connections/providers")
@app.get("/api/v1/providers")
def provider_metadata(request: Request) -> dict:
    """List safe connection state; encrypted keys never cross this boundary."""
    identity_data = identity(request)
    if not identity_data["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    try:
        from ctl.provider_ops import migrate_legacy
        state = ControlState.runtime()
        if state is None:
            raise ValueError("provider state is not initialized")
        migrate_legacy(state)
        providers = []
        for item in state.providers():
            try:
                definition = get_provider(str(item["provider_id"]))
                hint = definition.key_hint
                name = definition.name
                examples = list(definition.example_models)
                supported = True
            except ValueError:
                hint, name, examples, supported = "Unknown legacy format", str(item["label"]), [], False
            providers.append({
                "id": item["provider_id"], "name": name, "label": item["label"],
                "enabled": item["enabled"], "state": item["state"], "key_hint": hint,
                "credential_indicator": hint.replace("…", "••••"),
                "model_samples": item["model_samples"] or examples,
                "models_are_examples": not bool(item["model_samples"]),
                "last_attempt_at": item["last_attempt_at"],
                "last_verified_at": item["last_verified_at"],
                "updated_at": item["updated_at"], "active_job_id": item["active_job_id"],
                "error": (item["last_error"] or {}).get("message", ""), "supported": supported,
            })
        return {"ok": True, "providers": providers}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


@app.post("/api/connections/providers")
@app.post("/api/v1/providers")
@app.post("/api/v1/providers/{provider_id}")
async def save_provider(request: Request, provider_id: str = "") -> dict:
    """Accept one provider key without ever echoing or logging its value."""
    identity_data = identity(request)
    if not identity_data["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    if not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "same-origin CSRF verification failed"}, status_code=403)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    idempotency_key = request.headers.get("idempotency-key", "")
    previous = store.by_idempotency_key(idempotency_key)
    if previous:
        return {"ok": True, "duplicate": True, "job": previous}
    try:
        payload = await request.json()
        provider_id = provider_id or str(payload.get("provider_id", ""))
        definition = get_provider(provider_id)
        provider_id = definition.id
        label = str(payload.get("label", "")).strip() or definition.name
        api_key = str(payload.get("api_key", ""))
        from ctl.provider_secrets import save
        result = save(provider_id, label, api_key)
    except (ValueError, TypeError, AttributeError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    warning = prefix_warning(provider_id, api_key)
    control = ControlState.runtime()
    if control:
        control.set_provider(result["id"], result["label"], enabled=True, state="verifying")
    audit_job = store.create(kind="wiring", service_id=f"provider:{result['id']}",
                             action="save", actor=identity_data["username"],
                             detail=f"provider:{result['id']}",
                             idempotency_key=idempotency_key or None)
    if control:
        control.set_provider(result["id"], result["label"], enabled=True, state="verifying",
                             attempted=True, job_id=str(audit_job["id"]))
    return {"ok": True, "provider": result, "job": audit_job, "warning": warning,
            "routing": {"configured": False,
                        "detail": "Credential saved privately; the durable worker is verifying model discovery and streamed routing."}}


@app.get("/api/v1/providers/catalog")
def providers_catalog(request: Request) -> dict:
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    return {"ok": True, "providers": provider_catalog()}


async def _provider_action(provider_id: str, action: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    try:
        provider = get_provider(provider_id)
    except ValueError as exc:
        state = ControlState.runtime()
        legacy = state.provider(provider_id) if state else None
        if action != "remove" or not legacy or legacy.get("state") != "unsupported_legacy":
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        provider = None
    state = ControlState.runtime()
    resolved_id = provider.id if provider else provider_id
    if state is None or not state.provider(resolved_id):
        return JSONResponse({"ok": False, "error": "provider connection does not exist"}, status_code=404)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    try:
        job = store.create(kind="wiring", service_id=f"provider:{resolved_id}", action=action,
                           actor=identity_data["username"], detail=f"Operator requested provider {action}.",
                           idempotency_key=request.headers.get("idempotency-key") or None)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return {"ok": True, "job": job}


@app.post("/api/v1/providers/{provider_id}/verify")
async def verify_provider(provider_id: str, request: Request) -> dict:
    return await _provider_action(provider_id, "verify", request)


@app.post("/api/v1/providers/{provider_id}/enable")
async def enable_provider(provider_id: str, request: Request) -> dict:
    return await _provider_action(provider_id, "enable", request)


@app.post("/api/v1/providers/{provider_id}/disable")
async def disable_provider(provider_id: str, request: Request) -> dict:
    return await _provider_action(provider_id, "disable", request)


@app.delete("/api/v1/providers/{provider_id}")
async def remove_provider(provider_id: str, request: Request) -> dict:
    return await _provider_action(provider_id, "remove", request)


@app.get("/api/v1/providers/{provider_id}/models")
def provider_models(provider_id: str, request: Request) -> dict:
    result = provider_metadata(request)
    if isinstance(result, JSONResponse):
        return result
    provider = next((item for item in result["providers"] if item["id"] == provider_id), None)
    if not provider:
        return JSONResponse({"ok": False, "error": "provider connection does not exist"}, status_code=404)
    return {"ok": True, "provider_id": provider_id, "models": provider["model_samples"],
            "examples": provider["models_are_examples"]}


@app.post("/api/setup/core")
@app.post("/api/v1/jobs/core-install")
def start_core(request: Request) -> dict:
    """Start the one guided core-suite job for an Authentik operator."""
    identity_data = identity(request)
    if not identity_data["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    if not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "same-origin CSRF verification failed"}, status_code=403)
    execution = core_plan(ROOT)
    if not execution["ready"]:
        return JSONResponse({"ok": False, "error": execution["error"],
                             "missing_manifests": execution["missing"]}, status_code=409)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    idempotency_key = request.headers.get("idempotency-key", "")
    previous = store.by_idempotency_key(idempotency_key)
    if previous:
        return {"ok": True, "duplicate": True, "job": previous}
    active = [job for job in store.jobs() if job["service_id"] == "core-suite" and job["state"] in {"queued", "running"}]
    if active:
        return JSONResponse({"ok": False, "error": "core setup is already running", "job": active[0]}, status_code=409)
    job = start_core_setup(store, identity_data["username"], ROOT,
                           idempotency_key=idempotency_key or None)
    return {"ok": True, "job": job}


@app.post("/api/v1/services/{service_id}/actions")
async def service_action(service_id: str, request: Request) -> dict:
    """Queue one allowlisted service lifecycle action for the durable worker."""
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    try:
        payload = await request.json()
        action = str(payload.get("action", ""))
    except (ValueError, TypeError, AttributeError):
        return JSONResponse({"ok": False, "error": "invalid JSON action"}, status_code=400)
    if action not in SUPPORTED_ACTIONS:
        return JSONResponse({"ok": False, "error": "unsupported service action"}, status_code=400)
    try:
        service = load_registry().get(service_id)
    except RegistryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    if service.is_blocked:
        return JSONResponse({"ok": False, "error": service.blocked_reason}, status_code=409)
    if action == "stop" and service.lifecycle == "always_on":
        return JSONResponse({"ok": False, "error": "always-on infrastructure cannot be stopped"}, status_code=409)
    if not (service.compose_path(ROOT) / "docker-compose.yml").is_file():
        return JSONResponse({"ok": False, "error": "service manifest is not deployable"}, status_code=409)
    current = service_status(service, tailnet_dns_name(), ROOT)
    control_state = ControlState.runtime()
    installed = control_state.installation(service.id) if control_state else None
    current_state = str(installed["state"]) if installed else str(current["state"])
    if service_config.missing_required(service):
        current_state = "config_required"
    if action not in allowed_actions(service, current_state):
        return JSONResponse({"ok": False,
                             "error": "action is not valid for the current service state"},
                            status_code=409)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    idempotency_key = request.headers.get("idempotency-key", "")
    previous = store.by_idempotency_key(idempotency_key)
    if previous:
        return {"ok": True, "duplicate": True, "job": previous}
    try:
        job = store.create(kind="lifecycle", service_id=service.id, action=action,
                           actor=identity_data["username"],
                           detail=f"Operator requested {action} for {service.name}.",
                           idempotency_key=idempotency_key or None)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    if control_state and job.get("state") == "queued":
        control_state.set_installation(service.id, "queued", job_id=str(job["id"]))
    return {"ok": True, "job": job}


@app.get("/api/v1/services/{service_id}/logs")
def service_logs(service_id: str, request: Request, tail: int = 120,
                 container: str = "") -> dict:
    """Return bounded, redacted logs for a curated Compose service."""
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    try:
        service = load_registry().get(service_id)
    except RegistryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    runtime_project = project_path(service, ROOT)
    compose_path = runtime_project / "docker-compose.yml"
    if not compose_path.is_file():
        return JSONResponse({"ok": False, "error": "service manifest is not deployable"}, status_code=409)
    try:
        definition = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        known = set((definition.get("services") or {}).keys())
    except (OSError, yaml.YAMLError, AttributeError):
        known = set()
    if container and container not in known:
        return JSONResponse({"ok": False, "error": "unknown service container"}, status_code=400)
    lines: list[str] = []
    rc, output = actions.compose_logs(runtime_project, lines.append,
                                      tail=max(20, min(tail, 500)), container=container)
    safe_lines = [redact(line) for line in output[-40000:].splitlines()]
    return JSONResponse({"ok": rc == 0, "service_id": service.id,
                         "container": container, "lines": safe_lines},
                        status_code=200 if rc == 0 else 500)


@app.get("/api/v1/services/{service_id}/configuration")
def get_service_configuration(service_id: str, request: Request) -> dict:
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    try:
        service = load_registry().get(service_id)
    except RegistryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    return {"ok": True, "service_id": service.id,
            "fields": service_config.read(service)}


@app.put("/api/v1/services/{service_id}/configuration")
async def put_service_configuration(service_id: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    try:
        service = load_registry().get(service_id)
    except RegistryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    try:
        payload = await request.json()
        fields = service_config.write(service, payload.get("values", {}))
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    store = JobStore.runtime()
    if store:
        store.record_audit(actor=identity_data["username"],
                           event="service.configuration.changed",
                           detail=f"Typed configuration updated for {service.id}.")
    return {"ok": True, "service_id": service.id, "fields": fields,
            "restart_required": bool((RuntimePaths().projects / service.id / "docker-compose.yml").is_file())}


@app.get("/api/v1/services/{service_id}/updates")
def service_updates(service_id: str, request: Request) -> dict:
    """Check the curated upstream repository for its latest stable release."""
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    try:
        service = load_registry().get(service_id)
    except RegistryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    repository = str(service.update.get("repository", ""))
    if not repository:
        return JSONResponse({"ok": False, "error": "no reviewed upstream release source"}, status_code=409)
    try:
        release = latest_release(repository, str(service.update.get("current_version", "")))
    except (ValueError, RuntimeError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    backup = backup_readiness()
    reason = ("A verified service snapshot is required before updating."
              if backup.get("state") != "verified" else
              service.blocked_reason if service.is_blocked else
              "Update execution remains disabled until the restore executor is available.")
    return {"ok": True, **release, "update_enabled": False,
            "blocked_reason": reason}


@app.get("/api/v1/jobs/{job_id}")
def job_detail(job_id: str, request: Request) -> dict:
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    job = next((item for item in store.jobs(limit=100) if item["id"] == job_id), None)
    if not job:
        return JSONResponse({"ok": False, "error": "job not found"}, status_code=404)
    return {"ok": True, "job": job, "events": store.events(job_id)}


@app.get("/api/v1/mcp/servers")
def mcp_servers(request: Request) -> dict:
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    registry = load_registry()
    states = {item["id"]: item["state"] for item in _service_snapshot(request)["services"]}
    return mcp_snapshot(registry, states)


@app.get("/api/v1/mcp/servers/{server_id}/tools")
def mcp_tools(server_id: str, request: Request) -> dict:
    result = mcp_servers(request)
    if isinstance(result, JSONResponse):
        return result
    server = next((item for item in result["servers"] if item["id"] == server_id), None)
    if not server:
        return JSONResponse({"ok": False, "error": "unknown MCP server"}, status_code=404)
    return {"ok": True, "server_id": server_id, "state": server["state"],
            "tools": server["tools"]}


@app.put("/api/v1/mcp/servers/{server_id}/configuration")
@app.post("/api/v1/mcp/servers/{server_id}/credentials")
async def put_mcp_configuration(server_id: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    registry = load_registry()
    server = next((item for item in load_mcp_catalog(registry) if item.id == server_id), None)
    if not server:
        return JSONResponse({"ok": False, "error": "unknown MCP server"}, status_code=404)
    try:
        payload = await request.json()
        mcp_config.write(server, payload.get("values", {}))
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    store = JobStore.runtime()
    if store:
        store.record_audit(actor=identity_data["username"], event="mcp.configuration.changed",
                           detail=f"Write-only credentials updated for {server.id}.")
    states = {item["id"]: item["state"] for item in _service_snapshot(request)["services"]}
    refreshed = mcp_snapshot(registry, states)
    result = next(item for item in refreshed["servers"] if item["id"] == server_id)
    return {"ok": True, "server": result}


async def _queue_mcp_action(server_id: str, action: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    registry = load_registry()
    server = next((item for item in load_mcp_catalog(registry) if item.id == server_id), None)
    if not server:
        return JSONResponse({"ok": False, "error": "unknown MCP server"}, status_code=404)
    if server.status != "accepted" or not server.compose_dir:
        return JSONResponse({"ok": False, "error": "MCP server has not passed runtime review"}, status_code=409)
    states = {item["id"]: item["state"] for item in _service_snapshot(request)["services"]}
    if states.get(server.service_id) not in {"ready", "running", "degraded"}:
        return JSONResponse({"ok": False, "error": "install and verify the application first"}, status_code=409)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    try:
        job = store.create(kind="wiring", service_id=f"mcp:{server.id}", action=action,
                           actor=identity_data["username"],
                           detail=f"Operator requested MCP {action} for {server.service_id}.",
                           idempotency_key=request.headers.get("idempotency-key") or None)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return {"ok": True, "job": job}


@app.post("/api/v1/mcp/servers/{server_id}/enable")
async def enable_mcp(server_id: str, request: Request) -> dict:
    return await _queue_mcp_action(server_id, "enable", request)


@app.post("/api/v1/mcp/servers/{server_id}/install")
async def install_mcp(server_id: str, request: Request) -> dict:
    return await _queue_mcp_action(server_id, "install", request)


@app.post("/api/v1/mcp/servers/{server_id}/restart")
async def restart_mcp(server_id: str, request: Request) -> dict:
    return await _queue_mcp_action(server_id, "restart", request)


@app.post("/api/v1/mcp/servers/{server_id}/disable")
async def disable_mcp(server_id: str, request: Request) -> dict:
    return await _queue_mcp_action(server_id, "disable", request)


@app.post("/api/v1/mcp/servers/{server_id}/verify")
async def verify_mcp(server_id: str, request: Request) -> dict:
    return await _queue_mcp_action(server_id, "verify", request)


@app.get("/api/v1/mcp/servers/{server_id}/logs")
def mcp_logs(server_id: str, request: Request, tail: int = 120) -> dict:
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    registry = load_registry()
    server = next((item for item in load_mcp_catalog(registry) if item.id == server_id), None)
    if not server:
        return JSONResponse({"ok": False, "error": "unknown MCP server"}, status_code=404)
    project = RuntimePaths().projects / f"mcp-{server.id}"
    if not (project / "docker-compose.yml").is_file():
        return JSONResponse({"ok": False, "error": "MCP runtime is not installed"}, status_code=409)
    output_lines: list[str] = []
    rc, output = actions.compose_logs(project, output_lines.append, tail=max(20, min(tail, 500)))
    return JSONResponse({"ok": rc == 0, "server_id": server.id,
                         "lines": [redact(line) for line in output[-40000:].splitlines()]},
                        status_code=200 if rc == 0 else 500)


@app.get("/api/v1/chat/status")
def chat_status(request: Request) -> dict:
    """Return the curated Open WebUI frame target only to an operator."""
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    dns_name = tailnet_dns_name()
    try:
        service = load_registry().get("open-webui")
        state = service_status(service, dns_name, ROOT, tailnet_serve_ports())
    except RegistryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    ready = (state["health_state"] == "healthy" and bool(dns_name)
             and bool(state.get("route_ready")))
    url = f"https://{dns_name}:8445" if dns_name else ""
    mcp = mcp_snapshot(load_registry(), {
        item["id"]: item["state"] for item in _service_snapshot(request)["services"]
    })
    return {"ok": True, "ready": ready, "url": url,
            "authentication": "trusted_header",
            "mcp_enabled_count": sum(1 for item in mcp["servers"] if item["enabled"]),
            "detail": "Open WebUI is ready." if ready else
                      "Install and verify the core suite before opening Chat."}


@app.get("/api/jobs")
@app.get("/api/v1/jobs")
def jobs() -> dict:
    """Read durable job state. No job creation endpoint exists before identity enforcement."""
    store = JobStore.runtime()
    if store is None:
        return {"ok": True, "available": False, "jobs": []}
    return {"ok": True, "available": True, "jobs": store.jobs()}


@app.get("/api/v1/jobs/{job_id}/events")
def job_events(job_id: str, request: Request) -> dict:
    """Return structured redacted events to an authenticated operator."""
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    if not any(job["id"] == job_id for job in store.jobs(limit=100)):
        return JSONResponse({"ok": False, "error": "job not found"}, status_code=404)
    return {"ok": True, "events": store.events(job_id)}


@app.post("/api/v1/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    try:
        job = store.retry(job_id, actor=identity_data["username"],
                          idempotency_key=request.headers.get("idempotency-key") or None)
    except KeyError:
        return JSONResponse({"ok": False, "error": "job not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return {"ok": True, "job": job}


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    try:
        store.cancel(job_id, actor=identity_data["username"])
    except KeyError:
        return JSONResponse({"ok": False, "error": "job not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return {"ok": True, "state": "cancelled"}


@app.get("/api/audit")
def audit() -> dict:
    """Read append-only, secret-redacted audit metadata."""
    store = JobStore.runtime()
    if store is None:
        return {"ok": True, "available": False, "events": []}
    return {"ok": True, "available": True, "events": store.audit()}


if DIST.is_dir():
    # Static bundle (built by `npm run build`). Mounted only when present so
    # `import ctl.app` still works pre-build (tests, dry runs).
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """Dashboard entry page."""
        return FileResponse(DIST / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str):
        """Unknown non-API paths fall back to the SPA; unknown API paths 404.

        The /api prefix check keeps clients from parsing HTML as JSON.
        """
        if path.startswith("api/"):
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(DIST / "index.html")
