"""Back up Mu3Lab itself and its core apps, so a lost machine can be rebuilt (R14).

App backups hold each app's data only; its code comes from Mu3Lab's git
checkout and its images from their registries. What git deliberately does not
hold is each installation's own state, and without it restored data cannot be
opened (a database folder is useless without its password):

- the control database (people, settings, every app's generated secrets and
  configuration, release history, jobs) copied with SQLite's backup API, so
  it is consistent while the dashboard keeps running;
- the encryption key that opens those secrets;
- the saved deployment copies (R12);
- the runtime projects (rendered Compose files and settings).

Kopia's backup service saves these as the source ``mu3lab``: just before
that backup, its before-action asks Mu3Lab to write the consistent copy
(``export_control_plane``) into a folder the service reads, and its
after-action empties it again. Each core app with data (sign-in, password
vault, chat, model gateways) is its own source, stopped and started around
its own backup like any app (``ctl.backup_service``). Apps whose manifest
says their data can be fetched again (downloaded models) are left out, with
the reason shown.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl import backups
from ctl.lifecycle.uninstall import data_directories
from ctl.registry import Registry, Service, load
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic

EXPORT = "platform-export"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def core_apps(registry: Registry, root: Path) -> tuple[list[Service], list[tuple[Service, str]]]:
    """Core and foundation apps to back up, in dependency order, and those left out with why."""
    candidates = {service.id: service for service in registry.services if service.stage != "optional"}
    ordered: list[Service] = []

    def visit(service: Service, seen: set[str]) -> None:
        if service.id in seen or service in ordered:
            return
        seen.add(service.id)
        for dependency in service.dependencies:
            if dependency in candidates:
                visit(candidates[dependency], seen)
        ordered.append(service)

    for service in sorted(candidates.values(), key=lambda item: item.id):
        visit(service, set())
    included, skipped = [], []
    for service in ordered:
        if not data_directories(service, root):
            continue  # no data of its own (the ingress)
        if not service.manifest.backup.include:
            skipped.append((service, service.manifest.backup.reason or "Its data can be fetched again."))
        else:
            included.append(service)
    return included, skipped


def _sqlite_copy(source: Path, target: Path) -> None:
    """A consistent copy of a live SQLite database (WAL included), private from its first byte."""
    target.touch(mode=0o600)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as live, sqlite3.connect(target) as copy:
        live.backup(copy)
    target.chmod(0o600)


def export_folder(paths: RuntimePaths) -> Path:
    """The folder the backup service reads Mu3Lab's own state from; it must outlive every export.

    The backup service sees this folder through a bind mount, which follows
    the folder itself: it is emptied and refilled, never replaced.
    """
    target = paths.state / EXPORT
    target.mkdir(mode=0o700, exist_ok=True)
    return target


def clear_export(paths: RuntimePaths) -> None:
    """Empty the export folder once its backup is saved; it holds a copy of every secret."""
    target = export_folder(paths)
    for entry in target.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def export_control_plane(paths: RuntimePaths) -> Path:
    """Write Mu3Lab's own state into the private folder of the ``mu3lab`` backup source."""
    target = export_folder(paths)
    clear_export(paths)
    database = paths.state / "mu3lab.db"
    if database.is_file():
        _sqlite_copy(database, target / "mu3lab.db")
    key = paths.state / "secrets.key"
    if key.is_file():
        write_atomic(target / "secrets.key", key.read_bytes())
    request_key = paths.state / "mu3lab.request-key"
    if request_key.is_file():
        write_atomic(target / request_key.name, request_key.read_bytes())
    deployments = paths.state / "deployments"
    if deployments.is_dir():
        shutil.copytree(deployments, target / "deployments", symlinks=False)
    from ctl import __version__

    manifest = {"created_at": _now(), "mu3lab_version": __version__, "files": sorted(_listing(target))}
    write_atomic(target / "export.json", json.dumps(manifest, indent=2, sort_keys=True).encode())
    return target


def _listing(folder: Path) -> list[str]:
    return [str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file()]


def status(root: Path, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """What the dashboard shows about Mu3Lab's own backup and each core app's."""
    paths = paths or RuntimePaths()
    services, skipped = core_apps(load(), root)
    return {
        "platform": backups.protection(backups.PLATFORM_SOURCE, paths),
        "core_apps": [
            {"id": service.id, "name": service.name, "included": True, "reason": "",
             "protection": backups.protection(service.id, paths)}
            for service in services
        ]
        + [
            {"id": service.id, "name": service.name, "included": False, "reason": reason, "protection": None}
            for service, reason in skipped
        ],
    }  # fmt: skip
