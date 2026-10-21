"""Load and cross-check every app folder under ``apps/``.

Each ``apps/<id>/app.yaml`` is validated on its own by the models; this module
checks what only makes sense across apps (known dependencies, and one host
port per listener across apps, connectors, Caddy and Mu3Lab's own services)
and between a manifest and its folder (compose project name, images pinned to
a well-formed digest, published ports matching the manifest, connector folders
and their reviews).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from ctl.manifest.models import AppManifest, Connector

ROOT = Path(__file__).resolve().parents[2]
APPS_DIR = ROOT / "apps"
COMPOSE_FILE = "docker-compose.yml"
PLATFORM_DIR = ROOT / "platform"
# An image pinned to its content: name[:tag]@sha256:<64 hex>.
_PINNED = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")
# Listeners outside any compose file.
RESERVED_PORTS = {8787: "Mu3Lab's dashboard"}


class CatalogError(ValueError):
    """A manifest, connector or app folder is malformed or inconsistent."""


@dataclass(frozen=True)
class App:
    """One app: its validated manifest, its folder and its reviewed connectors."""

    manifest: AppManifest
    folder: Path
    connectors: tuple[Connector, ...] = ()

    @property
    def id(self) -> str:
        return self.manifest.id

    def connector_folder(self, connector_id: str) -> Path:
        return self.folder / "connectors" / connector_id


@dataclass(frozen=True)
class Catalog:
    apps: tuple[App, ...]
    _by_id: dict[str, App] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        self._by_id.update({app.id: app for app in self.apps})

    def get(self, app_id: str) -> App:
        try:
            return self._by_id[app_id]
        except KeyError as exc:
            raise CatalogError(f"unknown app {app_id!r}") from exc

    def __contains__(self, app_id: object) -> bool:
        return app_id in self._by_id

    def connector(self, connector_id: str) -> tuple[App, Connector]:
        for app in self.apps:
            for connector in app.connectors:
                if connector.id == connector_id:
                    return app, connector
        raise CatalogError(f"unknown chat connector {connector_id!r}")


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CatalogError(f"invalid YAML in {path}: {exc}") from exc


def _validated(model: type[AppManifest] | type[Connector], path: Path) -> AppManifest | Connector:
    try:
        return model.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise CatalogError(f"{path.relative_to(path.parents[2])}: {exc}") from exc


def compose_images(compose: Path) -> dict[str, str]:
    """Every image a compose file declares, by compose service name."""
    document = _read_yaml(compose) or {}
    services = document.get("services") if isinstance(document, dict) else None
    return {
        str(name): str(definition["image"])
        for name, definition in (services or {}).items()
        if isinstance(definition, dict) and definition.get("image")
    }


def _services(compose: Path) -> dict[str, dict]:
    document = _read_yaml(compose) or {}
    services = document.get("services") if isinstance(document, dict) else None
    return {str(name): value for name, value in (services or {}).items() if isinstance(value, dict)}


def _check_images(compose: Path) -> None:
    """Every image is pinned to a well-formed digest, unless Mu3Lab builds it here from pinned sources."""
    for service, definition in _services(compose).items():
        image = str(definition.get("image") or "")
        if definition.get("build"):
            continue
        if not _PINNED.fullmatch(image):
            raise CatalogError(f"{compose}: service {service} must pin its image to a sha256 digest, not {image!r}")


# ``<address>:<host port>:<container port>[/tcp|/udp]``
_PUBLISH = re.compile(r"(?:(?P<address>.+):)?(?P<port>\d+):\d+(?:/(?P<protocol>tcp|udp))?")
# This server's tailnet address. The ``:?`` form makes Compose refuse to start without it:
# an empty address would publish the port on every network the computer is on.
_TAILNET_ADDRESS = re.compile(r"\$\{MU3LAB_TAILNET_IP:\?[^}:]*\}")


def published(compose: Path) -> list[tuple[int, str, str]]:
    """``(port, protocol, listen)`` for every host port a compose file publishes.

    ``listen`` is ``loopback`` (``127.0.0.1``) or ``tailnet`` (``${MU3LAB_TAILNET_IP:?...}``);
    any other address is refused.
    """
    ports = []
    for definition in _services(compose).values():
        for entry in definition.get("ports") or []:
            match = _PUBLISH.fullmatch(str(entry))
            if not match:
                raise CatalogError(f"{compose}: publish ports as 127.0.0.1:<host>:<container>, not {entry!r}")
            address = match["address"] or ""  # none at all means every address
            if address == "127.0.0.1":
                listen = "loopback"
            elif _TAILNET_ADDRESS.fullmatch(address):
                listen = "tailnet"
            else:
                raise CatalogError(
                    f"{compose}: {entry!r} must listen on 127.0.0.1 only "
                    "(or on ${MU3LAB_TAILNET_IP:?...} for a tailnet extra port)"
                )
            ports.append((int(match["port"]), match["protocol"] or "tcp", listen))
    return ports


def published_ports(compose: Path) -> list[int]:
    """The host port numbers a compose file publishes."""
    return [port for port, _protocol, _listen in published(compose)]


def _host_listen_ports(compose: Path) -> list[int]:
    """Ports a host-network Mu3Lab adapter listens on (``MCP_LISTEN``), on loopback and Docker's host address only."""
    ports = []
    for definition in _services(compose).values():
        if definition.get("network_mode") != "host":
            continue
        for item in str((definition.get("environment") or {}).get("MCP_LISTEN", "")).split(","):
            host, _, port = item.strip().rpartition(":")
            if host not in {"127.0.0.1", "@docker0"} or not port.isdigit():
                raise CatalogError(f"{compose}: MCP_LISTEN may use 127.0.0.1 and @docker0 only, not {item!r}")
            ports.append(int(port))
    return ports


