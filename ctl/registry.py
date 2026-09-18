"""Mu3Lab :: ctl/registry.py

WHAT: Validated access to the curated services.yaml deployment registry.
WHY: Dashboard, jobs, backup, and lifecycle operations must agree on one safe
     service definition instead of accepting arbitrary Compose paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "services.yaml"
VALID_AUTH = frozenset({"oidc", "proxy", "local", "excluded"})
VALID_LIFECYCLE = frozenset({"always_on", "shared", "optional"})
VALID_ACTIONS = frozenset({"start", "stop", "restart", "update"})
VALID_STAGES = frozenset({"foundation", "core", "optional", "blocked"})
VALID_ROUTES = frozenset({"ready", "pending", "unavailable"})
VALID_MATURITY = frozenset({"supported", "experimental", "planned"})


class RegistryError(ValueError):
    """Raised when curated deployment metadata is malformed or unsafe."""


@dataclass(frozen=True)
class Service:
    """One curated, non-user-supplied Compose service definition."""

    id: str
    maturity: str
    name: str
    category: str
    lifecycle: str
    compose_dir: str
    https_port: int
    private_https_port: int | None
    health: dict[str, Any]
    auth: str
    profiles: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    availability: str = "available"
    blocked_reason: str = ""
    backup: dict[str, Any] = field(default_factory=dict)
    mcp: dict[str, Any] = field(default_factory=dict)
    stage: str = "planned"
    images: tuple[str, ...] = ()
    route: str = "pending"
    required: bool = False
    identity_note: str = ""
    resource_guidance: str = ""
    setup_action: str = ""

    @property
    def is_blocked(self) -> bool:
        """True when policy deliberately withholds the service from install."""
        return self.availability == "blocked"

    def compose_path(self, root: Path = ROOT) -> Path:
        """Return a checkout-contained Compose path; reject traversal by schema."""
        path = (root / self.compose_dir).resolve()
        if root.resolve() not in path.parents:
            raise RegistryError(f"service {self.id}: compose_dir escapes checkout")
        return path

    def public(self) -> dict[str, Any]:
        """Return browser-safe metadata with no paths outside the checkout/secrets."""
        return {"id": self.id, "name": self.name, "category": self.category,
                "maturity": self.maturity,
                "lifecycle": self.lifecycle, "https_port": self.https_port,
                "private_https_port": self.private_https_port,
                "auth": self.auth, "profiles": list(self.profiles),
                "dependencies": list(self.dependencies),
                "availability": self.availability,
                "blocked_reason": self.blocked_reason,
                "stage": self.stage, "route": self.route,
                "routable": self.route == "ready",
                "required": self.required, "identity_note": self.identity_note,
                "resource_guidance": self.resource_guidance,
                "setup_action": self.setup_action,
                "mcp": {"exposed": bool(self.mcp.get("exposed", False)),
                        "risk": self.mcp.get("risk", "")}}


def _required(item: dict[str, Any], key: str) -> Any:
    value = item.get(key)
    if value in (None, "", []):
        raise RegistryError(f"service {item.get('id', '<unknown>')}: missing {key}")
    return value


def _service(item: dict[str, Any]) -> Service:
    service_id = _required(item, "id")
    if not isinstance(service_id, str) or not service_id.replace("-", "").isalnum():
        raise RegistryError("service id must contain only letters, digits, and hyphens")
    auth = _required(item, "auth")
    lifecycle = _required(item, "lifecycle")
    if auth not in VALID_AUTH:
        raise RegistryError(f"service {service_id}: unsupported auth mode {auth!r}")
    if lifecycle not in VALID_LIFECYCLE:
        raise RegistryError(f"service {service_id}: unsupported lifecycle {lifecycle!r}")
    port = _required(item, "https_port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise RegistryError(f"service {service_id}: https_port must be a TCP port")
    private_port = item.get("private_https_port")
    if private_port is not None and (not isinstance(private_port, int) or not 1 <= private_port <= 65535):
        raise RegistryError(f"service {service_id}: private_https_port must be a TCP port")
    compose_dir = _required(item, "compose_dir")
    if not isinstance(compose_dir, str) or compose_dir.startswith("/") or ".." in Path(compose_dir).parts:
        raise RegistryError(f"service {service_id}: unsafe compose_dir")
    health = _required(item, "health")
    if not isinstance(health, dict) or health.get("kind") not in {"http", "tcp"}:
        raise RegistryError(f"service {service_id}: health.kind must be http or tcp")
    profiles = tuple(_required(item, "profiles"))
    if not set(profiles).issubset({"cpu", "nvidia", "amd"}):
        raise RegistryError(f"service {service_id}: unsupported hardware profile")
    stage = item.get("stage", "planned")
    maturity = _required(item, "maturity")
    route = item.get("route", "pending")
    if stage not in VALID_STAGES:
        raise RegistryError(f"service {service_id}: unsupported stage {stage!r}")
    if maturity not in VALID_MATURITY:
        raise RegistryError(f"service {service_id}: unsupported maturity {maturity!r}")
    if maturity == "planned" and item.get("availability") != "blocked":
        raise RegistryError(f"service {service_id}: planned maturity must be blocked")
    if route not in VALID_ROUTES:
        raise RegistryError(f"service {service_id}: unsupported route state {route!r}")
    images = tuple(item.get("images", []))
    if stage == "foundation" and not images:
        raise RegistryError(f"service {service_id}: foundation services require reviewed images")
    if not all(isinstance(image, str) and image and ":" in image and ":latest" not in image
               for image in images):
        raise RegistryError(f"service {service_id}: images must be pinned and never use latest")
    if stage == "blocked" and item.get("availability") != "blocked":
        raise RegistryError(f"service {service_id}: blocked stage requires blocked availability")
    return Service(id=service_id, maturity=maturity, name=_required(item, "name"),
                   category=_required(item, "category"), lifecycle=lifecycle,
                   compose_dir=compose_dir, https_port=port, private_https_port=private_port, health=health,
                   auth=auth, profiles=profiles,
                   dependencies=tuple(item.get("dependencies", [])),
                   availability=item.get("availability", "available"),
                   blocked_reason=item.get("blocked_reason", ""),
                   backup=dict(item.get("backup", {})), mcp=dict(item.get("mcp", {})),
                   stage=stage, images=images, route=route,
                   required=bool(item.get("required", False)),
                   identity_note=str(item.get("identity_note", "")),
                   resource_guidance=str(item.get("resource_guidance", "")),
                   setup_action=str(item.get("setup_action", "")))


class Registry:
    """Read-only validated registry for curated service lookups."""

    def __init__(self, services: tuple[Service, ...], settings: dict[str, Any]) -> None:
        self.services = services
        self.settings = settings
        self._by_id = {service.id: service for service in services}
        if len(self._by_id) != len(services):
            raise RegistryError("service ids must be unique")
        for service in services:
            unknown = set(service.dependencies) - self._by_id.keys()
            if unknown:
                raise RegistryError(f"service {service.id}: unknown dependencies {sorted(unknown)}")

    def get(self, service_id: str) -> Service:
        """Find one curated service or raise a safe lookup error."""
        try:
            return self._by_id[service_id]
        except KeyError as exc:
            raise RegistryError(f"unknown curated service {service_id!r}") from exc

    def public(self) -> list[dict[str, Any]]:
        """Return display metadata in source order."""
        return [service.public() for service in self.services]


def load(path: Path = REGISTRY_PATH) -> Registry:
    """Load and validate the checked-in registry; never mutates files or host."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegistryError(f"cannot read registry: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RegistryError(f"invalid registry YAML: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        raise RegistryError("registry schema_version must be 2")
    raw_services = raw.get("services")
    if not isinstance(raw_services, list) or not raw_services:
        raise RegistryError("registry services must be a non-empty list")
    if not all(isinstance(item, dict) for item in raw_services):
        raise RegistryError("registry services entries must be mappings")
    return Registry(tuple(_service(item) for item in raw_services),
                    dict(raw.get("settings", {})))
