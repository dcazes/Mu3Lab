"""Content fingerprints that let the installer update only what changed.

Each installer step that builds something from checked-in inputs records a
SHA-256 of those inputs next to its output. Re-running ./install.sh after a
`git pull` compares the recorded value with the current inputs, so changed
dashboard sources or control-plane code are rebuilt or
restarted, and everything else is skipped. Content hashes are used instead of
modification times because git does not preserve mtimes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

BUILD_STAMP = Path("dashboard") / "dist" / ".mu3lab-build.sha256"
SERVICES_STAMP_NAME = "control-plane-code.sha256"

_DASHBOARD_FILES = ("index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts")
_DASHBOARD_DIRS = ("src", "public")
_IGNORED_PARTS = frozenset({"__pycache__", "node_modules", "dist"})


def _files(root: Path, directory: str, suffixes: tuple[str, ...] = ()) -> list[Path]:
    base = root / directory
    if not base.is_dir():
        return []
    return sorted(
        path
        for path in base.rglob("*")
        if path.is_file()
        and not _IGNORED_PARTS.intersection(path.relative_to(root).parts)
        and (not suffixes or path.suffix in suffixes)
    )


def digest(root: Path, paths: Iterable[Path]) -> str:
    """Hash file names (relative to root) and contents in a stable order."""
    value = hashlib.sha256()
    for path in sorted(set(paths)):
        if not path.is_file():
            continue
        value.update(str(path.relative_to(root)).encode("utf-8"))
        value.update(b"\0")
        value.update(path.read_bytes())
        value.update(b"\0")
    return value.hexdigest()


def dashboard_digest(root: Path) -> str:
    dashboard = root / "dashboard"
    paths = [dashboard / name for name in _DASHBOARD_FILES]
    for directory in _DASHBOARD_DIRS:
        paths.extend(_files(root, f"dashboard/{directory}"))
    return digest(root, paths)


def control_plane_digest(root: Path) -> str:
    """Everything the long-running dashboard and worker load at start-up."""
    paths = _files(root, "ctl", (".py", ".html"))
    paths.extend(root / name for name in ("pyproject.toml", "uv.lock"))
    paths.extend(path for path in (root / "apps").rglob("*") if path.suffix in (".yaml", ".yml") and path.is_file())
    paths.extend(_files(root, "deploy", (".service",)))
    return digest(root, paths)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError):
        return ""


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    temporary.replace(path)
