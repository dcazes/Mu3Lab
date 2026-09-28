"""Content fingerprints that let the installer update only what changed.

Each installer step that builds something from checked-in inputs records a
SHA-256 of those inputs next to its output. Re-running ./install.sh after a
`git pull` compares the recorded value with the current inputs, so changed
requirements, dashboard sources or control-plane code are rebuilt or
restarted, and everything else is skipped. Content hashes are used instead of
modification times because git does not preserve mtimes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

REQUIREMENTS_STAMP = Path(".venv") / ".mu3lab-requirements.sha256"
NODE_MODULES_STAMP = Path("dashboard") / "node_modules" / ".mu3lab-lock.sha256"
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


def requirements_digest(root: Path) -> str:
    # Plain file hash so ./install.sh can compute the same value with sha256sum.
    return hashlib.sha256((root / "ctl" / "requirements.txt").read_bytes()).hexdigest()


def lock_digest(root: Path) -> str:
    lock = root / "dashboard" / "package-lock.json"
    return hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else ""


def dashboard_digest(root: Path) -> str:
    dashboard = root / "dashboard"
    paths = [dashboard / name for name in _DASHBOARD_FILES]
    for directory in _DASHBOARD_DIRS:
        paths.extend(_files(root, f"dashboard/{directory}"))
    return digest(root, paths)


def control_plane_digest(root: Path) -> str:
    """Everything the long-running dashboard and worker load at start-up."""
    paths = _files(root, "ctl", (".py", ".txt", ".html"))
    paths.extend(root / name for name in ("services.yaml", "catalog.yaml", "mcp-catalog.yaml"))
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
