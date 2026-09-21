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
from datetime import UTC, datetime
from pathlib import Path
import hmac
import hashlib
import re
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
from ctl.service_state import compose_snapshot, status as service_status, tailnet_dns_name, tailnet_serve_ports
from ctl.service_ops import SUPPORTED_ACTIONS, allowed_actions, project_path
from ctl.control_state import COMPUTE_MODES, ControlState
from ctl import actions
from ctl import service_config
from ctl.provider_catalog import catalog as provider_catalog, get as get_provider, prefix_warning
from ctl import workflow_secrets
from ctl.install_batches import InstallBatchStore

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dashboard" / "dist"
CATALOG = ROOT / "catalog.yaml"

app = FastAPI(title="Mu3Lab control plane", version=__version__)
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@app.on_event("startup")
def _reconcile_provisioning_on_startup() -> None:
    """Refresh durable milestones once when the control plane starts."""
    store = ProvisioningStore.runtime()
    if store:
        store.reconcile_runtime()
    # Recover deleted/missed optional OIDC blueprints from persisted runtime
    # secrets.  This is file-only reconciliation: no image pull, Compose
    # recreation, account creation, or credential rotation occurs here.
    try:
        from ctl.identity import reconcile_blueprints
        reconcile_blueprints(load_registry(), tailnet_dns_name())
    except (OSError, RegistryError, ValueError):
        pass


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
    project_states, container_snapshots = compose_snapshot()
    store = JobStore.runtime()
    jobs = store.jobs(limit=100) if store else []
    control_state = ControlState.runtime()
    operator = bool(request and identity(request)["writes_enabled"])
    with ThreadPoolExecutor(max_workers=min(4, len(registry.services))) as pool:
        statuses = list(pool.map(
            lambda service: service_status(service, dns_name, ROOT, route_ports, project_states),
            registry.services))
    result = []
    for service, item in zip(registry.services, statuses, strict=True):
        live_state = str(item["state"])
        # Traversing the protected dashboard through Authentik is stronger
        # evidence than a static manifest route flag.
        if service.id == "authentik" and operator and item["health_state"] == "healthy":
            auth_url = f"https://{dns_name}" if dns_name else ""
            item.update({"state": "ready", "lifecycle_state": "ready",
                         "setup_state": "configured", "route_state": "verified",
                         "route_ready": True, "url": auth_url, "user_action": "Open securely",
                         "ui": {"state": "ready", "url": auth_url or None,
                                "label": "Open securely", "authentication": "local", "reason": ""}})
        latest = next((job for job in jobs if job["service_id"] == service.id), None)
        installation = control_state.installation(service.id) if control_state else None
        initialization = control_state.initialization(service.id) if control_state else None
        missing_config = service_config.missing_required(service)
        if missing_config and service.stage == "optional" and not installation:
            item["state"] = "config_required"
            item["lifecycle_state"] = "config_required"
            item["detail"] = "Complete the required app configuration before installation."
            item["missing_configuration"] = missing_config
        if installation and service.stage == "optional":
            persisted = str(installation["state"])
            # Queued/running workflow states describe an active operation.
            # A historical failure must never hide Docker's current healthy
            # state; this was why AdventureLog could be healthy yet shown as
            # Failed and selectable for a second installation.
            workflow_active = persisted in {"queued", "installing", "starting", "verifying", "config_required"}
            if workflow_active or (persisted == "failed" and live_state not in {"ready", "running", "starting", "stopped", "needs_setup"}):
                item["state"] = persisted
                item["lifecycle_state"] = persisted
            item["installation"] = installation
            if item["state"] not in {"ready", "running", "starting", "stopped", "needs_setup"}:
                item["route_state"] = installation["route_state"]
            item["last_error"] = installation.get("last_error", {})
        item["initialization"] = initialization or {
            "mode": service.account.get("mode", "none"),
            "state": "pending" if service.account.get("mode", "none") != "none" else "not_required",
        }
        compose_dir = (RuntimePaths().projects / service.id if service.stage == "optional"
                       and (RuntimePaths().projects / service.id / "docker-compose.yml").is_file()
                       else service.compose_path(ROOT))
        item["containers"] = container_snapshots.get(str(compose_dir.resolve()), [])
        item["last_job"] = latest
        item["last_job_id"] = str(latest["id"]) if latest else ""
        if service.stage == "optional":
            if item["state"] in {"ready", "running", "starting", "stopped", "needs_setup"}:
                item["installation_state"] = "installed"
            elif item["compose_present"]:
                item["installation_state"] = "partial" if installation else "restore_available"
            elif installation and str(installation["state"]) == "failed":
                item["installation_state"] = "failed_setup"
            else:
                item["installation_state"] = "not_installed"
        else:
            item["installation_state"] = "installed" if item["state"] in {"ready", "running", "starting", "stopped"} else "not_installed"
        item["operational_state"] = live_state
        if item["installation_state"] == "restore_available":
            item["recommended_action"] = "restore"
        elif item["state"] in {"failed", "needs_attention", "degraded"}:
            item["recommended_action"] = "view_logs"
        elif item["state"] in {"planned", "not_installed"}:
            item["recommended_action"] = "install"
        else:
            item["recommended_action"] = "none"
        item["allowed_actions"] = allowed_actions(service, str(item["state"])) if operator else []
        from ctl.identity import projection as identity_projection
        item["identity"] = identity_projection(service, item, control_state)
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
        "uptime_seconds": max(0, int(__import__("time").time() - psutil.boot_time())),
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
        return {"ok": True, "available": False, "complete": False, "phases": [],
                "progress": {"completed": 0, "total": 0},
                "next_action": {"kind": "bootstrap", "label": "Run ./install"}}
    store.reconcile_runtime()
    return {"available": True, **store.summary()}


