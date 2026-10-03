"""Load and cross-check every app folder under ``apps/``.

Each ``apps/<id>/app.yaml`` is validated on its own by the models; this module
checks what only makes sense across apps (unique ports, known dependencies)
and between a manifest and its folder (compose project name, pinned images,
connector folders and their reviews).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from ctl.manifest.models import AppManifest, Connector

ROOT = Path(__file__).resolve().parents[2]
APPS_DIR = ROOT / "apps"
COMPOSE_FILE = "docker-compose.yml"


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


def _check_compose(compose: Path, project: str) -> None:
    document = _read_yaml(compose)
    if not isinstance(document, dict) or document.get("name") != project:
        raise CatalogError(f"{compose}: the compose project must be named {project!r}")
    for service, image in compose_images(compose).items():
        if "@sha256:" not in image or ":latest" in image:
            raise CatalogError(f"{compose}: service {service} must pin its image to a digest")


def _load_connectors(manifest: AppManifest, folder: Path) -> tuple[Connector, ...]:
    if manifest.chat.connectors and (manifest.group == "infrastructure" or manifest.chat.assistant is None):
        # Chat may use app data only; platform services (Vaultwarden, Authentik, Caddy) never get tools.
        raise CatalogError(f"{manifest.id}: only apps with a chat assistant, never infrastructure, may have connectors")
    connectors: list[Connector] = []
    for connector_id in manifest.chat.connectors:
        directory = folder / "connectors" / connector_id
        connector = _validated(Connector, directory / "connector.yaml")
        assert isinstance(connector, Connector)
        if connector.id != connector_id:
            raise CatalogError(f"{directory}: connector id must match its folder name")
        if not (directory / COMPOSE_FILE).is_file():
            raise CatalogError(f"{directory}: missing {COMPOSE_FILE}")
        if connector.reviewed and not (directory / "review.yaml").is_file():
            raise CatalogError(f"{directory}: a reviewed connector needs review.yaml")
        if connector.provision and not (directory / connector.provision.script).is_file():
            raise CatalogError(f"{directory}: missing credential script {connector.provision.script}")
        for relative in connector.include:
            if Path(relative).is_absolute() or ".." in Path(relative).parts or not (ROOT / relative).is_file():
                raise CatalogError(f"{directory}: include {relative!r} must be a file inside the checkout")
        connectors.append(connector)
    if sum(connector.preferred for connector in connectors) > 1:
        raise CatalogError(f"{manifest.id}: at most one connector may be preferred")
    return tuple(connectors)


def _check_across(apps: list[App]) -> None:
    ids = [app.id for app in apps]
    if len(ids) != len(set(ids)):
        raise CatalogError("app ids must be unique")
    known = set(ids)
    ports: dict[str, str] = {}

    def claim(kind: str, port: int, owner: str) -> None:
        key = f"{kind}:{port}"
        if key in ports:
            raise CatalogError(f"{owner} and {ports[key]} both use {kind} port {port}")
        ports[key] = owner

    connector_ids: set[str] = set()
    for app in apps:
        manifest = app.manifest
        unknown = set(manifest.depends_on) - known
        if unknown:
            raise CatalogError(f"{app.id}: unknown dependencies {sorted(unknown)}")
        claim("local", manifest.service.local_port, app.id)
        if manifest.route:
            claim("https", manifest.route.https_port, app.id)
            claim("caddy", manifest.route.caddy_port, app.id)
        for connector in app.connectors:
            if connector.id in connector_ids:
                raise CatalogError(f"connector id {connector.id!r} is used twice")
            connector_ids.add(connector.id)
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
    _check_across(apps)
    return Catalog(apps=tuple(apps))


_cache: dict[Path, tuple[tuple[float, ...], Catalog]] = {}
_cache_lock = threading.Lock()


def cached(apps_dir: Path = APPS_DIR) -> Catalog:
    """``load`` once per process, again only when a manifest file changes."""
    files = sorted(apps_dir.glob("*/app.yaml")) + sorted(apps_dir.glob("*/connectors/*/connector.yaml"))
    stamp = tuple(path.stat().st_mtime for path in files)
    with _cache_lock:
        hit = _cache.get(apps_dir)
        if hit and hit[0] == stamp:
            return hit[1]
        catalog = load(apps_dir)
        _cache[apps_dir] = (stamp, catalog)
        return catalog
