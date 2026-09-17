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

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dashboard" / "dist"

app = FastAPI(title="Mu3Lab control plane", version=__version__)


@app.get("/api/health")
def health() -> dict:
    """Liveness probe (Caddy, systemd, install verify all use this)."""
    return {"ok": True, "version": __version__}


@app.get("/api/status")
def status() -> dict:
    """Installed-infrastructure summary for the status screen.

    Minimal milestone: static component list. Later phases replace `detail`
    with live probes (docker ps, compose state) — the SHAPE stays.
    """
    return {
        "version": __version__,
        "components": [
            {"id": "dashboard", "label": "Dashboard",
             "detail": "this page, served locally"},
            {"id": "caddy", "label": "Caddy",
             "detail": "local entry point on :19460"},
            {"id": "docker", "label": "Docker",
             "detail": "container runtime + shared networks"},
            {"id": "tailscale", "label": "Tailscale",
             "detail": "tailnet access for your other devices"},
        ],
    }


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
