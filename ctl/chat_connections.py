"""Encrypted chat approvals and per-person keys, bound to Authentik subjects."""

from __future__ import annotations

import json
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ctl.runtime import RuntimePaths
from ctl.secret_file import read_or_create_key, serialized, write_atomic


def _cipher(paths: RuntimePaths) -> Fernet:
    return Fernet(read_or_create_key(paths.runtime / "chat-connections.key", Fernet.generate_key))


def _read(paths: RuntimePaths) -> dict[str, Any]:
    path = paths.runtime / "chat-connections.enc"
    if not path.exists():
        return {}
    try:
        return json.loads(_cipher(paths).decrypt(path.read_bytes()))
    except (InvalidToken, ValueError):
        raise ValueError("The saved chat connections could not be read.") from None


def _write(data: dict[str, Any], paths: RuntimePaths) -> None:
    write_atomic(paths.runtime / "chat-connections.enc", _cipher(paths).encrypt(json.dumps(data).encode()))


@serialized("chat-connections.lock")
def records(paths: RuntimePaths = RuntimePaths()) -> dict[str, Any]:
    return _read(paths)


@serialized("chat-connections.lock")
def save(uid: str, record: dict[str, Any], paths: RuntimePaths = RuntimePaths()) -> None:
    if not uid:
        raise ValueError("A verified sign-in is required to connect chat.")
    data = _read(paths)
    data[uid] = record
    _write(data, paths)


@serialized("chat-connections.lock")
def begin(uid: str, pending: dict[str, Any], paths: RuntimePaths = RuntimePaths()) -> None:
    """Never replace an approved key with a second browser's pending flow."""
    if not uid:
        raise ValueError("A verified sign-in is required to connect chat.")
    data = _read(paths)
    if data.get(uid, {}).get("key"):
        raise ValueError("Chat is already connected.")
    data[uid] = pending
    _write(data, paths)


@serialized("chat-connections.lock")
def poll(uid: str, client: Any, paths: RuntimePaths = RuntimePaths()) -> dict[str, Any]:
    """Serialize polling/key creation so concurrent browser requests mint once."""
    data = _read(paths)
    record = data.get(uid, {})
    if record.get("key"):
        return {"state": "connected"}
    if not record or time.time() >= record["expires_at"]:
        raise ValueError("Chat approval expired. Connect chat again.")
    if time.time() < record["next_poll"]:
        return {"state": "authorization_pending", "interval": record["interval"]}
    result = client.poll_device_login(record["device_code"])
    if result["state"] == "approved":
        key = client.create_key(result["access_token"])
        data[uid] = {"key": key, "managed": {}}
        _write(data, paths)
        return {"state": "connected"}
    if result["state"] == "slow_down":
        record["interval"] += 5
    record["next_poll"] = time.time() + record["interval"]
    _write(data, paths)
    return {"state": result["state"], "interval": record["interval"]}
