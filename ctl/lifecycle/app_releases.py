"""Which release of a catalog app is approved, and which one each machine runs.

The approved release is checked in: ``update.approved_version`` in
``version`` in ``apps/<id>/app.yaml`` names it, and the digest-pinned images in
``apps/<id>/docker-compose.yml`` are it. The maintainer raises both together
(``make approve``) after testing a new upstream release.

The installed release is per machine: ``docker-compose.digest.yml`` in the
runtime project pins every container to the image it was installed or last
updated with, and every Compose command layers it on top. Pulling a newer
Mu3Lab only changes what is approved; an app moves to it solely through the
backup-first update in ``ctl.lifecycle.maintenance``.

A release is identified by content, not by its version label (R11): its
``id`` hashes every service image, the compute variant (CPU or a GPU
override) and a digest of the deployment definition (the Compose file, the
variant's override and the app's configuration and integration schema). Two
deployments of the same version with a different database image are two
releases. History keeps every release by id and never overwrites one, and
backups are tagged with the id so a restore brings back exactly that release.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ctl import actions
from ctl.engine.compose import Compose
from ctl.registry import Service
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic
from ctl.store import records

RECORD = "docker-compose.digest.yml"
_EXTENSION = "x-mu3lab-release"


class AmbiguousRelease(LookupError):
    """More than one recorded release carries this version label; Mu3Lab will not guess."""


@dataclass(frozen=True)
class Release:
    version: str
    images: dict[str, str] = field(default_factory=dict)
    # Part of the identity, but two records naming the same images and version
    # compare equal so older records (without them) still match.
    variant: str = field(default="cpu", compare=False)
    definition: str = field(default="", compare=False)

    @property
    def id(self) -> str:
        canonical = json.dumps(
            {"images": dict(sorted(self.images.items())), "variant": self.variant, "definition": self.definition},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "images": dict(self.images),
            "variant": self.variant,
            "definition": self.definition,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Release:
        return cls(
            str(value.get("version") or ""),
            {str(name): str(image) for name, image in (value.get("images") or {}).items()},
            str(value.get("variant") or "cpu"),
            str(value.get("definition") or ""),
        )


def definition_digest(service: Service, directory: Path, variant: str) -> str:
    """Digest of what, besides images, decides how this release runs."""
    digest = hashlib.sha256()
    for name in ("docker-compose.yml", *([f"docker-compose.{variant}.yml"] if variant != "cpu" else [])):
        path = directory / name
        digest.update(name.encode() + b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"")
        digest.update(b"\0")
    schema = {
        "configuration": sorted(
            ({key: value for key, value in item.items() if key != "default"} for item in service.configuration),
            key=lambda item: str(item.get("key")),
        ),
        "integrations": sorted(item.capability for item in service.manifest.integrates_with),
    }
    digest.update(json.dumps(schema, sort_keys=True, default=str).encode())
    return digest.hexdigest()[:32]


def _variant_images(directory: Path, variant: str) -> dict[str, str]:
    images = compose_images(directory / "docker-compose.yml")
    if variant != "cpu":
        images.update(compose_images(directory / f"docker-compose.{variant}.yml"))
    return images


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


def approved(service: Service, root: Path, variant: str = "cpu") -> Release:
    """The checked-in release, including the images the variant's override selects."""
    directory = service.compose_path(root)
    return Release(
        str(service.update.get("approved_version", "")),
        _variant_images(directory, variant),
        variant,
        definition_digest(service, directory, variant),
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
    extension = document.get(_EXTENSION) or {}
    version = str(extension.get("version") or "")
    return Release(
        version or _infer_version(images, approved(service, root)),
        images,
        str(extension.get("variant") or "cpu"),
        str(extension.get("definition") or ""),
    )


def installed_version(service: Service, root: Path, paths: RuntimePaths | None = None) -> str:
    release = installed(service, root, paths)
    return release.version if release else ""


def status(service: Service, root: Path, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """What the dashboard shows: installed, approved, and whether to offer the move."""
    current = installed(service, root, paths)
    target = approved(service, root, current.variant if current else "cpu")
    if not current:
        return {
            "installed_version": "",
            "approved_version": target.version,
            "update_available": False,
            "supporting_only": False,
            "added_services": [],
            "removed_services": [],
        }
    changed = any(_identity(current.images.get(name, "")) != _identity(image) for name, image in target.images.items())
    # The union of both service sets: a service the new release drops is a change too.
    removed = sorted(set(current.images) - set(target.images))
    added = sorted(set(target.images) - set(current.images))
    # Never offer a step back: an app already past the approved release stays put.
    available = (changed or bool(removed)) and not is_newer(current.version, target.version)
    return {
        "installed_version": current.version,
        "approved_version": target.version,
        "update_available": available,
        # Same release, newer database or helper images: worth saying so.
        "supporting_only": available and current.version == target.version,
        "added_services": added if available else [],
        "removed_services": removed if available else [],
    }


def _history(service_id: str, paths: RuntimePaths | None) -> dict[str, Release]:
    """Every release this machine ran, by id.

    Records written before release ids existed map a version label straight
    to its images; each becomes the CPU release with an unknown definition.
    """
    stored = records.get("app-releases", service_id, paths or RuntimePaths())
    releases: dict[str, Release] = {}
    for value in (stored.get("releases") or {}).values():
        if isinstance(value, dict):
            release = Release.from_dict(value)
            releases[release.id] = release
    for version, images in stored.items():
        if version != "releases" and isinstance(images, dict) and all(isinstance(v, str) for v in images.values()):
            legacy = Release(version, dict(images))
            releases.setdefault(legacy.id, legacy)
    return releases


def from_history(
    service_id: str, version: str = "", paths: RuntimePaths | None = None, *, release_id: str = ""
) -> Release | None:
    """A release this machine ran before, so restoring its backup can bring it back.

    By id when the backup names one. By version label only for older backups,
    and only when exactly one recorded release carries it; otherwise
    ``AmbiguousRelease`` is raised rather than guessing.
    """
    history = _history(service_id, paths)
    if release_id:
        return history.get(release_id)
    matches = [release for release in history.values() if release.version == version]
    if len({tuple(sorted(release.images.items())) for release in matches}) > 1:
        raise AmbiguousRelease(version)
    return matches[0] if matches else None


def remember(service_id: str, release: Release, paths: RuntimePaths | None = None) -> str:
    """Keep ``release`` in history (never rewriting one already there); return its id."""
    stored = records.get("app-releases", service_id, paths or RuntimePaths())
    kept = dict(stored.get("releases") or {})
    if release.id not in kept:
        kept[release.id] = release.as_dict()
        records.put("app-releases", service_id, {**stored, "releases": kept}, paths or RuntimePaths())
    return release.id


def write(service_id: str, release: Release, paths: RuntimePaths | None = None) -> None:
    project = _project(service_id, paths)
    document: dict[str, Any] = {"services": {name: {"image": image} for name, image in release.images.items()}}
    if release.version:
        document[_EXTENSION] = {
            "version": release.version,
            "id": release.id,
            "variant": release.variant,
            "definition": release.definition,
        }
        remember(service_id, release, paths)
    write_atomic(project / RECORD, yaml.safe_dump(document, sort_keys=True).encode())


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
    write(service.id, Release(current.version, kept, current.variant, current.definition), paths)


def pin(
    service: Service,
    root: Path,
    log: Callable[[str], None],
    paths: RuntimePaths | None = None,
    *,
    gpu_mode: str = "cpu",
) -> dict[str, str]:
    """Record the release a new install runs; keep the one data was left at.

    Uninstalling with "keep data" leaves the record, so reinstalling reconnects
    that data to the release it was migrated to, and the newer approved
    release is offered as an ordinary, backed-up update.
    """
    current = installed(service, root, paths)
    if current:
        log(f"Keeping {service.name} {current.version or 'as installed'}, the release its existing data uses.")
        return current.images
    release = approved(service, root, gpu_mode)
    project = _project(service.id, paths)
    images: dict[str, str] = {}
    for compose_file in Compose(project, gpu_mode=gpu_mode).files():
        images.update(compose_images(compose_file))
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
    write(service.id, Release(release.version, pinned, gpu_mode, definition_digest(service, project, gpu_mode)), paths)
    return pinned


def download(images: dict[str, str], log: Callable[[str], None]) -> None:
    """Pull every image of a release before anything is stopped. Raises RuntimeError."""
    for image in images.values():
        rc, output = actions.docker_cmd(["docker", "pull", image], log, timeout=3600)
        if rc:
            raise RuntimeError(f"{image} could not be downloaded: {output[-300:]}")
