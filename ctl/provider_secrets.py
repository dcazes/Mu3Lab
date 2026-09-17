"""Encrypted, write-only provider credential storage."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from ctl.runtime import RuntimePaths

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
    if key_path.is_file():
        key = key_path.read_bytes()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        os.chmod(key_path, 0o600)
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
    except Exception as exc:  # noqa: BLE001 - never expose crypto details
        raise ProviderSecretError("encrypted provider store could not be read") from exc
    return value if isinstance(value, list) else []


def save(provider_id: str, label: str, api_key: str, paths: RuntimePaths = RuntimePaths()) -> dict[str, str]:
    """Upsert one credential and return metadata only."""
    if not PROVIDER_ID.fullmatch(provider_id):
        raise ProviderSecretError("provider id must be a short lowercase slug")
    if not label.strip() or len(label.strip()) > 64:
        raise ProviderSecretError("provider label is required and must be at most 64 characters")
    if not api_key or len(api_key) > 4096:
        raise ProviderSecretError("provider credential is required and too long")
    records = _read(paths)
    record = {"id": provider_id, "label": label.strip(), "api_key": api_key,
              "updated_at": datetime.now(UTC).isoformat(timespec="seconds")}
    records = [item for item in records if item.get("id") != provider_id]
    records.append(record)
    cipher = _cipher(paths)
    _, store_path = _paths(paths)
    store_path.write_bytes(cipher.encrypt(json.dumps(records).encode("utf-8")))
    os.chmod(store_path, 0o600)
    return {"id": provider_id, "label": label.strip(), "updated_at": record["updated_at"]}


def metadata(paths: RuntimePaths = RuntimePaths()) -> list[dict[str, str]]:
    """Return provider ids and labels only; never return encrypted values."""
    return [{key: item[key] for key in ("id", "label", "updated_at")}
            for item in _read(paths) if all(key in item for key in ("id", "label", "updated_at"))]