@app.get("/api/identity")
@app.get("/api/v1/identity")
def identity(request: Request) -> dict:
    """Trust Authentik headers only across the authenticated Caddy hop."""
    trusted = _trusted_proxy(request)
    username = request.headers.get("x-authentik-username", "").strip() if trusted else ""
    subject_id = request.headers.get("x-authentik-uid", "").strip() if trusted else ""
    email = request.headers.get("x-authentik-email", "").strip() if trusted else ""
    display_name = request.headers.get("x-authentik-name", "").strip() if trusted else ""
    if email and _EMAIL.fullmatch(email):
        local, domain = email.rsplit("@", 1)
        email = f"{local}@{domain.lower()}"
    else:
        email = ""
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
        try:
            provisioning_store = ProvisioningStore.runtime()
            if provisioning_store:
                current = {item["phase_id"]: item["actual_state"]
                           for item in provisioning_store.summary()["phases"]}
                if current.get("dashboard_protection") != "verified":
                    provisioning_store.update("dashboard_protection", "verified",
                                              detail="Authenticated operator session verified through Authentik.")
        except (OSError, ValueError):
            pass
    return {
        "ok": True,
        "control_plane_auth": "authentik_forward_auth" if authenticated else "not_configured",
        "username": username,
        "subject_id": subject_id,
        "email": email,
        "display_name": display_name,
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


def _calendar_owner(request: Request, *, write: bool = False) -> tuple[dict, str] | JSONResponse:
    identity_data = identity(request)
    owner_uid = str(identity_data.get("subject_id") or "")
    if not owner_uid:
        return JSONResponse({"ok": False, "error": "authenticated subject required"}, status_code=403)
    # Calendar reads are personal and owner-scoped.  Any state-changing
    # operation, including polling Login Flow v2 because it may store a
    # credential, stays behind the same operator boundary as the rest of the
    # control plane as well as the session-bound same-origin check.
    if write and not identity_data.get("writes_enabled"):
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    if write and not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "same-origin mutation verification failed"}, status_code=403)
    return identity_data, owner_uid


def _calendar_readiness(state: ControlState) -> str:
    installation = state.installation("nextcloud")
    if not installation:
        return "not_installed"
    installation_state = str(installation.get("state", ""))
    # A previously failed batch can leave durable workflow state behind even
    # after the application has subsequently become healthy. Use the same
    # local health contract as the service cards so Calendar does not falsely
    # claim that a running Nextcloud is uninstalled.
    if installation_state == "failed":
        try:
            from ctl.registry import load as load_registry
            from ctl.service_state import _healthy
            service = load_registry().get("nextcloud")
            if _healthy(service)[0]:
                installation_state = "running"
            else:
                installation_state = "stopped"
        except Exception:
            installation_state = "stopped"
    if installation_state == "stopped":
        return "service_stopped"
    if installation_state not in {"running", "degraded"}:
        return "not_installed"
    identity_state = state.service_identity("nextcloud")
    if not identity_state or identity_state.get("state") in {"unconfigured", "degraded"}:
        return "sso_not_ready"
    return "ready"


