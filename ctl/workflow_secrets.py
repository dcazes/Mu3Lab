"""Encrypted, short-lived workflow inputs and credential handoffs.

The durable job database is intentionally secret-free. This store carries the
verified Authentik identity into the worker and retains generated application
credentials for at most 24 hours without reusing the provider-secret key.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ctl.runtime import RuntimePaths

TTL_HOURS = 24
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_!@#%"


class WorkflowSecretError(ValueError):
    """Raised when encrypted workflow storage is invalid or unavailable."""


def _now() -> datetime:
    return datetime.now(UTC)


def _paths(paths: RuntimePaths) -> tuple[Path, Path]:
    return paths.runtime / "workflow-secrets.key", paths.runtime / "workflow-secrets.enc"


def _cipher(paths: RuntimePaths):
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover
        raise WorkflowSecretError("encrypted workflow storage is unavailable") from exc
    key_path, _ = _paths(paths)
    paths.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    if key_path.is_file():
        key = key_path.read_bytes()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        os.chmod(key_path, 0o600)
    if len(key) != 44:
        raise WorkflowSecretError("encrypted workflow key is invalid")
    return Fernet(key)


def _read(paths: RuntimePaths) -> list[dict[str, Any]]:
    _, store_path = _paths(paths)
    if not store_path.is_file():
        return []
    try:
        raw = _cipher(paths).decrypt(store_path.read_bytes())
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise WorkflowSecretError("encrypted workflow store could not be read") from exc
    return value if isinstance(value, list) else []


def _write(records: list[dict[str, Any]], paths: RuntimePaths) -> None:
    cipher = _cipher(paths)
    _, store_path = _paths(paths)
    temporary = store_path.with_suffix(".enc.tmp")
    with temporary.open("wb") as handle:
        handle.write(cipher.encrypt(json.dumps(records, sort_keys=True).encode("utf-8")))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, store_path)
    os.chmod(store_path, 0o600)


def generate_password() -> str:
    """Return a 20-character password with every required character class."""
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower = "abcdefghijkmnopqrstuvwxyz"
    digits = "23456789"
    symbols = "-_!@#%"
    while True:
        value = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(20))
        if all(any(char in group for char in value) for group in (upper, lower, digits, symbols)):
            return value


def save_job_identity(job_id: str, *, owner_uid: str, email: str, username: str,
                      display_name: str, paths: RuntimePaths = RuntimePaths()) -> None:
    if not job_id or not owner_uid or not email:
        raise WorkflowSecretError("verified job identity is incomplete")
    records = [item for item in _read(paths)
               if not (item.get("kind") == "job_identity" and item.get("job_id") == job_id)]
    created = _now()
    records.append({"id": uuid4().hex, "kind": "job_identity", "job_id": job_id,
                    "owner_uid": owner_uid, "email": email, "username": username,
                    "display_name": display_name,
                    "created_at": created.isoformat(timespec="seconds"),
                    "expires_at": (created + timedelta(hours=TTL_HOURS)).isoformat(timespec="seconds")})
    _write(records, paths)


def job_identity(job_id: str, paths: RuntimePaths = RuntimePaths()) -> dict[str, str] | None:
    cleanup(paths)
    for item in _read(paths):
        if item.get("kind") == "job_identity" and item.get("job_id") == job_id:
            return {key: str(item.get(key, "")) for key in
                    ("owner_uid", "email", "username", "display_name")}
    return None


def create_handoff(*, service_id: str, job_id: str, owner_uid: str, username: str,
                   email: str, password: str, login_url: str,
                   paths: RuntimePaths = RuntimePaths()) -> dict[str, str]:
    created, handoff_id = _now(), uuid4().hex
    records = _read(paths)
    records.append({"id": handoff_id, "kind": "credential", "service_id": service_id,
                    "job_id": job_id, "owner_uid": owner_uid, "username": username,
                    "email": email, "password": password, "login_url": login_url,
                    "created_at": created.isoformat(timespec="seconds"),
                    "expires_at": (created + timedelta(hours=TTL_HOURS)).isoformat(timespec="seconds")})
    _write(records, paths)
    return {"id": handoff_id, "created_at": created.isoformat(timespec="seconds"),
            "expires_at": (created + timedelta(hours=TTL_HOURS)).isoformat(timespec="seconds")}


def metadata(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> list[dict[str, str]]:
    cleanup(paths)
    keys = ("id", "service_id", "job_id", "created_at", "expires_at", "login_url")
    return [{key: str(item.get(key, "")) for key in keys}
            for item in _read(paths)
            if item.get("kind") == "credential" and item.get("owner_uid") == owner_uid]


def reveal(handoff_id: str, owner_uid: str,
           paths: RuntimePaths = RuntimePaths()) -> dict[str, str] | None:
    cleanup(paths)
    for item in _read(paths):
        if (item.get("kind") == "credential" and item.get("id") == handoff_id
                and item.get("owner_uid") == owner_uid):
            return {key: str(item.get(key, "")) for key in
                    ("id", "service_id", "username", "email", "password",
                     "login_url", "created_at", "expires_at")}
    return None


def delete(record_id: str, owner_uid: str = "", paths: RuntimePaths = RuntimePaths()) -> bool:
    records = _read(paths)
    retained = [item for item in records if not (
        item.get("id") == record_id and (not owner_uid or item.get("owner_uid") == owner_uid))]
    if len(retained) == len(records):
        return False
    _write(retained, paths)
    return True


def delete_by_job(job_id: str, paths: RuntimePaths = RuntimePaths()) -> bool:
    records = _read(paths)
    retained = [item for item in records if item.get("job_id") != job_id]
    if len(retained) == len(records):
        return False
    _write(retained, paths)
    return True


def cleanup(paths: RuntimePaths = RuntimePaths()) -> list[str]:
    now = _now()
    records = _read(paths)
    expired: list[str] = []
    retained = []
    for item in records:
        try:
            is_expired = datetime.fromisoformat(str(item.get("expires_at", ""))) <= now
        except ValueError:
            is_expired = True
        if is_expired:
            expired.append(str(item.get("id", "")))
        else:
            retained.append(item)
    if len(retained) != len(records):
        _write(retained, paths)
    return expired
