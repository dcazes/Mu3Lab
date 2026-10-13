"""Opaque proof that an app backup belongs with a platform export.

This is deliberately conservative: changed deployment files or saved app
settings require a new app backup before automatic new-host recovery.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from cryptography.fernet import Fernet

from ctl.runtime import RuntimePaths


def fingerprint(app_id: str, folders: list[str], paths: RuntimePaths, *, control: Path | None = None) -> str:
    control = control or paths.state
    key = (control / "secrets.key").read_bytes()
    cipher = Fernet(key)
    settings = {}
    with closing(sqlite3.connect((control / "mu3lab.db").resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        for name, ciphertext in conn.execute(
            "SELECT name, ciphertext FROM secrets WHERE scope=? ORDER BY name", (f"app:{app_id}",)
        ):
            settings[name] = json.loads(cipher.decrypt(ciphertext))
    project = paths.projects / app_id
    if project.is_symlink():
        raise ValueError("A linked deployment cannot prove backup compatibility.")
    files = {}
    for path in sorted(project.rglob("*")):
        if path.is_symlink():
            raise ValueError("A linked deployment cannot prove backup compatibility.")
        if path.is_file() and "__pycache__" not in path.parts:
            files[str(path.relative_to(project))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if "docker-compose.yml" not in files:
        raise ValueError("The app's deployed Compose file is missing.")
    document = {"format": 1, "app": app_id, "folders": sorted(folders), "files": files, "settings": settings}
    return hmac.new(key, json.dumps(document, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def export_contexts(paths: RuntimePaths, control: Path) -> dict[str, dict]:
    from ctl.lifecycle.uninstall import data_directories
    from ctl.registry import load

    result = {}
    for service in load().services:
        project = paths.projects / service.id
        if not service.manifest.backup.include or not (project / "docker-compose.yml").is_file():
            continue
        folders = sorted(
            path.name
            for path in data_directories(service, Path(__file__).resolve().parents[1], paths=paths)
            if path.exists()
        )
        if folders:
            result[service.id] = {
                "folders": folders,
                "fingerprint": fingerprint(service.id, folders, paths, control=control),
            }
    return result
