"""LobeHub's supported device authorization and per-person v1 API.

Request shapes are pinned by tests/fixtures/lobehub-openapi-2.2.18.json.
No database access is used. Tokens and API error bodies never reach logs.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

import httpx

SCOPES = ["agent:read", "agent:write", "mcp:read", "mcp:write", "chat:read"]


class ChatError(RuntimeError):
    """A safe message for the dashboard or worker."""


class AssistantSyncError(ChatError):
    """Some assistants failed; ``managed`` still records the ones that synced."""

    def __init__(self, message: str, managed: dict[str, dict[str, str]]) -> None:
        super().__init__(message)
        self.managed = managed


class LobeHub:
    def __init__(self, origin: str, key: str = "", *, transport: httpx.BaseTransport | None = None) -> None:
        self.origin = origin.rstrip("/")
        self.client = httpx.Client(
            base_url=self.origin,
            timeout=30,
            transport=transport,
            headers={"Authorization": f"Bearer {key}"} if key else {},
        )

    def __enter__(self) -> LobeHub:
        return self

    def __exit__(self, *_args: object) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise ChatError("Chat could not be reached. Try connecting again.") from None
        if not response.is_success or not isinstance(data, dict) or not data.get("success"):
            raise ChatError(
                f"Chat did not accept this request (HTTP {response.status_code}). Reconnect chat if its key expired."
            )
        return data.get("data")

    # Fixed routes of LobeHub's built-in OIDC provider (src/libs/oidc-provider/provider.ts).
    # Its discovery document needs a signed-in session; these two endpoints are public,
    # which is how LobeHub's own CLI uses them.
    DEVICE_AUTHORIZATION_PATH = "/oidc/device/auth"
    TOKEN_PATH = "/oidc/token"

    def start_device_login(self) -> dict[str, Any]:
        try:
            response = self.client.post(
                self.DEVICE_AUTHORIZATION_PATH,
                data={"client_id": "lobehub-cli", "scope": "openid profile email offline_access"},
            )
            response.raise_for_status()
            data = response.json()
            if not all(data.get(k) for k in ("verification_uri_complete", "user_code", "device_code", "expires_in")):
                raise ValueError("incomplete reply")
            if urlsplit(data["verification_uri_complete"])[:2] != urlsplit(self.origin)[:2]:
                raise ValueError("unexpected verification page")
            return data
        except (httpx.HTTPError, ValueError):
            raise ChatError("Chat could not start its one-time connection.") from None

    def poll_device_login(self, device_code: str) -> dict[str, Any]:
        try:
            response = self.client.post(
                self.TOKEN_PATH,
                data={
                    "client_id": "lobehub-cli",
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise ChatError("Chat could not check your approval. Try again.") from None
        if data.get("error") in {"authorization_pending", "slow_down"}:
            return {"state": data["error"]}
        if not response.is_success or not data.get("access_token"):
            raise ChatError("Chat approval expired or was declined. Connect chat again.")
        return {"state": "approved", "access_token": data["access_token"]}

    def create_key(self, access_token: str) -> str:
        data = self.request(
            "POST",
            "/api/v1/api-keys",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"name": "Mu3Lab", "scopes": SCOPES, "expiresAt": None},
        )
        key = data.get("key") if isinstance(data, dict) else None
        if not isinstance(key, str) or not key.startswith("sk-lh-"):
            raise ChatError("Chat did not return a connection key. Connect again.")
        return key

    def agents(self) -> list[dict[str, Any]]:
        results = []
        page = 1
        while True:
            data = self.request("GET", "/api/v1/agents", params={"page": page, "pageSize": 100})
            rows = data["agents"]
            results.extend(rows)
            if not rows or len(results) >= data.get("total", len(results)):
                return results
            page += 1

    def ensure_assistants(
        self, desired: list[dict[str, Any]], managed: dict[str, dict[str, str]], *, installed: set[str] | None = None
    ) -> dict[str, dict[str, str]]:
        """Upsert by saved ID, then title; only retire our own empty agents.

        Saved instruction text lets us preserve a person's edits on later syncs.
        Stale connectors are disabled even when conversation history is retained.
        """
        agents = self.agents()
        servers = self.request("GET", "/api/v1/mcp-servers")
        updated = dict(managed)
        wanted = {item["id"] for item in desired}
        installed = installed if installed is not None else wanted
        failures: list[str] = []
        for item in desired:
            # One app's failing connector must not keep the person's other assistants away.
            try:
                updated[item["id"]] = self._upsert_assistant(item, managed.get(item["id"], {}), agents, servers)
            except ChatError as exc:
                failures.append(f"{item['title']}: {exc}")
        for app_id, old in managed.items():
            if app_id in wanted:
                continue
            try:
                if self._retire_assistant(app_id, old, agents, servers, app_id in installed):
                    updated.pop(app_id, None)
            except ChatError as exc:
                failures.append(f"{app_id}: {exc}")
        if failures:
            detail = (
                failures[0] if len(failures) == 1 else f"{len(failures)} assistants were not updated. {failures[0]}"
            )
            raise AssistantSyncError(detail, updated)
        return updated

    def _upsert_assistant(
        self, item: dict[str, Any], old: dict[str, str], agents: list[dict[str, Any]], servers: list[dict[str, Any]]
    ) -> dict[str, str]:
        slug = "mu3lab-" + item["id"]
        agent = next((a for a in agents if a["id"] == old.get("agent_id")), None)
        if not agent:
            agent = next((a for a in agents if a.get("title") == item["title"]), None)
        server = next((s for s in servers if s["identifier"] == slug), None)
        server_body = {
            "name": item["title"],
            "serverUrl": item["url"],
            "isEnabled": True,
            "credentials": {"type": "bearer", "token": item["token"]},
        }
        if server:
            server = self.request("PATCH", "/api/v1/mcp-servers/" + quote(server["id"], safe=""), json=server_body)
        else:
            server = self.request("POST", "/api/v1/mcp-servers", json={"identifier": slug, **server_body})
        self.request("POST", "/api/v1/mcp-servers/" + quote(server["id"], safe="") + "/sync")
        body = {
            "title": item["title"],
            "description": item["description"],
            "model": "mu3lab-chat",
            "provider": "openai",
            "plugins": [{"identifier": slug, "mode": "pinned"}],
        }
        instructions = item["instructions"]
        if not agent or agent.get("systemRole") in {old.get("instructions"), instructions}:
            body["systemRole"] = instructions
        if agent:
            agent = self.request("PATCH", "/api/v1/agents/" + quote(agent["id"], safe=""), json=body)
        else:
            agent = self.request("POST", "/api/v1/agents", json={**body, "systemRole": instructions})
        return {"agent_id": agent["id"], "instructions": instructions}

    def _retire_assistant(
        self,
        app_id: str,
        old: dict[str, str],
        agents: list[dict[str, Any]],
        servers: list[dict[str, Any]],
        still_installed: bool,
    ) -> bool:
        """Detach or remove an assistant no longer wanted; True when it should be forgotten."""
        server = next((s for s in servers if s["identifier"] == "mu3lab-" + app_id), None)
        if server:
            self.request("PATCH", "/api/v1/mcp-servers/" + quote(server["id"], safe=""), json={"isEnabled": False})
        if not any(agent["id"] == old.get("agent_id") for agent in agents):
            # The person deleted this assistant themselves; there is nothing left to retire.
            return True
        agent_id = quote(old["agent_id"], safe="")
        if still_installed:
            self.request("PATCH", "/api/v1/agents/" + agent_id, json={"plugins": []})
            return False
        topics = self.request("GET", "/api/v1/topics", params={"agentId": old["agent_id"], "pageSize": 1})
        if not topics.get("topics") and not topics.get("total", 0):
            self.request("DELETE", "/api/v1/agents/" + agent_id)
            return True
        self.request("PATCH", "/api/v1/agents/" + agent_id, json={"plugins": []})
        return False