def _calendar_state_response(value: str) -> dict:
    detail = {
        "service_stopped": "Nextcloud is installed but stopped.",
        "sso_not_ready": "Repair Nextcloud sign-in before connecting a calendar.",
        "not_installed": "Install Nextcloud before connecting a calendar.",
    }.get(value, "Calendar is unavailable.")
    return {"ok": True, "state": value, "username_hint": "",
            "selected_calendar_id": "", "calendars": [],
            "last_success_at": "", "error": detail}


@app.get("/api/v1/calendar/connection")
def calendar_connection(request: Request) -> dict:
    auth = _calendar_owner(request)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    state = ControlState.runtime()
    if state is None:
        return JSONResponse({"ok": False, "error": "calendar state is unavailable"}, status_code=503)
    readiness = _calendar_readiness(state)
    if readiness != "ready":
        return _calendar_state_response(readiness)
    from ctl.nextcloud_calendar import public_connection
    return public_connection(owner_uid, state)


@app.post("/api/v1/calendar/connection")
async def save_calendar_connection(request: Request) -> dict:
    auth = _calendar_owner(request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    from ctl.nextcloud_calendar import CalendarError, connect
    try:
        payload = await request.json()
        username = str(payload.get("username", "")).strip()
        app_password = str(payload.get("app_password", ""))
        return connect(owner_uid, username, app_password)
    except CalendarError as exc:
        return JSONResponse({"ok": False, "state": exc.state, "error": str(exc)}, status_code=400)
    except (ValueError, TypeError, AttributeError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/v1/calendar/auto-connect")
def auto_connect_calendar(request: Request) -> dict:
    """Create the owner's encrypted device credential without password entry."""
    auth = _calendar_owner(request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    identity_data, owner_uid = auth
    state = ControlState.runtime()
    if state is None:
        return JSONResponse({"ok": False, "error": "calendar state is unavailable"}, status_code=503)
    readiness = _calendar_readiness(state)
    if readiness != "ready":
        return JSONResponse(_calendar_state_response(readiness), status_code=409)
    from ctl.nextcloud_calendar import CalendarError, auto_connect
    try:
        return auto_connect(owner_uid, str(identity_data.get("username") or ""))
    except CalendarError as exc:
        return JSONResponse({"ok": False, "state": exc.state, "error": str(exc)}, status_code=400)


@app.post("/api/v1/calendar/authorization")
def start_calendar_authorization(request: Request) -> dict:
    """Begin Nextcloud Login Flow v2 without sending a password to the browser."""
    auth = _calendar_owner(request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    state = ControlState.runtime()
    if state is None:
        return JSONResponse({"ok": False, "error": "calendar state is unavailable"}, status_code=503)
    readiness = _calendar_readiness(state)
    # Login Flow v2 is the live callback used to finish a safely staged owner
    # migration, so migration_required is intentionally permitted here.
    identity_state = state.service_identity("nextcloud") or {}
    if readiness != "ready" and identity_state.get("state") != "migration_required":
        return JSONResponse(_calendar_state_response(readiness), status_code=409)
    try:
        from ctl.nextcloud_calendar import CalendarError, start_authorization
        return JSONResponse(start_authorization(owner_uid), status_code=202)
    except CalendarError as exc:
        return JSONResponse({"ok": False, "state": exc.state, "error": str(exc)}, status_code=400)


@app.post("/api/v1/calendar/authorization/{authorization_id}/poll")
def poll_calendar_authorization(authorization_id: str, request: Request) -> dict:
    auth = _calendar_owner(request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    try:
        from ctl.nextcloud_calendar import CalendarError, poll_authorization
        return poll_authorization(owner_uid, authorization_id)
    except CalendarError as exc:
        return JSONResponse({"ok": False, "state": exc.state, "error": str(exc)},
                            status_code=404 if exc.state == "not_found" else 400)


@app.delete("/api/v1/calendar/authorization/{authorization_id}")
def cancel_calendar_authorization(authorization_id: str, request: Request) -> dict:
    auth = _calendar_owner(request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    try:
        from ctl.nextcloud_calendar import CalendarError, cancel_authorization
        cancel_authorization(owner_uid, authorization_id)
        return {"ok": True}
    except CalendarError as exc:
        return JSONResponse({"ok": False, "state": exc.state, "error": str(exc)}, status_code=404)


@app.put("/api/v1/calendar/connection")
async def select_calendar_connection(request: Request) -> dict:
    auth = _calendar_owner(request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    state = ControlState.runtime()
    if state is None:
        return JSONResponse({"ok": False, "error": "calendar state is unavailable"}, status_code=503)
    from ctl.nextcloud_calendar import CalendarError, select
    try:
        payload = await request.json()
        return select(owner_uid, str(payload.get("calendar_id", "")), state)
    except CalendarError as exc:
        return JSONResponse({"ok": False, "state": exc.state, "error": str(exc)}, status_code=400)


@app.delete("/api/v1/calendar/connection")
def delete_calendar_connection(request: Request) -> dict:
    auth = _calendar_owner(request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    from ctl.nextcloud_calendar import disconnect
    return disconnect(owner_uid)


@app.get("/api/v1/calendar/events")
def calendar_events(request: Request) -> dict:
    auth = _calendar_owner(request)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    state = ControlState.runtime()
    if state is None:
        return JSONResponse({"ok": False, "state": "unavailable", "error": "calendar state is unavailable"}, status_code=503)
    readiness = _calendar_readiness(state)
    if readiness != "ready":
        from ctl.nextcloud_calendar import stale_events
        cached = stale_events(owner_uid)
        if cached:
            return cached
        return {"ok": True, "state": readiness, "events": [],
                "error": _calendar_state_response(readiness)["error"]}
    try:
        from ctl.nextcloud_calendar import CalendarError, events, stale_events
        start_value = request.query_params.get("start", "").strip()
        end_value = request.query_params.get("end", "").strip()
        if bool(start_value) != bool(end_value):
            return JSONResponse({"ok": False, "state": "invalid_range",
                                 "error": "Calendar start and end must be supplied together."}, status_code=400)
        try:
            start = datetime.fromisoformat(start_value.replace("Z", "+00:00")) if start_value else None
            end = datetime.fromisoformat(end_value.replace("Z", "+00:00")) if end_value else None
            if start and start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            if end and end.tzinfo is None:
                end = end.replace(tzinfo=UTC)
            limit = int(request.query_params.get("limit", "5"))
        except ValueError:
            return JSONResponse({"ok": False, "state": "invalid_range",
                                 "error": "Calendar range values must be valid ISO 8601 datetimes."}, status_code=400)
        return events(owner_uid, start=start, end=end, limit=limit)
    except CalendarError as exc:
        if exc.state == "unavailable":
            cached = stale_events(owner_uid)
            if cached:
                return cached
        return JSONResponse({"ok": False, "state": exc.state, "error": str(exc)},
                            status_code=401 if exc.state == "authentication_expired" else 503)


@app.post("/api/v1/calendar/events")
async def create_calendar_event(request: Request) -> dict:
    auth = _calendar_owner(request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    try:
        from ctl.nextcloud_calendar import CalendarError, create_event
        return create_event(owner_uid, await request.json())
    except CalendarError as exc:
        return JSONResponse({"ok": False, "state": exc.state, "error": str(exc)},
                            status_code=409 if exc.state == "event_changed" else 400)


@app.put("/api/v1/calendar/events/{event_id}")
async def update_calendar_event(event_id: str, request: Request) -> dict:
    auth = _calendar_owner(request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    try:
        from ctl.nextcloud_calendar import CalendarError, update_event
        return update_event(owner_uid, event_id, await request.json())
    except CalendarError as exc:
        return JSONResponse({"ok": False, "state": exc.state, "error": str(exc)},
                            status_code=409 if exc.state == "event_changed" else 400)


@app.delete("/api/v1/calendar/events/{event_id}")
async def delete_calendar_event(event_id: str, request: Request) -> dict:
    auth = _calendar_owner(request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    _, owner_uid = auth
    try:
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        from ctl.nextcloud_calendar import CalendarError, delete_event
        return delete_event(owner_uid, event_id, str(payload.get("revision", "")))
    except CalendarError as exc:
        return JSONResponse({"ok": False, "state": exc.state, "error": str(exc)},
                            status_code=409 if exc.state == "event_changed" else 400)


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
    if provisioning_store:
        provisioning_store.reconcile_runtime()
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
                "error_code": (item["last_error"] or {}).get("code", ""),
                "recommended_action": (item["last_error"] or {}).get("recommended_action", ""),
                "routed_via": (item["last_error"] or {}).get("routed_via", ""),
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
    if identity_data.get("subject_id") and identity_data.get("email"):
        try:
            workflow_secrets.save_job_identity(
                str(job["id"]), owner_uid=str(identity_data["subject_id"]),
                email=str(identity_data["email"]), username=str(identity_data["username"]),
                display_name=str(identity_data.get("display_name") or identity_data["username"]))
        except workflow_secrets.WorkflowSecretError as exc:
            store.transition(str(job["id"]), "failed", actor=identity_data["username"],
                             detail="Encrypted account-bootstrap storage is unavailable.",
                             error_code="bootstrap_contract_unavailable",
                             step_id="account_preflight")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return {"ok": True, "job": job}


@app.post("/api/v1/jobs/core-verify")
def verify_core(request: Request) -> dict:
    """Queue live contract checks without reinstalling healthy services."""
    identity_data = identity(request)
    if not identity_data["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    if not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "same-origin CSRF verification failed"}, status_code=403)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    active = next((job for job in store.jobs() if job["service_id"] == "core-suite"
                   and job["state"] in {"queued", "running"}), None)
    if active:
        return JSONResponse({"ok": False, "error": "core work is already running", "job": active}, status_code=409)
    from ctl.core_setup import start_verify
    job = start_verify(store, identity_data["username"],
                       request.headers.get("idempotency-key") or None)
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
    if (action in {"install", "retry_setup"}
            and service.account.get("mode") == "environment_bootstrap"
            and (not identity_data.get("subject_id") or not identity_data.get("email"))):
        return JSONResponse({"ok": False, "error": {
            "code": "identity_email_missing", "stage": "account_preflight",
            "message": "A verified Authentik email is required. Sign out, sign back in, and retry.",
            "retryable": True,
            "recommended_action": "Refresh the Authentik session and retry installation.",
        }}, status_code=409)
    if action == "stop" and service.lifecycle == "always_on":
        return JSONResponse({"ok": False, "error": "always-on infrastructure cannot be stopped"}, status_code=409)
    if not (service.compose_path(ROOT) / "docker-compose.yml").is_file():
        return JSONResponse({"ok": False, "error": "service manifest is not deployable"}, status_code=409)
    current = service_status(service, tailnet_dns_name(), ROOT)
    control_state = ControlState.runtime()
    installed = control_state.installation(service.id) if control_state else None
    current_state = str(current["state"])
    if installed and str(installed["state"]) in {"queued", "installing", "starting", "verifying", "config_required"}:
        current_state = str(installed["state"])
    elif installed and str(installed["state"]) == "failed" and current_state not in {"ready", "running", "starting", "stopped", "needs_setup"}:
        current_state = "failed"
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
    if action in {"install", "retry_setup"} and identity_data.get("subject_id") and identity_data.get("email"):
        try:
            workflow_secrets.save_job_identity(
                str(job["id"]), owner_uid=str(identity_data["subject_id"]),
                email=str(identity_data["email"]), username=str(identity_data["username"]),
                display_name=str(identity_data.get("display_name") or identity_data["username"]))
        except workflow_secrets.WorkflowSecretError as exc:
            store.transition(str(job["id"]), "failed", actor=identity_data["username"],
                             detail="Encrypted account-bootstrap storage is unavailable.",
                             error_code="bootstrap_contract_unavailable",
                             step_id="account_preflight")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return {"ok": True, "job": job}


@app.post("/api/v1/services/{service_id}/identity/reconcile")
def reconcile_service_identity(service_id: str, request: Request) -> dict:
    """Queue bounded identity configuration without disguising it as repair."""
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    if not identity_data.get("subject_id") or not identity_data.get("email"):
        return JSONResponse({"ok": False, "error": "A verified Authentik subject and email are required."}, status_code=409)
    try:
        service = load_registry().get(service_id)
    except RegistryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    if service.is_blocked:
        return JSONResponse({"ok": False, "error": service.blocked_reason}, status_code=409)
    store = JobStore.runtime()
    control = ControlState.runtime()
    if store is None or control is None:
        return JSONResponse({"ok": False, "error": "runtime state is unavailable"}, status_code=503)
    active = next((job for job in store.jobs() if job["service_id"] == service_id
                   and job["action"] == "configure_identity"
                   and job["state"] in {"queued", "running"}), None)
    if active:
        return {"ok": True, "duplicate": True, "job": active}
    job = store.create(kind="lifecycle", service_id=service_id, action="configure_identity",
                       actor=str(identity_data["username"]),
                       detail=f"Operator requested sign-in reconciliation for {service.name}.",
                       idempotency_key=request.headers.get("idempotency-key") or None)
    try:
        workflow_secrets.save_job_identity(
            str(job["id"]), owner_uid=str(identity_data["subject_id"]),
            email=str(identity_data["email"]), username=str(identity_data["username"]),
            display_name=str(identity_data.get("display_name") or identity_data["username"]))
    except workflow_secrets.WorkflowSecretError as exc:
        store.transition(str(job["id"]), "failed", actor=str(identity_data["username"]),
                         detail="Encrypted identity handoff storage is unavailable.",
                         error_code="identity_contract_unavailable", step_id="identity_preflight")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    from ctl.identity import mode_for
    control.set_service_identity(service_id, mode_for(service), "configuring",
                                 owner_uid=str(identity_data["subject_id"]),
                                 job_id=str(job["id"]), detail="Sign-in reconciliation is queued.")
    return {"ok": True, "job": job}


@app.post("/api/v1/services/install-batch")
async def create_install_batch(request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    if not identity_data.get("subject_id") or not identity_data.get("email"):
        return JSONResponse({"ok": False, "error": "A verified Authentik UID and email are required."},
                            status_code=409)
    try:
        payload = await request.json()
        service_ids = payload.get("service_ids", [])
        if not isinstance(service_ids, list) or not all(isinstance(item, str) for item in service_ids):
            raise ValueError("service_ids must be a list of curated application IDs")
        batches = InstallBatchStore.runtime()
        jobs_store = JobStore.runtime()
        control = ControlState.runtime()
        if not batches or not jobs_store or not control:
            raise RuntimeError("runtime state is not initialized")
        result = batches.create(
            load_registry(), service_ids, actor=str(identity_data["username"]),
            owner_uid=str(identity_data["subject_id"]),
            identity={"owner_uid": str(identity_data["subject_id"]),
                      "email": str(identity_data["email"]),
                      "username": str(identity_data["username"]),
                      "display_name": str(identity_data.get("display_name") or identity_data["username"])},
            idempotency_key=request.headers.get("idempotency-key", ""),
            jobs=jobs_store, control=control)
        return {"ok": True, "batch": result}
    except (ValueError, RegistryError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


def _install_batch_view(batch: dict | None) -> dict:
    """Add the current child-job state and redacted tail to the browser view.

    InstallBatchStore intentionally keeps its persistence shape small for the
    worker.  The control-plane projection can safely enrich it with the live
    child job without exposing job paths, identities, or unredacted output.
    """
    if not batch:
        return {"batch": None, "current_job": None}
    current_item = next(
        (item for item in batch.get("items", [])
         if int(item.get("ordinal", -1)) == int(batch.get("current_ordinal", -1))
         and item.get("job_id")),
        None,
    )
    if current_item is None:
        current_item = next(
            (item for item in batch.get("items", [])
             if item.get("state") in {"queued", "running"} and item.get("job_id")),
            None,
        )
    current_job = None
    jobs_store = JobStore.runtime()
    if current_item and jobs_store:
        job_id = str(current_item["job_id"])
        job = jobs_store.get(job_id)
        if job:
            current_job = {
                "id": job_id,
                "state": str(job.get("state") or "queued"),
                "step_id": str(job.get("step_id") or ""),
                "detail": str(job.get("detail") or ""),
                "events": jobs_store.events(job_id, limit=20),
            }
    return {"batch": batch, "current_job": current_job}


@app.get("/api/v1/service-install-batches/{batch_id}")
def get_install_batch(batch_id: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    batches = InstallBatchStore.runtime()
    batch = batches.get(batch_id) if batches else None
    if not batch or batch["owner_uid"] != identity_data.get("subject_id"):
        return JSONResponse({"ok": False, "error": "batch not found"}, status_code=404)
    return {"ok": True, **_install_batch_view(batch)}


@app.get("/api/v1/service-install-batches")
def latest_install_batch(request: Request) -> dict:
    identity_data = identity(request)
    owner_uid = str(identity_data.get("subject_id") or "")
    if not identity_data["writes_enabled"] or not owner_uid:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    batches = InstallBatchStore.runtime()
    return {"ok": True, **_install_batch_view(batches.latest(owner_uid) if batches else None)}


@app.post("/api/v1/service-install-batches/{batch_id}/resume")
def resume_install_batch(batch_id: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    batches, jobs_store = InstallBatchStore.runtime(), JobStore.runtime()
    batch = batches.get(batch_id) if batches else None
    if not batch or batch["owner_uid"] != identity_data.get("subject_id"):
        return JSONResponse({"ok": False, "error": "batch not found"}, status_code=404)
    try:
        return {"ok": True, "batch": batches.resume(batch_id, jobs_store)}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)


@app.post("/api/v1/service-install-batches/{batch_id}/cancel")
def cancel_install_batch(batch_id: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    batches = InstallBatchStore.runtime()
    batch = batches.get(batch_id) if batches else None
    if not batch or batch["owner_uid"] != identity_data.get("subject_id"):
        return JSONResponse({"ok": False, "error": "batch not found"}, status_code=404)
    jobs_store = JobStore.runtime()
    if not jobs_store or not batches.cancel(batch_id, jobs_store):
        return JSONResponse({"ok": False, "error": "batch cannot be cancelled"}, status_code=409)
    return {"ok": True, "batch": batches.get(batch_id)}


@app.post("/api/v1/service-install-batches/{batch_id}/reset")
def reset_install_batch(batch_id: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    batches, jobs_store = InstallBatchStore.runtime(), JobStore.runtime()
    batch = batches.get(batch_id) if batches else None
    if not batch or batch["owner_uid"] != identity_data.get("subject_id"):
        return JSONResponse({"ok": False, "error": "batch not found"}, status_code=404)
    control = ControlState.runtime()
    if not jobs_store or not control or not batches:
        return JSONResponse({"ok": False, "error": "runtime state is unavailable"}, status_code=503)
    if batch["state"] not in {"paused", "cancelled", "completed_with_failures", "reset_failed"}:
        return JSONResponse({"ok": False, "error": "batch is not resettable"}, status_code=409)

    # A reset must never race an active worker.  Cleanup is deliberately
    # queued for the durable worker rather than running Docker inside this
    # request; browser connections can expire while Compose is stopping an
    # unhealthy stack.
    active = {(str(job.get("service_id")), str(job.get("action")))
              for job in jobs_store.jobs(limit=100)
              if job.get("state") in {"queued", "running", "waiting_for_confirmation"}}
    for item in batch["items"]:
        if item["state"] in {"failed", "cancelled"} and any(
                service_id == str(item["service_id"]) for service_id, _action in active):
            return JSONResponse({"ok": False,
                                 "error": f"{item['service_id']} still has an active operation"},
                                status_code=409)

    try:
        reset_batch = batches.begin_reset(batch_id, jobs_store)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return JSONResponse({"ok": True, "reset": True, "batch": reset_batch,
                         "message": "Cleanup has been queued. Persistent data will be preserved."},
                        status_code=202)


@app.get("/api/v1/credential-handoffs")
def credential_handoffs(request: Request) -> dict:
    identity_data = identity(request)
    owner_uid = str(identity_data.get("subject_id") or "")
    if not identity_data["writes_enabled"] or not owner_uid:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    state = ControlState.runtime()
    expired = workflow_secrets.cleanup()
    if state:
        state.expire_handoffs(expired)
    secret_metadata = {item["id"]: item for item in workflow_secrets.metadata(owner_uid)}
    records = []
    for item in state.handoffs(owner_uid) if state else []:
        if item["state"] == "available" and item["id"] in secret_metadata:
            records.append({**item, **secret_metadata[item["id"]]})
    return {"ok": True, "handoffs": records}


def _sensitive_response(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers={
        "Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
    })


@app.post("/api/v1/credential-handoffs/{handoff_id}/reveal")
def reveal_credential(handoff_id: str, request: Request) -> dict:
    identity_data = identity(request)
    owner_uid = str(identity_data.get("subject_id") or "")
    if not identity_data["writes_enabled"] or not owner_uid or not _mutation_allowed(request):
        return _sensitive_response({"ok": False, "error": "operator mutation verification failed"}, 403)
    credential = workflow_secrets.reveal(handoff_id, owner_uid)
    if not credential:
        return _sensitive_response({"ok": False, "error": "credential is unavailable or expired"}, 404)
    return _sensitive_response({"ok": True, "credential": credential})


@app.post("/api/v1/credential-handoffs/{handoff_id}/confirm")
def confirm_credential(handoff_id: str, request: Request) -> dict:
    identity_data = identity(request)
    owner_uid = str(identity_data.get("subject_id") or "")
    if not identity_data["writes_enabled"] or not owner_uid or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    state = ControlState.runtime()
    if not state or not state.confirm_handoff(handoff_id, owner_uid):
        return JSONResponse({"ok": False, "error": "credential handoff not found"}, status_code=404)
    workflow_secrets.delete(handoff_id, owner_uid)
    state_store = JobStore.runtime()
    if state_store:
        state_store.record_audit(actor=str(identity_data["username"]),
                                 event="credential_handoff.confirmed",
                                 detail="Generated application credential was confirmed saved and removed.")
    return {"ok": True, "state": "confirmed"}


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


@app.post("/api/v1/services/{service_id}/initialization/confirm")
def confirm_service_initialization(service_id: str, request: Request) -> dict:
    identity_data = identity(request)
    if not identity_data["writes_enabled"] or not _mutation_allowed(request):
        return JSONResponse({"ok": False, "error": "operator mutation verification failed"}, status_code=403)
    try:
        service = load_registry().get(service_id)
    except RegistryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    mode = str(service.account.get("mode", "none"))
    if mode not in {"oidc_first_login", "browser_registration", "local_account_manual", "manual_owner", "trusted_header"}:
        return JSONResponse({"ok": False, "error": "this application does not use manual initialization"},
                            status_code=409)
    control = ControlState.runtime()
    if not control:
        return JSONResponse({"ok": False, "error": "runtime state is not initialized"}, status_code=503)
    installation = control.installation(service.id)
    if not installation or installation["state"] not in {"running", "stopped", "degraded"}:
        return JSONResponse({"ok": False, "error": "install the application before confirming setup"},
                            status_code=409)
    result = control.set_initialization(service.id, mode, "ready",
                                        job_id=str(installation.get("last_job_id", "")),
                                        owner_uid=str(identity_data.get("subject_id") or ""))
    store = JobStore.runtime()
    if store:
        store.record_audit(actor=str(identity_data["username"]),
                           event="service.initialization.confirmed",
                           detail=f"Operator confirmed supported first-user setup for {service.id}.")
    if service.id == "open-webui":
        provisioning_store = ProvisioningStore.runtime()
        if provisioning_store:
            try:
                provisioning_store.update("open_webui_admin", "verified",
                                          detail="The operator confirmed the first Open WebUI administrator session.")
            except ValueError:
                pass
    return {"ok": True, "initialization": result}


@app.get("/api/v1/services/{service_id}/configuration")
def get_service_configuration(service_id: str, request: Request) -> dict:
    if not identity(request)["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    try:
        service = load_registry().get(service_id)
    except RegistryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    managed = {str(field["key"]) for field in service.configuration if field.get("managed")}
    return {"ok": True, "service_id": service.id,
            "fields": [field for field in service_config.read(service)
                       if str(field["key"]) not in managed]}


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
        values = payload.get("values", {})
        managed = {str(field["key"]) for field in service.configuration if field.get("managed")}
        if isinstance(values, dict) and set(values).intersection(managed):
            return JSONResponse({"ok": False, "error": "managed account fields cannot be changed here"},
                                status_code=422)
        fields = [field for field in service_config.write(service, values)
                  if str(field["key"]) not in managed]
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
