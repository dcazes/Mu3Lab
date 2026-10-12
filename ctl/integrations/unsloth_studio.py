"""Unsloth Studio's API, as Mu3Lab drives it from this computer.

Studio's own owner account belongs to Mu3Lab: its password is a
generated secret, and with it Mu3Lab mints the API key LiteLLM uses, turns on
the settings model routing needs, and creates each administrator's personal
Studio account. People never use the owner account.

Studio loads a downloaded GGUF model when a request names it (``auto-switch``),
keeps a second one loaded beside it (``multi-model``), and serves the search
model from its own settings (``RAG_EMBEDDING_MODEL``) on ``/v1/embeddings``.
Passwords, keys and API error bodies never reach logs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

# Mu3Lab's key for chat and search requests, kept in the app's private settings.
KEY_ENV = "UNSLOTH_API_KEY"
PASSWORD_ENV = "UNSLOTH_STUDIO_PASSWORD"
KEY_NAME = "mu3lab-litellm"
# The port inside the container, which LiteLLM reaches on Mu3Lab's private network.
CONTAINER_PORT = 8000
Log = Callable[[str], None]


class StudioError(RuntimeError):
    """A safe message for the dashboard or a job log."""


class Studio:
    def __init__(
        self, port: int, *, token: str = "", timeout: float = 30, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.port, self.transport = port, transport
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=timeout, transport=transport)
        self.token = token

    def __enter__(self) -> Studio:
        return self

    def __exit__(self, *_args: object) -> None:
        self.client.close()

    def request(self, method: str, path: str, *, timeout: float | None = None, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = self.client.request(
                method, path, headers=headers, timeout=timeout or self.client.timeout, **kwargs
            )
            data = response.json() if response.content else None
        except (httpx.HTTPError, ValueError):
            raise StudioError("Unsloth Studio could not be reached.") from None
        if not response.is_success:
            detail = data.get("detail") if isinstance(data, dict) else None
            # Studio's own sentences name no secrets; keep them, they say what to fix.
            reason = detail if isinstance(detail, str) and len(detail) < 300 else f"HTTP {response.status_code}"
            raise StudioError(f"Unsloth Studio did not accept this request ({reason}).")
        return data

    # --- Accounts ----------------------------------------------------------------

    def login(self, username: str, password: str) -> dict[str, Any]:
        data = self.request("POST", "/api/auth/login", json={"username": username, "password": password})
        self.token = str(data["access_token"])
        return data

    def create_key(self, name: str) -> str:
        return str(self.request("POST", "/api/auth/api-keys", json={"name": name})["key"])

    def key_works(self) -> bool:
        try:
            self.request("GET", "/v1/models")
        except StudioError:
            return False
        return True

    def accounts(self) -> list[dict[str, Any]]:
        return list((self.request("GET", "/api/accounts") or {}).get("accounts") or [])

    def create_account(self, username: str) -> dict[str, Any]:
        return self.request("POST", "/api/accounts", json={"username": username})

    def new_setup_code(self, account_id: str) -> dict[str, Any]:
        return self.request("POST", f"/api/accounts/{account_id}/setup-code")

    def set_active(self, account_id: str, active: bool) -> None:
        self.request("PATCH", f"/api/accounts/{account_id}", json={"is_active": active})

    def set_password(self, username: str, setup_code: str, password: str) -> None:
        """Finish a new account: sign in with its one-time setup code, then choose its password."""
        with Studio(self.port, transport=self.transport) as person:
            person.login(username, setup_code)
            person.request(
                "POST", "/api/auth/change-password", json={"current_password": setup_code, "new_password": password}
            )

    # --- Settings ---------------------------------------------------------------

    def prepare_routing(self) -> None:
        """Requests load the model they name, and a second model may stay loaded beside it."""
        self.request("PUT", "/api/settings/openai-auto-switch", json={"enabled": True})
        self.request("PUT", "/api/settings/multi-model", json={"enabled": True})

    # --- Models -------------------------------------------------------------------

    def downloaded(self) -> set[str]:
        """The repositories with GGUF files on this server."""
        cached = (self.request("GET", "/api/hub/cached-gguf") or {}).get("cached") or []
        return {str(item.get("repo_id")) for item in cached if isinstance(item, dict) and not item.get("partial")}

    def download(self, repo: str, quant: str, log: Log, *, timeout: float = 3600) -> None:
        params = {"repo_id": repo, "gguf_variant": quant}
        self.request("POST", "/api/hub/download", json=params)
        started = time.monotonic()
        deadline, reported = started + timeout, started
        while time.monotonic() < deadline:
            state = self.request("GET", "/api/hub/download-status", params=params) or {}
            if state.get("state") == "complete":
                return
            if state.get("state") in {"error", "cancelled", "failed"}:
                raise StudioError(f"Downloading {repo} stopped: {state.get('error') or state.get('state')}.")
            if time.monotonic() - reported >= 60:
                reported = time.monotonic()
                log(f"Still downloading {repo} ({quant}).")
            time.sleep(3)
        raise StudioError(f"Downloading {repo} did not finish within {int(timeout // 60)} minutes.")

    def delete(self, repo: str, quant: str) -> None:
        self.request("DELETE", "/api/hub/delete-cached", json={"repo_id": repo, "variant": quant})

    def chat(self, model: str) -> None:
        """Ask for a short answer; the first request loads the model, which can take minutes on a CPU."""
        answer = self.request(
            "POST",
            "/v1/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 200},
            timeout=900,
        )
        if not (answer or {}).get("choices"):
            raise StudioError(f"{model} gave no answer.")

    def embed(self, model: str) -> list[float]:
        answer = self.request("POST", "/v1/embeddings", json={"model": model, "input": "Mu3Lab readiness"}, timeout=600)
        data = (answer or {}).get("data") or []
        return list(data[0].get("embedding") or []) if data else []
