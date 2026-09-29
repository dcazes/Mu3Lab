"""FreeLLMAPI's own admin API: key health, model ranking and model tests.

FreeLLMAPI keeps the up-to-date knowledge of every free provider: how to
validate a key, which models each one serves, and how to rank them. Mu3Lab
asks it instead of keeping its own model lists, which would go stale.

It signs in with the internal account `core_setup` created for the gateway.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

BASE_URL = "http://127.0.0.1:3001"
ACCOUNT_EMAIL = "mu3lab-gateway@localhost.test"


class GatewayAdminError(RuntimeError):
    """FreeLLMAPI's admin API could not be used."""


def _call(path: str, *, method: str = "GET", token: str = "", body: Any = None, timeout: int = 60) -> tuple[int, Any]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        BASE_URL + path,
        method=method,
        headers=headers,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (OSError, ValueError) as exc:
        raise GatewayAdminError("FreeLLMAPI is not answering.") from exc


class GatewayAdmin:
    def __init__(self, token: str) -> None:
        self.token = token

    @classmethod
    def sign_in(cls, paths: RuntimePaths | None = None) -> GatewayAdmin:
        env = read_runtime_env((paths or RuntimePaths()).projects / "freellmapi" / ".env")
        password = env.get("FREELLMAPI_ADMIN_PASSWORD", "")
        if not password:
            raise GatewayAdminError("FreeLLMAPI's internal account is not set up.")
        status, body = _call("/api/auth/login", method="POST", body={"email": ACCOUNT_EMAIL, "password": password})
        token = str((body or {}).get("token", "")) if status == 200 and isinstance(body, dict) else ""
        if not token:
            raise GatewayAdminError(f"FreeLLMAPI refused its internal account (HTTP {status}).")
        return cls(token)

    def _list(self, path: str) -> list[dict[str, Any]]:
        status, body = _call(path, token=self.token)
        if status != 200 or not isinstance(body, list):
            raise GatewayAdminError(f"FreeLLMAPI {path} returned HTTP {status}.")
        return [item for item in body if isinstance(item, dict)]

    def api_keys(self) -> list[dict[str, Any]]:
        return self._list("/api/keys")

    def check_key(self, key_id: int) -> tuple[str, str]:
        """Run FreeLLMAPI's own validation of one key: (status, reason if invalid)."""
        status, body = _call(f"/api/health/check/{key_id}", method="POST", token=self.token, body={}, timeout=90)
        if status != 200 or not isinstance(body, dict):
            raise GatewayAdminError(f"FreeLLMAPI's key check returned HTTP {status}.")
        verdict = str(body.get("status", "unknown"))
        _status, health = _call("/api/health", token=self.token)
        rows = health.get("keys", []) if isinstance(health, dict) else []
        reason = next((str(row.get("lastHealthError") or "") for row in rows if row.get("id") == key_id), "")
        return verdict, reason

    def ranked_models(self, platform: str) -> list[dict[str, Any]]:
        """The platform's usable models in FreeLLMAPI's own routing order."""
        models = [
            model
            for model in self._list("/api/models")
            if model.get("platform") == platform
            and model.get("enabled")
            and model.get("fallbackEnabled")
            and model.get("keyCount")
        ]
        return sorted(models, key=lambda model: model.get("priority") or 10**9)

    def test_model(self, model_db_id: int) -> tuple[bool, str]:
        """FreeLLMAPI's pinned test: this exact model through this platform's key."""
        status, body = _call(f"/api/models/{model_db_id}/test", method="POST", token=self.token, body={}, timeout=90)
        if status != 200 or not isinstance(body, dict):
            return False, f"FreeLLMAPI's model test returned HTTP {status}."
        return bool(body.get("success")), str(body.get("error") or "")

    def delete_key(self, key_id: int) -> None:
        status, _body = _call(f"/api/keys/{key_id}", method="DELETE", token=self.token)
        if status not in {200, 204, 404}:
            raise GatewayAdminError(f"FreeLLMAPI could not remove key {key_id} (HTTP {status}).")
