"""LibreChat's admin API, used as Mu3Lab's own LibreChat administrator.

LibreChat has no administrator key. Mu3Lab's administrator account
(``apps/librechat/scripts/service-admin.js``) signs in with its generated password,
on this server's loopback only: the private address blocks password sign-in.

What Mu3Lab keeps in step are configuration profiles layered on ``librechat.yaml``
(LibreChat's admin configuration API): the household's in the Base profile, and
each person's own in theirs, keyed by their LibreChat account. A profile's
``modelSpecs`` (the assistants in the menu) replaces the one beneath it, so each
person's profile carries their whole menu; its ``mcpServers`` (the app tools) adds
to the ones beneath it. Every app tool passes the signed-in person's own Authentik
sign-in on to ContextForge, as Open WebUI's do.

API error bodies and the password never reach logs.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import quote

import httpx

# The account service-admin.js makes; its password is LIBRECHAT_ADMIN_PASSWORD.
ADMIN_EMAIL = "mu3lab@librechat.example.com"
PASSWORD_ENV = "LIBRECHAT_ADMIN_PASSWORD"
BASE = ("role", "__base__")
# LibreChat's sign-ins last 15 minutes and it allows a few password sign-ins every few
# minutes: one sign-in serves every sync for a while.
TOKEN_SECONDS = 10 * 60
_TOKENS: dict[str, tuple[str, float]] = {}
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0 Mu3Lab"
_LOCK = threading.Lock()


class ChatError(RuntimeError):
    """A safe message for the dashboard or worker."""


class LibreChat:
    def __init__(self, origin: str, password: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.origin = origin.rstrip("/")
        self.password = password
        # LibreChat's agent routes refuse anything that does not name a browser (its uaParser).
        self.client = httpx.Client(
            base_url=self.origin, timeout=30, transport=transport, headers={"User-Agent": USER_AGENT}
        )

    def __enter__(self) -> LibreChat:
        return self

    def __exit__(self, *_args: object) -> None:
        self.client.close()

    def _sign_in(self, *, fresh: bool = False) -> str:
        with _LOCK:
            token, until = _TOKENS.get(self.origin, ("", 0.0))
            if token and not fresh and until > time.monotonic():
                return token
            try:
                response = self.client.post(
                    "/api/admin/login/local", json={"email": ADMIN_EMAIL, "password": self.password}
                )
                data = response.json()
            except (httpx.HTTPError, ValueError):
                raise ChatError("LibreChat could not be reached.") from None
            found = data.get("token") if isinstance(data, dict) else None
            if not response.is_success or not isinstance(found, str) or not found:
                raise ChatError(
                    f"LibreChat refused Mu3Lab's administrator (HTTP {response.status_code}); retry LibreChat's setup."
                )
            _TOKENS[self.origin] = (found, time.monotonic() + TOKEN_SECONDS)
            return found

    def request(self, method: str, path: str, *, missing_ok: bool = False, **kwargs: Any) -> Any:
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self._sign_in(fresh=bool(attempt))}"}
            try:
                response = self.client.request(method, path, headers=headers, **kwargs)
                data = response.json() if response.content else None
            except (httpx.HTTPError, ValueError):
                raise ChatError("LibreChat could not be reached.") from None
            if response.status_code == 401 and not attempt:
                continue  # the saved sign-in ended early (LibreChat restarted): sign in again
            if response.status_code == 404 and missing_ok:
                return None
            if not response.is_success:
                code = (data or {}).get("code") if isinstance(data, dict) else ""
                raise ChatError(
                    f"LibreChat did not accept this request (HTTP {response.status_code}{f', {code}' if code else ''})."
                )
            return data
        raise ChatError("LibreChat refused Mu3Lab's administrator.")

    def users(self) -> list[dict[str, Any]]:
        """Everyone with a LibreChat account: ``id``, ``email``, ``role``."""
        found: list[dict[str, Any]] = []
        while True:
            data = self.request("GET", "/api/admin/users", params={"limit": 100, "offset": len(found)}) or {}
            rows = list(data.get("users") or [])
            found.extend(rows)
            if not rows or len(found) >= int(data.get("total") or 0):
                return found

    def profile(self, principal_type: str, principal_id: str) -> dict[str, Any] | None:
        """A configuration profile's overrides, or None when there is none."""
        data = self.request(
            "GET", f"/api/admin/config/{principal_type}/{quote(principal_id, safe='')}", missing_ok=True
        )
        if not isinstance(data, dict):
            return None
        config = data.get("config") or {}
        return dict(config.get("overrides") or {})

    def save_profile(self, principal_type: str, principal_id: str, overrides: dict[str, Any]) -> None:
        self.request(
            "PUT", f"/api/admin/config/{principal_type}/{quote(principal_id, safe='')}", json={"overrides": overrides}
        )

    def delete_profile(self, principal_type: str, principal_id: str) -> None:
        self.request("DELETE", f"/api/admin/config/{principal_type}/{quote(principal_id, safe='')}", missing_ok=True)

    def effective(self, user_id: str) -> dict[str, Any]:
        """What LibreChat serves this person now: librechat.yaml with every profile that applies to them."""
        data = self.request("GET", "/api/admin/config/effective", params={"userId": user_id})
        return dict((data or {}).get("resolved") or {}) if isinstance(data, dict) else {}

    # --- App agents (the main assistant's subagents) ---------------------------------------

    def agent(self, agent_id: str) -> dict[str, Any] | None:
        """One agent Mu3Lab's administrator made, or None when it is gone."""
        data = self.request("GET", f"/api/agents/{quote(agent_id, safe='')}", missing_ok=True)
        return data if isinstance(data, dict) else None

    def create_agent(self, body: dict[str, Any]) -> dict[str, Any]:
        """A new agent owned by Mu3Lab's administrator: its ``id`` (agent_...) and ``_id`` (for sharing)."""
        data = self.request("POST", "/api/agents", json=body)
        if not isinstance(data, dict) or not data.get("id") or not data.get("_id"):
            raise ChatError("LibreChat did not return the new app agent.")
        return data

    def update_agent(self, agent_id: str, body: dict[str, Any]) -> None:
        self.request("PATCH", f"/api/agents/{quote(agent_id, safe='')}", json=body)

    def delete_agent(self, agent_id: str) -> None:
        self.request("DELETE", f"/api/agents/{quote(agent_id, safe='')}", missing_ok=True)

    def share_agent(self, object_id: str, user_ids: list[str]) -> None:
        """Let these LibreChat accounts use (not edit) one agent; nobody else but its owner can."""
        self.request(
            "PUT",
            f"/api/permissions/agent/{quote(object_id, safe='')}",
            json={
                "updated": [{"type": "user", "id": uid, "accessRoleId": "agent_viewer"} for uid in user_ids],
                "removed": [],
                "public": False,
            },
        )

    def set_role_permissions(self, role: str, permissions: dict[str, dict[str, bool]]) -> None:
        """Change what everyone with a LibreChat role may do (``{"MARKETPLACE": {"USE": False}}``)."""
        self.request("PATCH", f"/api/admin/roles/{quote(role, safe='')}/permissions", json={"permissions": permissions})