def _check_compose(compose: Path, project: str) -> None:
    document = _read_yaml(compose)
    if not isinstance(document, dict) or document.get("name") != project:
        raise CatalogError(f"{compose}: the compose project must be named {project!r}")
    _check_images(compose)


def _check_read_only(connector: Connector, directory: Path) -> None:
    """An infrastructure connector is reviewed, and every tool its review offers only reads."""
    from ctl.mcp_review import load as load_review

    if not connector.reviewed:
        raise CatalogError(f"{directory}: an infrastructure connector needs a review")
    root = directory.parents[3]  # <checkout>/apps/<app>/connectors/<id>
    try:
        review = load_review(connector.id, str((directory / "review.yaml").relative_to(root)), root)
    except (OSError, ValueError) as exc:
        raise CatalogError(f"{directory}: {exc}") from None
    writes = sorted(name for name, tool in review.tools.items() if tool.access != "read")
    if writes:
        raise CatalogError(f"{directory}: an infrastructure connector may only read; it offers {', '.join(writes)}")


def _load_connectors(manifest: AppManifest, folder: Path) -> tuple[Connector, ...]:
    assistant = manifest.chat.assistant
    if manifest.chat.connectors and assistant is None:
        raise CatalogError(f"{manifest.id}: only apps with a chat assistant may have connectors")
    # Platform services (Authentik, Kopia) may only be read, and only by administrators:
    # their assistant shows the whole server's people, sign-ins or backups.
    infrastructure = manifest.group == "infrastructure"
    if infrastructure and assistant is not None and assistant.audience != "operators":
        raise CatalogError(f"{manifest.id}: an infrastructure app's assistant is for administrators only")
    connectors: list[Connector] = []
    for connector_id in manifest.chat.connectors:
        directory = folder / "connectors" / connector_id
        connector = _validated(Connector, directory / "connector.yaml")
        assert isinstance(connector, Connector)
        if connector.id != connector_id:
            raise CatalogError(f"{directory}: connector id must match its folder name")
        if connector.builtin:
            # The app serves MCP itself: it must be reachable by ContextForge, and nothing else runs.
            if (directory / COMPOSE_FILE).is_file():
                raise CatalogError(f"{directory}: a built-in connector has no {COMPOSE_FILE}")
            if "mu3lab_mcp_upstream" not in (folder / COMPOSE_FILE).read_text(encoding="utf-8"):
                raise CatalogError(f"{manifest.id}: a built-in connector needs the app on mu3lab_mcp_upstream")
        elif not (directory / COMPOSE_FILE).is_file():
            raise CatalogError(f"{directory}: missing {COMPOSE_FILE}")
        if connector.reviewed and not (directory / "review.yaml").is_file():
            raise CatalogError(f"{directory}: a reviewed connector needs review.yaml")
        if infrastructure:
            _check_read_only(connector, directory)
        if not connector.builtin:
            _check_images(directory / COMPOSE_FILE)
        if connector.provision and not (directory / connector.provision.script).is_file():
            raise CatalogError(f"{directory}: missing credential script {connector.provision.script}")
        for relative in connector.include:
            if Path(relative).is_absolute() or ".." in Path(relative).parts or not (ROOT / relative).is_file():
                raise CatalogError(f"{directory}: include {relative!r} must be a file inside the checkout")
        connectors.append(connector)
    if sum(connector.preferred for connector in connectors) > 1:
        raise CatalogError(f"{manifest.id}: at most one connector may be preferred")
    return tuple(connectors)


