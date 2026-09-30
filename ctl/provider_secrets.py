"""Encrypted, write-only provider credential storage."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from ctl.runtime import RuntimePaths
from ctl.secret_file import read_or_create_key, serialized, write_atomic

PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")


class ProviderSecretError(ValueError):
    """Raised for invalid provider input or unavailable encrypted storage."""


def _paths(paths: RuntimePaths) -> tuple[Path, Path]:
    return paths.runtime / "provider-secrets.key", paths.runtime / "provider-connections.enc"


def _cipher(paths: RuntimePaths):
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - install gate covers this
        raise ProviderSecretError("encrypted provider storage is unavailable") from exc
    key_path, _ = _paths(paths)
    paths.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = read_or_create_key(key_path, Fernet.generate_key)
    if len(key) != 44:
        raise ProviderSecretError("encrypted provider key is invalid")
    return Fernet(key)


def _read(paths: RuntimePaths) -> list[dict[str, str]]:
    _, store_path = _paths(paths)
    if not store_path.is_file():
        return []
    _cipher(paths)  # also validates that encrypted storage is available
    try:
        raw = _cipher(paths).decrypt(store_path.read_bytes())
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ProviderSecretError("encrypted provider store could not be read") from exc
    return value if isinstance(value, list) else []


def _write(records: list[dict[str, str]], paths: RuntimePaths) -> None:
    # An interrupted update leaves either the old or the complete new ciphertext.
    _, store_path = _paths(paths)
    write_atomic(store_path, _cipher(paths).encrypt(json.dumps(records).encode("utf-8")))


@serialized("provider-secrets.lock")
def save(provider_id: str, label: str, api_key: str, paths: RuntimePaths = RuntimePaths()) -> dict[str, str]:
    """Upsert one credential and return metadata only."""
    from ctl.provider_catalog import get

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


@serialized("provider-secrets.lock")
def delete(provider_id: str, paths: RuntimePaths = RuntimePaths()) -> bool:
    """Delete one credential atomically without exposing any other record."""
    records = _read(paths)
    retained = [item for item in records if item.get("id") != provider_id]
    if len(retained) == len(records):
        return False
    _write(retained, paths)
    return True


@serialized("provider-secrets.lock")
def metadata(paths: RuntimePaths = RuntimePaths()) -> list[dict[str, str]]:
    """Return provider ids and labels only; never return encrypted values."""
    return [
        {key: item[key] for key in ("id", "label", "updated_at")}
        for item in _read(paths)
        if all(key in item for key in ("id", "label", "updated_at"))
    ]


@serialized("provider-secrets.lock")
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
