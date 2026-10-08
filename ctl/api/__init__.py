"""Control-plane HTTP API: application factory and router wiring."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from ctl import __version__
from ctl.api.errors import (
    ApiError,
    api_error_handler,
    job_conflict_handler,
    response_error_handler,
    validation_error_handler,
)
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
    snapshot,
    system,
    vault,
    voice,
)
from ctl.identity import sync_sign_in
from ctl.integrations.authentik import Authentik, AuthentikError
from ctl.jobs import JobConflict, redact
from ctl.provisioning import ProvisioningStore
from ctl.registry import RegistryError
from ctl.registry import load as load_registry
from ctl.service_state import tailnet_dns_name

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dashboard" / "dist"


def _reconcile_runtime_state() -> None:
    """Recover durable milestones and sign-in settings through Authentik on startup."""
    store = ProvisioningStore.runtime()
    if store:
        store.reconcile_runtime()
    try:
        sync_sign_in(load_registry().catalog, tailnet_dns_name(), Authentik.runtime())
    except (AuthentikError, OSError, RegistryError, ValueError) as exc:
        logging.getLogger(__name__).warning("Sign-in reconciliation deferred: %s", redact(str(exc)))


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
    app.add_exception_handler(JobConflict, job_conflict_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(ResponseValidationError, response_error_handler)
    app.add_exception_handler(ValidationError, response_error_handler)
    app.include_router(system.health_router)
    for module in (
        system,
        identity,
        services,
        install_batches,
        jobs,
        providers,
        vault,
        calendar,
        mcp,
        chat,
        people,
        voice,
    ):
        app.include_router(module.router)
    app.include_router(snapshot.router)
    if dist.is_dir():
        _mount_dashboard(app, dist)
    return app
