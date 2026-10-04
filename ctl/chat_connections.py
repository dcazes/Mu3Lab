"""Encrypted chat approvals and per-person keys, bound to Authentik subjects."""

from __future__ import annotations

import time
from typing import Any

from ctl.runtime import RuntimePaths
from ctl.secret_file import serialized
from ctl.store.secrets import SecretStore


def _read(paths: RuntimePaths) -> dict[str, Any]:
    store = SecretStore(paths)
    return {name: store.get("chat", name) for name in store.list_names("chat")}


def _write(data: dict[str, Any], paths: RuntimePaths) -> None:
    store = SecretStore(paths)
    for name in set(store.list_names("chat")) - set(data):
        store.delete("chat", name)
    for name, record in data.items():
        ttl = max(0, record["expires_at"] - time.time()) if "expires_at" in record else None
        store.put("chat", name, record, ttl=ttl)


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
