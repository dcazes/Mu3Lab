"""Private, verified copies of an app's whole deployment, for exact rollback (R12).

Restoring data and images is not enough to undo an update: the new release
also changed the Compose file, overrides, rendered templates and settings. A
bundle captures all of it before anything changes:

- every regular file of the runtime project (``/srv/mu3lab/projects/<id>``),
  including the release record and the rendered ``.env``, under
  ``state/deployments/<id>/<bundle>/files`` (0700 directories, 0600 files);
- the app's canonical settings (``ctl.app_settings``), encrypted in the
  secret store;
- a manifest with each file's SHA-256, the release it ran and the data
  folders that release uses, so a restore can require a complete set.

``restore`` checks every hash first and refuses a damaged bundle, then makes
the project exactly the captured one (files the newer deployment added are
removed) and puts the settings back. Bundles are kept per release (the newest
of each) plus every one an unfinished or unresolved operation names.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl.app_settings import AppSettings
from ctl.registry import Service
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic
from ctl.store.secrets import SecretStore

MANIFEST = "bundle.json"
FILES = "files"


class BundleError(RuntimeError):
    """A deployment copy is missing or damaged; the message is safe to show."""


def _root(service_id: str, paths: RuntimePaths) -> Path:
    path = paths.state / "deployments" / service_id
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_files(project: Path) -> list[Path]:
    return sorted(
        path
        for path in project.rglob("*")
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
    )


def capture(
    service: Service,
    bundle_id: str,
    *,
    release_id: str,
    storage: list[str],
    paths: RuntimePaths | None = None,
) -> str:
    """Copy the app's current deployment as ``bundle_id``; a bundle already captured is kept as is."""
    paths = paths or RuntimePaths()
    target = _root(service.id, paths) / bundle_id
    if (target / MANIFEST).is_file():
        return bundle_id
    project = paths.projects / service.id
    staging = target.with_name(f".{bundle_id}.partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o700, parents=True)
    staging.chmod(0o700)
    files: dict[str, str] = {}
    for source in _project_files(project) if project.is_dir() else []:
        relative = source.relative_to(project)
        destination = staging / FILES / relative
        for folder in reversed(destination.relative_to(staging).parents[:-1]):
            (staging / folder).mkdir(mode=0o700, exist_ok=True)
        write_atomic(destination, source.read_bytes())
        files[str(relative)] = _digest(destination)
    settings = AppSettings(service.id, paths)
    SecretStore(paths).put(
        f"deployment:{service.id}",
        bundle_id,
        {"generated": settings.generated(), "config": settings.config(), "imported": settings.imported()},
    )
    manifest = {
        "bundle": bundle_id,
        "service_id": service.id,
        "release_id": release_id,
        "storage": sorted(storage),
        "files": files,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    write_atomic(staging / MANIFEST, json.dumps(manifest, sort_keys=True).encode())
    # The bundle appears complete or not at all.
    os.replace(staging, target)
    return bundle_id


def manifest(service_id: str, bundle_id: str, paths: RuntimePaths | None = None) -> dict[str, Any]:
    path = _root(service_id, paths or RuntimePaths()) / bundle_id / MANIFEST
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BundleError("The saved copy of this app's deployment is missing.") from None


def verify(service_id: str, bundle_id: str, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """The bundle's manifest, once every file matches its recorded hash."""
    paths = paths or RuntimePaths()
    value = manifest(service_id, bundle_id, paths)
    base = _root(service_id, paths) / bundle_id / FILES
    for relative, expected in value["files"].items():
        path = base / relative
        if not path.is_file() or _digest(path) != expected:
            raise BundleError(f"The saved copy of this app's deployment is damaged ({relative}).")
    if SecretStore(paths).get(f"deployment:{service_id}", bundle_id) is None:
        raise BundleError("The saved settings for this app's deployment are missing.")
    return value


def restore(service: Service, bundle_id: str, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Make the runtime project and settings exactly the captured deployment."""
    paths = paths or RuntimePaths()
    value = verify(service.id, bundle_id, paths)
    base = _root(service.id, paths) / bundle_id / FILES
    project = paths.projects / service.id
    project.mkdir(mode=0o750, parents=True, exist_ok=True)
    for relative in value["files"]:
        destination = project / relative
        destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        write_atomic(destination, (base / relative).read_bytes())
    kept = {project / relative for relative in value["files"]}
    for path in _project_files(project):
        if path not in kept:
            path.unlink(missing_ok=True)
    saved = SecretStore(paths).get(f"deployment:{service.id}", bundle_id) or {}
    settings = AppSettings(service.id, paths)
    settings.replace_generated(dict(saved.get("generated") or {}))
    current = settings.config()
    settings.set_config(dict(saved.get("config") or {}))
    for key in set(current) - set(saved.get("config") or {}):
        settings.store.delete(settings.scope, "config:" + key)
    return value


def latest_for_release(service_id: str, release_id: str, paths: RuntimePaths | None = None) -> str:
    """The newest complete bundle that ran ``release_id``, or ""."""
    if not release_id:
        return ""
    found: list[tuple[str, str]] = []
    for path in _root(service_id, paths or RuntimePaths()).iterdir():
        if path.name.startswith(".") or not (path / MANIFEST).is_file():
            continue
        try:
            value = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if value.get("release_id") == release_id:
            found.append((str(value.get("created_at", "")), path.name))
    return max(found)[1] if found else ""


def prune(service_id: str, keep: set[str], paths: RuntimePaths | None = None) -> list[str]:
    """Remove bundles that are neither the newest of their release nor in ``keep``."""
    paths = paths or RuntimePaths()
    newest: dict[str, tuple[str, str]] = {}
    everything: list[str] = []
    for path in _root(service_id, paths).iterdir():
        if not (path / MANIFEST).is_file():
            continue
        try:
            value = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        everything.append(path.name)
        release, created = str(value.get("release_id", "")), str(value.get("created_at", ""))
        if release not in newest or (created, path.name) > newest[release]:
            newest[release] = (created, path.name)
    kept = keep | {name for _created, name in newest.values()}
    removed = [name for name in everything if name not in kept]
    for name in removed:
        shutil.rmtree(_root(service_id, paths) / name, ignore_errors=True)
        SecretStore(paths).delete(f"deployment:{service_id}", name)
    return removed