def _check_across(apps: list[App], platform_dir: Path = PLATFORM_DIR) -> None:
    ids = [app.id for app in apps]
    if len(ids) != len(set(ids)):
        raise CatalogError("app ids must be unique")
    known = set(ids)
    # One namespace for every port on the host (review R25): an app's loopback port, its Caddy
    # listener, its tailnet port, a connector's port and Mu3Lab's own services must all differ.
    # port -> (who owns it, what it is); one app may use a port for two of its own listeners (Caddy does).
    ports: dict[int, tuple[str, str]] = {port: (label, label) for port, label in RESERVED_PORTS.items()}

    def claim(port: int, label: str, owner: str = "") -> None:
        owner = owner or label
        if port in ports and ports[port][0] != owner:
            raise CatalogError(f"{label} and {ports[port][1]} both use port {port}")
        ports[port] = (owner, label)

    for compose in sorted(platform_dir.glob(f"*/{COMPOSE_FILE}")):
        _check_images(compose)
        for port in published_ports(compose):
            claim(port, f"Mu3Lab's {compose.parent.name}")
    connector_ids: set[str] = set()
    for app in apps:
        manifest = app.manifest
        unknown = set(manifest.depends_on) - known
        if unknown:
            raise CatalogError(f"{app.id}: unknown dependencies {sorted(unknown)}")
        claim(manifest.service.local_port, app.id)
        declared = {(manifest.service.local_port, "tcp", "loopback")}
        for extra in manifest.service.extra_ports:
            claim(extra.port, f"{app.id}'s {extra.purpose}", app.id)
            declared.add((extra.port, extra.protocol, extra.listen))
        for port, protocol, listen in published(app.folder / COMPOSE_FILE):
            if (port, protocol, listen) not in declared:
                raise CatalogError(
                    f"{app.id}: its compose file publishes port {port}, but service.local_port is "
                    f"{manifest.service.local_port} and service.extra_ports does not list it as {protocol} on {listen}"
                )
        for route in (manifest.route, *manifest.extra_routes):
            if route:
                name = getattr(route, "name", "")
                where = f"{app.id}'s {name} route" if name else f"{app.id}'s"
                claim(route.https_port, f"{where} tailnet address", app.id)
                claim(route.caddy_port, f"{where} Caddy listener", app.id)
        for connector in app.connectors:
            if connector.id in connector_ids:
                raise CatalogError(f"connector id {connector.id!r} is used twice")
            connector_ids.add(connector.id)
            health = connector.local_health.port
            if health is None:
                raise CatalogError(f"{connector.id}: local_health must name its port")
            if connector.builtin:
                # The app's own port, already claimed by the app.
                if health != app.manifest.service.local_port:
                    raise CatalogError(f"{connector.id}: a built-in connector's local_health uses the app's port")
                continue
            compose = app.connector_folder(connector.id) / COMPOSE_FILE
            connector_ports = published_ports(compose)
            if not connector_ports and health in _host_listen_ports(compose):
                # On the host network (Kopia's reader): it publishes nothing and listens itself.
                claim(health, f"connector {connector.id}")
                continue
            if connector_ports != [health]:
                raise CatalogError(
                    f"{connector.id}: its compose file publishes {connector_ports}, but local_health uses port {health}"
                )
            claim(health, f"connector {connector.id}")
    _check_no_cycles({app.id: app.manifest.depends_on for app in apps})


def _check_no_cycles(graph: dict[str, tuple[str, ...]]) -> None:
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        if node in done:
            return
        if node in visiting:
            raise CatalogError(f"dependency cycle through {node}")
        visiting.add(node)
        for child in graph.get(node, ()):
            visit(child)
        visiting.discard(node)
        done.add(node)

    for node in graph:
        visit(node)


def load(apps_dir: Path = APPS_DIR) -> Catalog:
    """Read every app folder; raise CatalogError on the first problem found."""
    apps: list[App] = []
    for manifest_path in sorted(apps_dir.glob("*/app.yaml")):
        folder = manifest_path.parent
        manifest = _validated(AppManifest, manifest_path)
        assert isinstance(manifest, AppManifest)
        if manifest.id != folder.name:
            raise CatalogError(f"{manifest_path}: id must match the folder name")
        _check_compose(folder / COMPOSE_FILE, f"mu3lab-{manifest.id}")
        apps.append(App(manifest=manifest, folder=folder, connectors=_load_connectors(manifest, folder)))
    if not apps:
        raise CatalogError(f"no apps found under {apps_dir}")
    _check_across(apps, apps_dir.parent / "platform")
    return Catalog(apps=tuple(apps))


_cache: dict[Path, tuple[tuple[tuple[str, int, int], ...], Catalog]] = {}
_cache_lock = threading.Lock()


def cached(apps_dir: Path = APPS_DIR) -> Catalog:
    """``load`` once per process, again only when a manifest file changes."""
    # Every file load() reads, so a changed compose or review file is seen too (review R25).
    files = [
        path
        for pattern in ("*/app.yaml", f"*/{COMPOSE_FILE}", "*/connectors/*/*.yaml", f"*/connectors/*/{COMPOSE_FILE}")
        for path in sorted(apps_dir.glob(pattern))
    ] + sorted((apps_dir.parent / "platform").glob(f"*/{COMPOSE_FILE}"))
    stamp = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in files)
    with _cache_lock:
        hit = _cache.get(apps_dir)
        if hit and hit[0] == stamp:
            return hit[1]
        catalog = load(apps_dir)
        _cache[apps_dir] = (stamp, catalog)
        return catalog
