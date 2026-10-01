"""Move one catalog app to a newer upstream release, and keep it there.

The checked-in Compose file pins each app to the release Mu3Lab was tested
with. An update writes ``docker-compose.update.yml`` beside the materialized
project: a Compose override that swaps only the app's own images for the new
release, pinned to their digests. Every lifecycle command layers it on top
(see ``ctl.compute.compose_overrides``), and materializing the project again
leaves it alone, so a later repair or retry never drops an updated app back
onto an older image its migrated data no longer fits.

Once Mu3Lab itself pins the same or a newer release, the override is stale
and ignored; the checked-in file wins again.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ctl import actions
from ctl.registry import Service
from ctl.runtime import RuntimePaths

OVERRIDE = "docker-compose.update.yml"
_EXTENSION = "x-mu3lab-update"
_RELEASE_TAG = re.compile(r"^[A-Za-z0-9._+-]{1,80}$")


def _normalized(version: str) -> str:
    return version.strip().removeprefix("v")


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", _normalized(version)))


def is_newer(candidate: str, than: str) -> bool:
    return bool(_parts(candidate)) and _parts(candidate) > _parts(than)


def _version_pattern(version: str) -> re.Pattern[str]:
    # Whole version only: 3.2.2 must not match inside 13.2.2 or 3.2.21.
    return re.compile(r"(?<![0-9.])" + re.escape(version) + r"(?![0-9]|\.[0-9])")


def _split(image: str) -> tuple[str, str]:
    """``registry/name:tag@digest`` -> (``registry/name``, ``tag``)."""
    reference = image.split("@", 1)[0]
    name, _, tag = reference.rpartition(":")
    if not name or "/" in tag:
        return reference, ""
    return name, tag


def plan(service: Service, root: Path, target_version: str) -> dict[str, str]:
    """The app's images re-tagged for ``target_version``, by Compose service name.

    Only images tagged with the release Mu3Lab pins are the app's own; a
    database or cache next to it keeps its tested image. An empty result
    means the app's image tags do not follow its release numbers, so Mu3Lab
    cannot tell which image a release corresponds to.
    """
    current = _normalized(str(service.update.get("current_version", "")))
    target = _normalized(target_version)
    if not current or not target or not _RELEASE_TAG.fullmatch(target_version):
        return {}
    try:
        document = yaml.safe_load((service.compose_path(root) / "docker-compose.yml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    pattern = _version_pattern(current)
    result = {}
    for name, definition in ((document or {}).get("services") or {}).items():
        image = str((definition or {}).get("image") or "")
        repository, tag = _split(image)
        if tag and pattern.search(tag):
            result[str(name)] = f"{repository}:{pattern.sub(target, tag)}"
    return result


def _path(service_id: str, paths: RuntimePaths | None) -> Path:
    return (paths or RuntimePaths()).projects / service_id / OVERRIDE


def read(service_id: str, paths: RuntimePaths | None = None) -> dict[str, Any] | None:
    """The written override as {"version", "images"}, or None."""
    try:
        document = yaml.safe_load(_path(service_id, paths).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    version = str((document.get(_EXTENSION) or {}).get("version") or "")
    images = {
        str(name): str((definition or {}).get("image") or "")
        for name, definition in (document.get("services") or {}).items()
    }
    if not version or not images or not all(images.values()):
        return None
    return {"version": version, "images": images}


def active(service: Service, paths: RuntimePaths | None = None) -> dict[str, Any] | None:
    """The override, unless Mu3Lab's own pinned release has caught up with it."""
    override = read(service.id, paths)
    if not override or not is_newer(override["version"], str(service.update.get("current_version", ""))):
        return None
    return override


def installed_version(service: Service, paths: RuntimePaths | None = None) -> str:
    override = active(service, paths)
    return override["version"] if override else str(service.update.get("current_version", ""))


def override_file(service: Service, paths: RuntimePaths | None = None) -> Path | None:
    return _path(service.id, paths) if active(service, paths) else None


def write(service_id: str, version: str, images: dict[str, str], paths: RuntimePaths | None = None) -> None:
    target = _path(service_id, paths)
    document = {
        _EXTENSION: {"version": version},
        "services": {name: {"image": image} for name, image in images.items()},
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def restore_previous(service_id: str, previous: dict[str, Any] | None, paths: RuntimePaths | None = None) -> None:
    """Put back exactly the override that was there before an update attempt."""
    if previous:
        write(service_id, previous["version"], previous["images"], paths)
    else:
        _path(service_id, paths).unlink(missing_ok=True)


def download(images: dict[str, str], log: Callable[[str], None]) -> dict[str, str]:
    """Pull each new image and pin it to its digest. Raises RuntimeError on any failure."""
    pinned = {}
    for name, image in images.items():
        rc, output = actions.docker_cmd(["docker", "pull", image], log, timeout=3600)
        if rc:
            raise RuntimeError(
                f"{image} could not be downloaded; the release may not be published yet. {output[-300:]}"
            )
        rc, output = actions.docker_image_digest(image)
        digest = output.strip().rpartition("@")[2]
        if rc or not digest.startswith("sha256:"):
            raise RuntimeError(f"{image} did not resolve to an immutable digest")
        pinned[name] = f"{image}@{digest}"
    return pinned
