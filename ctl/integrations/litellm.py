"""LiteLLM's key-management API, used with its master key on loopback.

Only what Mu3Lab needs: list a person's keys, create one limited to certain
models, and delete keys. Key values are returned once by ``generate`` and
never logged.
"""

from __future__ import annotations

from typing import Any

import httpx


class LiteLLMError(RuntimeError):
    """LiteLLM refused or did not answer; the message never contains a key."""


class LiteLLM:
    def __init__(self, base_url: str, master_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {master_key}"},
            timeout=20,
            transport=transport,
        )

    def __enter__(self) -> LiteLLM:
        return self

    def __exit__(self, *_args: object) -> None:
        self._client.close()

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise LiteLLMError(f"LiteLLM could not be reached ({type(exc).__name__}).") from None
        if not response.is_success:
            raise LiteLLMError(f"LiteLLM refused the key request (HTTP {response.status_code}).")
        return response.json()

    def keys(self, user_id: str) -> list[dict[str, Any]]:
        """Key records (never key values) for one person."""
        data = self._call("GET", "/key/list", params={"user_id": user_id, "return_full_object": "true"})
        return [item for item in data.get("keys", []) if isinstance(item, dict)]

    def generate(self, *, user_id: str, alias: str, models: list[str], rpm_limit: int) -> str:
        data = self._call(
            "POST",
            "/key/generate",
            json={"user_id": user_id, "key_alias": alias, "models": models, "rpm_limit": rpm_limit},
        )
        key = data.get("key")
        if not isinstance(key, str) or not key:
            raise LiteLLMError("LiteLLM did not return a key.")
        return key

    def delete(self, aliases: list[str]) -> None:
        if aliases:
            self._call("POST", "/key/delete", json={"key_aliases": aliases})
