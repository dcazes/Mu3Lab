"""Mu3Lab :: ctl/app.py

WHAT: Real-dashboard backend (port 8787). Minimal milestone: GET /api/health,
      GET /api/status, plus static serving of dashboard/dist/ with SPA
      fallback. No preflight/infra routes here — those live behind the check
      dashboard until later phases merge the two.
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

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ctl import __version__  # noqa: F401 (re-exported for /api/health)
from ctl.backups import readiness as backup_readiness
from ctl.registry import RegistryError, load as load_registry
from ctl.runtime import RuntimePaths
from ctl.service_state import status as service_status, tailnet_dns_name

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dashboard" / "dist"

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


@app.get("/api/backups")
def backups() -> dict:
    """Return local encrypted-backup readiness; execution needs an authenticated job."""
    return {"ok": True, **backup_readiness()}


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
