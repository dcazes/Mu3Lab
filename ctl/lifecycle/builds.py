"""Whether an app's own images (Compose ``build:``) must be rebuilt for its files in place.

Mu3Lab builds a few images from source (LiveKit's voice parts). Rebuilding
them on every start after an update is slow, all the more once the build
cache was cleared (``ctl.disk_cleanup``). So each build records a fingerprint
of what it was built from: every file in each build context, its Dockerfile
and build arguments. A start rebuilds only when that fingerprint changed, or
when no record says what the images were built from.

The record is removed before a build and written once the build finished, so a
build that failed half-way leaves no record and the next start builds again.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from ctl.runtime import RuntimePaths


def _record(service_id: str, paths: RuntimePaths | None) -> Path:
    return (paths or RuntimePaths()).state / "builds" / f"{service_id}.json"


def fingerprints(project: Path) -> dict[str, str]:
    """A digest per service that Compose builds, of everything its build reads; {} for none."""
    try:
        document = yaml.safe_load((project / "docker-compose.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    found: dict[str, str] = {}
    for name, definition in (document.get("services") or {}).items():
        build = (definition or {}).get("build") if isinstance(definition, dict) else None
        if not build:
            continue
        spec = {"context": build} if isinstance(build, str) else dict(build)
        context = (project / str(spec.get("context") or ".")).resolve()
        digest = hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode())
        if context.is_dir():
            for path in sorted(context.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    digest.update(str(path.relative_to(context)).encode() + b"\0")
                    digest.update(path.read_bytes())
                    digest.update(b"\0")
        found[str(name)] = digest.hexdigest()
    return found


def needed(service_id: str, project: Path, paths: RuntimePaths | None = None) -> bool:
    """True when the app builds images and they may not match its files in place."""
    current = fingerprints(project)
    if not current:
        return False
    try:
        recorded = json.loads(_record(service_id, paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return recorded != current


def forget(service_id: str, paths: RuntimePaths | None = None) -> None:
    _record(service_id, paths).unlink(missing_ok=True)


def remember(service_id: str, project: Path, paths: RuntimePaths | None = None) -> None:
    current = fingerprints(project)
    record = _record(service_id, paths)
    if not current:
        record.unlink(missing_ok=True)
        return
    record.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = record.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")
    temporary.replace(record)
