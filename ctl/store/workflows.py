"""Encrypted, short-lived workflow identity inputs.

The durable job database is intentionally secret-free. This store carries the
verified Authentik identity into the worker for at most 24 hours without reusing the provider-secret key.
"""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict
from uuid import uuid4

from ctl.runtime import RuntimePaths
from ctl.store import db
from ctl.store.secrets import SecretError as WorkflowSecretError
from ctl.store.secrets import SecretStore

TTL_HOURS = 24
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_!@#%"


def _now() -> datetime:
    return datetime.now(UTC)


def _read(paths: RuntimePaths) -> list[dict[str, Any]]:
    store = SecretStore(paths)
    return [record for name in store.list_names("job") if (record := store.get("job", name)) is not None]


def _write(records: list[dict[str, Any]], paths: RuntimePaths) -> None:
    store = SecretStore(paths)
    names = {str(record.get("job_id") or record["id"]) for record in records}
    for name in set(store.list_names("job")) - names:
        store.delete("job", name)
    for record in records:
        ttl = max(0, datetime.fromisoformat(record["expires_at"]).timestamp() - time.time())
        store.put("job", str(record.get("job_id") or record["id"]), record, ttl=ttl)


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


class JobIdentity(TypedDict):
    """The verified Authentik user a provisioning job creates an account for."""

    owner_uid: str
    email: str
    username: str
    display_name: str


@db.transactional
def save_job_identity(
    job_id: str, *, owner_uid: str, email: str, username: str, display_name: str, paths: RuntimePaths = RuntimePaths()
) -> None:
    if not job_id or not owner_uid or not email:
        raise WorkflowSecretError("verified job identity is incomplete")
    records = [
        item for item in _read(paths) if not (item.get("kind") == "job_identity" and item.get("job_id") == job_id)
    ]
    created = _now()
    records.append(
        {
            "id": uuid4().hex,
            "kind": "job_identity",
            "job_id": job_id,
            "owner_uid": owner_uid,
            "email": email,
            "username": username,
            "display_name": display_name,
            "created_at": created.isoformat(timespec="seconds"),
            "expires_at": (created + timedelta(hours=TTL_HOURS)).isoformat(timespec="seconds"),
        }
    )
    _write(records, paths)


@db.transactional
def job_identity(job_id: str, paths: RuntimePaths = RuntimePaths()) -> JobIdentity | None:
    cleanup(paths)
    for item in _read(paths):
        if item.get("kind") == "job_identity" and item.get("job_id") == job_id:
            return JobIdentity(
                owner_uid=str(item.get("owner_uid", "")),
                email=str(item.get("email", "")),
                username=str(item.get("username", "")),
                display_name=str(item.get("display_name", "")),
            )
    return None


@db.transactional
def delete_by_job(job_id: str, paths: RuntimePaths = RuntimePaths()) -> bool:
    records = _read(paths)
    retained = [item for item in records if item.get("job_id") != job_id]
    if len(retained) == len(records):
        return False
    _write(retained, paths)
    return True


@db.transactional
def cleanup(paths: RuntimePaths = RuntimePaths()) -> list[str]:
    now = _now()
    records = _read(paths)
    expired: list[str] = SecretStore(paths).cleanup("job")
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
