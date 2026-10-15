"""Encrypted, write-only provider credential storage."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from ctl.provider_catalog import get
from ctl.runtime import RuntimePaths
from ctl.store import db
from ctl.store.secrets import SecretError as ProviderSecretError
from ctl.store.secrets import SecretStore

PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")


def _read(paths: RuntimePaths) -> list[dict[str, str]]:
    store = SecretStore(paths)
    return [record for name in store.list_names("provider") if (record := store.get("provider", name)) is not None]


def _write(records: list[dict[str, str]], paths: RuntimePaths) -> None:
    store = SecretStore(paths)
    names = {str(record.get("job_id") or record["id"]) for record in records}
    for name in set(store.list_names("provider")) - names:
        store.delete("provider", name)
    for record in records:
        store.put("provider", str(record.get("job_id") or record["id"]), record)


@db.transactional
def save(provider_id: str, label: str, api_key: str, paths: RuntimePaths = RuntimePaths()) -> dict[str, str]:
    """Upsert one credential and return metadata only."""
    provider = get(provider_id)
    provider_id = provider.id
    label = label.strip() or provider.name
    if not PROVIDER_ID.fullmatch(provider_id):
        raise ProviderSecretError("provider id must be a short lowercase slug")
    if len(label) > 64:
        raise ProviderSecretError("provider label must be at most 64 characters")
    if not api_key or len(api_key) > 4096:
        raise ProviderSecretError("provider credential is required and too long")
    records = _read(paths)
    record = {
        "id": provider_id,
        "label": label,
        "api_key": api_key,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    records = [item for item in records if item.get("id") != provider_id]
    records.append(record)
    _write(records, paths)
    return {"id": provider_id, "label": label, "updated_at": record["updated_at"]}


@db.transactional
def delete(provider_id: str, paths: RuntimePaths = RuntimePaths()) -> bool:
    """Delete one credential atomically without exposing any other record."""
    records = _read(paths)
    retained = [item for item in records if item.get("id") != provider_id]
    if len(retained) == len(records):
        return False
    _write(retained, paths)
    return True


@db.transactional
def metadata(paths: RuntimePaths = RuntimePaths()) -> list[dict[str, str]]:
    """Return provider ids and labels only; never return encrypted values."""
    return [
        {key: item[key] for key in ("id", "label", "updated_at")}
        for item in _read(paths)
        if all(key in item for key in ("id", "label", "updated_at"))
    ]


@db.transactional
def records(paths: RuntimePaths = RuntimePaths()) -> list[dict[str, str]]:
    """Return private records for Mu3Lab's internal configuration renderer.

    This is intentionally not imported by an HTTP handler.  It forms the one
    narrow bridge between encrypted user credentials and generated root-only
    service configuration.
    """
    return [
        {key: str(item[key]) for key in ("id", "label", "api_key", "updated_at")}
        for item in _read(paths)
        if all(key in item for key in ("id", "label", "api_key", "updated_at"))
    ]
