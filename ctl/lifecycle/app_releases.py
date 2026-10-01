"""Which release of a catalog app is approved, and which one each machine runs.

The approved release is checked in: ``update.approved_version`` in
``services.yaml`` names it, and the digest-pinned images in
``apps/<id>/docker-compose.yml`` are it. The maintainer raises both together
(``make approve``) after testing a new upstream release.

The installed release is per machine: ``docker-compose.digest.yml`` in the
runtime project pins every container to the image it was installed or last
updated with, and every Compose command layers it on top. Pulling a newer
Mu3Lab only changes what is approved; an app moves to it solely through the
backup-first update in ``ctl.lifecycle.maintenance``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ctl import actions
from ctl.registry import Service
from ctl.runtime import RuntimePaths

RECORD = "docker-compose.digest.yml"
HISTORY = "releases.json"
_EXTENSION = "x-mu3lab-release"


@dataclass(frozen=True)
class Release:
    version: str
    images: dict[str, str] = field(default_factory=dict)


def _normalized(version: str) -> str:
    return version.strip().removeprefix("v")


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", _normalized(version)))


def is_newer(candidate: str, than: str) -> bool:
    return bool(_parts(candidate)) and _parts(candidate) > _parts(than)


def _identity(image: str) -> str:
    """Two references name the same image when their digests match."""
    reference, _, digest = image.partition("@")
    return digest or reference


def split_image(image: str) -> tuple[str, str]:
    """``registry/name:tag@digest`` -> (``registry/name``, ``tag``)."""
    reference = image.split("@", 1)[0]
    name, _, tag = reference.rpartition(":")
    if not name or "/" in tag:
        return reference, ""
    return name, tag


def compose_images(compose_file: Path) -> dict[str, str]:
    """Every declared image in a Compose file, by service name."""
    try:
        document = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {
        str(name): str(definition["image"])
        for name, definition in (document.get("services") or {}).items()
        if isinstance(definition, dict) and definition.get("image")
    }


def approved(service: Service, root: Path) -> Release:
    return Release(
        str(service.update.get("approved_version", "")),
        compose_images(service.compose_path(root) / "docker-compose.yml"),
    )


def _project(service_id: str, paths: RuntimePaths | None) -> Path:
    return (paths or RuntimePaths()).projects / service_id


def _infer_version(images: dict[str, str], release: Release) -> str:
    """Name the release of a record written before records carried one.

    Matching images are the approved release. Otherwise the app's own image
    tag gives it away: ``v3.2.2`` tagged where ``v3.3.0`` is approved.
    """
    shared = [name for name in images if name in release.images]
    if shared and all(_identity(images[name]) == _identity(release.images[name]) for name in shared):
        return release.version
    target = _normalized(release.version)
    for name in shared:
        _, approved_tag = split_image(release.images[name])
        prefix, found, suffix = approved_tag.partition(target)
        _, tag = split_image(images[name])
        if found and tag.startswith(prefix) and tag.endswith(suffix) and len(tag) > len(prefix) + len(suffix):
            version = tag[len(prefix) : len(tag) - len(suffix)]
            return f"v{version}" if release.version.startswith("v") and not version.startswith("v") else version
    return ""


def installed(service: Service, root: Path, paths: RuntimePaths | None = None) -> Release | None:
    """The release this machine runs, or None when the app was never installed."""
    try:
        document = yaml.safe_load((_project(service.id, paths) / RECORD).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    images = {
        str(name): str((definition or {}).get("image") or "")
        for name, definition in (document.get("services") or {}).items()
    }
    if not images or not all(images.values()):
        return None
    version = str((document.get(_EXTENSION) or {}).get("version") or "")
    return Release(version or _infer_version(images, approved(service, root)), images)


def installed_version(service: Service, root: Path, paths: RuntimePaths | None = None) -> str:
    release = installed(service, root, paths)
    return release.version if release else ""


def status(service: Service, root: Path, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """What the dashboard shows: installed, approved, and whether to offer the move."""
    target = approved(service, root)
    current = installed(service, root, paths)
    if not current:
        return {
            "installed_version": "",
            "approved_version": target.version,
            "update_available": False,
            "supporting_only": False,
        }
    changed = any(_identity(current.images.get(name, "")) != _identity(image) for name, image in target.images.items())
    # Never offer a step back: an app already past the approved release stays put.
    available = changed and not is_newer(current.version, target.version)
    return {
        "installed_version": current.version,
        "approved_version": target.version,
        "update_available": available,
        # Same release, newer database or helper images: worth saying so.
        "supporting_only": available and current.version == target.version,
    }


def _history(service_id: str, paths: RuntimePaths | None) -> dict[str, dict[str, str]]:
    try:
        data = json.loads((_project(service_id, paths) / HISTORY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def from_history(service_id: str, version: str, paths: RuntimePaths | None = None) -> Release | None:
    """A release this machine ran before, so restoring its backup can bring it back."""
    images = _history(service_id, paths).get(version)
    return Release(version, images) if images else None


def _atomic(target: Path, text: str) -> None:
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)


def write(service_id: str, release: Release, paths: RuntimePaths | None = None) -> None:
    project = _project(service_id, paths)
    document: dict[str, Any] = {"services": {name: {"image": image} for name, image in release.images.items()}}
    if release.version:
        document[_EXTENSION] = {"version": release.version}
        history = _history(service_id, paths)
        history[release.version] = dict(release.images)
        _atomic(project / HISTORY, json.dumps(history, indent=2, sort_keys=True))
    _atomic(project / RECORD, yaml.safe_dump(document, sort_keys=True))


def restore_previous(service_id: str, previous: Release | None, paths: RuntimePaths | None = None) -> None:
    """Put back exactly the record that was there before an update attempt."""
    if previous:
        write(service_id, previous, paths)
    else:
        (_project(service_id, paths) / RECORD).unlink(missing_ok=True)


def align(service: Service, root: Path, paths: RuntimePaths | None = None) -> None:
    """Fit an existing record to a freshly copied Compose file.

    The record is itself a Compose file: an entry for a container the approved
    release dropped would bring that container back, image only. Containers
    the approved release added run its pinned image until the next update.
    """
    current = installed(service, root, paths)
    declared = compose_images(_project(service.id, paths) / "docker-compose.yml")
    if not current or set(current.images) <= set(declared):
        return
    kept = {name: image for name, image in current.images.items() if name in declared}
    write(service.id, Release(current.version, kept), paths)


def pin(service: Service, root: Path, log: Callable[[str], None], paths: RuntimePaths | None = None) -> dict[str, str]:
    """Record the release a new install runs; keep the one data was left at.

    Uninstalling with "keep data" leaves the record, so reinstalling reconnects
    that data to the release it was migrated to, and the newer approved
    release is offered as an ordinary, backed-up update.
    """
    current = installed(service, root, paths)
    if current:
        log(f"Keeping {service.name} {current.version or 'as installed'}, the release its existing data uses.")
        return current.images
    release = approved(service, root)
    images = compose_images(_project(service.id, paths) / "docker-compose.yml")
    if not images:
        raise RuntimeError("the curated deployment did not declare any images")
    pinned = {}
    for name, image in images.items():
        if "@sha256:" not in image:
            rc, output = actions.docker_image_digest(image)
            image = output.strip()
            if rc or "@sha256:" not in image:
                raise RuntimeError(f"image {name} did not resolve to an immutable digest")
        pinned[name] = image
    write(service.id, Release(release.version, pinned), paths)
    return pinned


def download(images: dict[str, str], log: Callable[[str], None]) -> None:
    """Pull every image of a release before anything is stopped. Raises RuntimeError."""
    for image in images.values():
        rc, output = actions.docker_cmd(["docker", "pull", image], log, timeout=3600)
        if rc:
            raise RuntimeError(f"{image} could not be downloaded: {output[-300:]}")
