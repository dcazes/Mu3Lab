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

import psutil
import yaml

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ctl import __version__  # noqa: F401 (re-exported for /api/health)
from ctl.backups import readiness as backup_readiness
from ctl.core_setup import CORE_ORDER, plan as core_plan, start as start_core_setup
from ctl.jobs import JobStore
from ctl.registry import RegistryError, load as load_registry
from ctl.runtime import RuntimePaths
from ctl.service_state import status as service_status, tailnet_dns_name

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dashboard" / "dist"
CATALOG = ROOT / "catalog.yaml"

app = FastAPI(title="Mu3Lab control plane", version=__version__)


@app.get("/api/health")
def health() -> dict:
    """Liveness probe (Caddy, systemd, install verify all use this)."""
    return {"ok": True, "version": __version__}


@app.get("/api/status")
def status() -> dict:
    """Compatibility summary derived from the curated registry, not constants."""
    services = list_services()["services"]
    return {"version": __version__, "components": [
        {"id": item["id"], "label": item["name"], "detail": item["detail"]}
        for item in services
    ]}


@app.get("/api/services")
def list_services() -> dict:
    """Read the curated catalog and project live, non-mutating service health."""
    try:
        registry = load_registry()
    except RegistryError as exc:
        return {"ok": False, "error": str(exc), "services": []}
    dns_name = tailnet_dns_name()
    return {"ok": True, "version": __version__, "tailnet_dns_name": dns_name,
            "runtime": RuntimePaths().as_dict(),
            "services": [service_status(service, dns_name, ROOT)
                         for service in registry.services]}


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
    """Return the checked-in, curated dashboard catalog without host mutation."""
    try:
        raw = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {"ok": False, "error": str(exc), "profiles": [], "services": {}}
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return {"ok": False, "error": "catalog schema is invalid", "profiles": [], "services": {}}
    return {"ok": True, "profiles": raw.get("profiles", []),
            "services": raw.get("services", {})}


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


@app.get("/api/backups")
def backups() -> dict:
    """Return local encrypted-backup readiness; execution needs an authenticated job."""
    return {"ok": True, **backup_readiness()}


@app.get("/api/identity")
def identity(request: Request) -> dict:
    """Report Authentik identity headers without trusting browser claims."""
    username = request.headers.get("x-authentik-username", "").strip()
    groups = tuple(value.strip() for value in request.headers.get("x-authentik-groups", "").split(",") if value.strip())
    authenticated = bool(username)
    operator = authenticated and "mu3lab-operators" in groups
    return {
        "ok": True,
        "control_plane_auth": "authentik_forward_auth" if authenticated else "not_configured",
        "username": username,
        "groups": list(groups),
        "detail": ("Authenticated through Authentik." if operator else
                   "Tailnet access is private, but Authentik protection and operator role mapping are not configured yet."),
        "writes_enabled": operator,
    }


@app.get("/api/setup/core")
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
    return {"ok": True, "ready_to_run": execution["ready"],
            "services": list(CORE_ORDER),
            "missing_manifests": execution["missing"],
            "current_job": current_job,
            "next_action": "Set up the core application suite" if execution["ready"] else "Core service manifests are still being prepared"}


@app.get("/api/connections/providers")
def provider_metadata(request: Request) -> dict:
    """List provider labels only; encrypted keys never cross this boundary."""
    identity_data = identity(request)
    if not identity_data["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    try:
        from ctl.provider_secrets import metadata
        return {"ok": True, "providers": metadata()}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


@app.post("/api/connections/providers")
async def save_provider(request: Request) -> dict:
    """Accept one provider key without ever echoing or logging its value."""
    identity_data = identity(request)
    if not identity_data["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    try:
        payload = await request.json()
        provider_id = str(payload.get("provider_id", ""))
        label = str(payload.get("label", ""))
        api_key = str(payload.get("api_key", ""))
        from ctl.provider_secrets import save
        result = save(provider_id, label, api_key)
    except (ValueError, TypeError, AttributeError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    audit_job = store.create(kind="wiring", service_id="provider-accounts",
                             action="save", actor=identity_data["username"],
                             detail=f"provider:{result['id']}")
    store.transition(audit_job["id"], "succeeded", actor=identity_data["username"],
                     detail="Provider credential stored in encrypted local storage.")
    return {"ok": True, "provider": result}


@app.post("/api/setup/core")
def start_core(request: Request) -> dict:
    """Start the one guided core-suite job for an Authentik operator."""
    identity_data = identity(request)
    if not identity_data["writes_enabled"]:
        return JSONResponse({"ok": False, "error": "operator identity required"}, status_code=403)
    execution = core_plan(ROOT)
    if not execution["ready"]:
        return JSONResponse({"ok": False, "error": execution["error"],
                             "missing_manifests": execution["missing"]}, status_code=409)
    store = JobStore.runtime()
    if store is None:
        return JSONResponse({"ok": False, "error": "runtime job store is not initialized"}, status_code=503)
    active = [job for job in store.jobs() if job["service_id"] == "core-suite" and job["state"] in {"queued", "running"}]
    if active:
        return JSONResponse({"ok": False, "error": "core setup is already running", "job": active[0]}, status_code=409)
    job = start_core_setup(store, identity_data["username"], ROOT)
    return {"ok": True, "job": job}


@app.get("/api/jobs")
def jobs() -> dict:
    """Read durable job state. No job creation endpoint exists before identity enforcement."""
    store = JobStore.runtime()
    if store is None:
        return {"ok": True, "available": False, "jobs": []}
    return {"ok": True, "available": True, "jobs": store.jobs()}


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
