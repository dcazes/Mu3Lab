"""The flat ``Service`` view of the app catalog that existing lifecycle code reads.

The truth lives in ``apps/<id>/app.yaml`` (see ``ctl.manifest``). This module
projects each manifest into the older flat shape so lifecycle code can move to
the manifest one module at a time; it adds no facts of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ctl.manifest import catalog as manifest_catalog
from ctl.manifest.models import AppManifest

ROOT = Path(__file__).resolve().parent.parent

# Core apps that use the catalog-app installer rather than the core executor.
# Goes away when both run through one lifecycle engine.
APP_INSTALLER_CORE = frozenset({"firecrawl"})

_AUTH = {"oidc": "oidc", "gate": "proxy", "trusted_header": "trusted_header", "local": "local", "none": "excluded"}


class RegistryError(ValueError):
    """Raised when an app is unknown or the catalog is malformed."""


@dataclass(frozen=True, eq=False)
class Service:
    """One app, flattened for the lifecycle, status and API code."""

    manifest: AppManifest = field(repr=False)
    id: str
    name: str
    category: str
    group: str
    lifecycle: str
    stage: str
    compose_dir: str
    https_port: int
    private_https_port: int | None
    proxy_port: int | None
    health: dict[str, Any]
    auth: str
    dependencies: tuple[str, ...]
    summary: str
    tagline: str
    identity_note: str
    resource_guidance: str
    setup_action: str
    update: dict[str, str]
    configuration: tuple[dict[str, Any], ...]
    account: dict[str, Any]
    ui: dict[str, Any]
    mobile: dict[str, Any]
    uses_gpu: bool

    @property
    def route(self) -> str:
        """ "ready" when the installer always publishes this address (the platform routes)."""
        route = self.manifest.route
        return "ready" if route and not route.generated else "pending"

    @property
    def required(self) -> bool:
        return self.manifest.tier != "optional"

    def compose_path(self, root: Path = ROOT) -> Path:
        return root / self.compose_dir

    def public(self) -> dict[str, Any]:
        """Browser-safe metadata; never paths or secrets."""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "group": self.group,
            "lifecycle": self.lifecycle,
            "https_port": self.https_port,
            "private_https_port": self.private_https_port,
            "proxy_port": self.proxy_port,
            "auth": self.auth,
            "dependencies": list(self.dependencies),
            "stage": self.stage,
            "routable": self.private_https_port is not None,
            "required": self.required,
            "summary": self.summary,
            "tagline": self.tagline,
            "identity_note": self.identity_note,
            "resource_guidance": self.resource_guidance,
            "setup_action": self.setup_action,
            "update": dict(self.update),
            "account": {
                "mode": str(self.account.get("mode", "none")),
                "user_action": str(self.account.get("user_action", "")),
            },
            "ui": {
                "available": bool(self.ui.get("available", False)),
                "path": str(self.ui.get("path", "")),
                "authentication": str(self.ui.get("authentication", self.auth)),
                "unavailable_reason": str(self.ui.get("unavailable_reason", "")),
                "label": str(self.ui.get("label", "Open")),
            },
            "mobile": self.mobile,
            "configuration": [
                {key: value for key, value in item.items() if key != "env"} for item in self.configuration
            ],
        }


def _lifecycle(manifest: AppManifest) -> str:
    if not manifest.service.stoppable or manifest.tier == "foundation":
        return "always_on"
    return "shared" if manifest.tier == "core" else "optional"


def _health(manifest: AppManifest) -> dict[str, Any]:
    health = manifest.service.health
    port = health.port or manifest.service.local_port
    if health.kind == "tcp":
        return {"kind": "tcp", "port": port}
    return {"kind": "http", "url": f"http://127.0.0.1:{port}{health.path}"}


def service_from(manifest: AppManifest) -> Service:
    route = manifest.route
    stage = "optional" if manifest.id in APP_INSTALLER_CORE else manifest.tier
    return Service(
        manifest=manifest,
        id=manifest.id,
        name=manifest.name,
        category=manifest.category,
        group=manifest.group,
        lifecycle=_lifecycle(manifest),
        stage=stage,
        compose_dir=f"apps/{manifest.id}",
        https_port=manifest.service.local_port,
        private_https_port=route.https_port if route else None,
        proxy_port=route.caddy_port if route else None,
        health=_health(manifest),
        auth=_AUTH[manifest.sign_in.method],
        dependencies=manifest.depends_on,
        summary=manifest.summary,
        tagline=manifest.tagline,
        identity_note=manifest.sign_in.note,
        resource_guidance=manifest.resource_guidance,
        setup_action=manifest.setup_action,
        update={"repository": manifest.upstream, "approved_version": manifest.version},
        configuration=tuple(item.model_dump(exclude_defaults=False) for item in manifest.configuration),
        account=manifest.account.model_dump(),
        ui={**manifest.ui.model_dump(), "authentication": _AUTH[manifest.sign_in.method]},
        mobile=manifest.mobile.model_dump(mode="json") if manifest.mobile else {},
        uses_gpu=manifest.service.uses_gpu,
    )


class Registry:
    """Read-only lookups over the catalog, in folder order."""

    def __init__(self, services: tuple[Service, ...], catalog: manifest_catalog.Catalog) -> None:
        self.services = services
        self.catalog = catalog
        self._by_id = {service.id: service for service in services}

    def get(self, service_id: str) -> Service:
        try:
            return self._by_id[service_id]
        except KeyError as exc:
            raise RegistryError(f"unknown curated service {service_id!r}") from exc

    def public(self) -> list[dict[str, Any]]:
        return [service.public() for service in self.services]


def load(apps_dir: Path = manifest_catalog.APPS_DIR) -> Registry:
    """The catalog as flat services; raises RegistryError when any manifest is invalid."""
    try:
        catalog = manifest_catalog.cached(apps_dir)
    except (manifest_catalog.CatalogError, OSError) as exc:
        raise RegistryError(str(exc)) from exc
    order = {"foundation": 0, "core": 1, "optional": 2}
    apps = sorted(catalog.apps, key=lambda app: (order[app.manifest.tier], app.id))
    return Registry(tuple(service_from(app.manifest) for app in apps), catalog)
