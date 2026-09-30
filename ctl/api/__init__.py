"""Control-plane HTTP API: application factory and router wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ctl import __version__
from ctl.api.errors import ApiError, api_error_handler
from ctl.api.routes import (
    calendar,
    chat,
    identity,
    install_batches,
    jobs,
    mcp,
    people,
    providers,
    services,
    system,
    vault,
)

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dashboard" / "dist"


def _reconcile_runtime_state() -> None:
    """Refresh durable milestones and recover missing OIDC blueprints at startup.

    File-only reconciliation: no image pull, Compose recreation, account
    creation, or credential rotation happens here.
    """
    from ctl.identity import reconcile_blueprints
    from ctl.provisioning import ProvisioningStore
    from ctl.registry import RegistryError
    from ctl.registry import load as load_registry
    from ctl.service_state import tailnet_dns_name

    store = ProvisioningStore.runtime()
    if store:
        store.reconcile_runtime()
    try:
        reconcile_blueprints(load_registry(), tailnet_dns_name())
    except (OSError, RegistryError, ValueError):
        pass


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _reconcile_runtime_state()
    yield


_ROOT_FILE_TYPES = {".webmanifest": "application/manifest+json", ".js": "text/javascript"}


def _mount_dashboard(app: FastAPI, dist: Path) -> None:
    """Serve the built React bundle; unknown /api paths stay JSON 404s."""
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        if path.startswith("api/"):
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        # Real files from the build root (manifest, service worker, icons), never outside it.
        candidate = (root / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:
            headers = {"Cache-Control": "no-cache"} if candidate.name == "sw.js" else None
            return FileResponse(candidate, media_type=_ROOT_FILE_TYPES.get(candidate.suffix), headers=headers)
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})


def create_app(dist: Path = DIST) -> FastAPI:
    app = FastAPI(title="Mu3Lab control plane", version=__version__, lifespan=_lifespan)
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(system.health_router)
    for module in (system, identity, services, install_batches, jobs, providers, vault, calendar, mcp, chat, people):
        app.include_router(module.router)
    if dist.is_dir():
        _mount_dashboard(app, dist)
    return app
