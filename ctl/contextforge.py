"""ContextForge: the one front door between chat and every app's MCP connector.

Mu3Lab registers each live connector in ContextForge once (a "gateway") and
gives it a virtual server: the set of tools that connector's app assistant
gets. The starting set comes from the connector's review (its reviewed tools
in categories that start on) or, for a connector without a review, every tool
it offers. After that, ContextForge's own screens own the set: Mu3Lab never
changes a virtual server it has already created.

Chat reaches a virtual server at ``/servers/<id>/mcp``:

* Open WebUI passes each person's own Authentik sign-in, which ContextForge
  trusts through the ``mu3lab-chat`` provider below (no keys to keep);
* LobeChat cannot, so Mu3Lab creates a key per person and virtual server.

Mu3Lab acts as ContextForge's built-in administrator, whose password only
Mu3Lab holds; people sign in through Authentik. Keys and API error bodies
never reach logs.
"""

from __future__ import annotations

from typing import Any

import httpx

from ctl.manifest.catalog import App, cached
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

ADMIN_EMAIL = "mu3lab@contextforge.example.com"
PASSWORD_ENV = "CONTEXTFORGE_ADMIN_PASSWORD"
# How chat apps reach it, on the mu3lab_mcp network.
INTERNAL = "http://contextforge:4444"
# The trusted issuer for sign-ins Open WebUI passes on with tool calls.
CHAT_PROVIDER = "mu3lab-chat"
KEY_PREFIX = "mu3lab-lobechat-"


class ContextForgeError(RuntimeError):
    """A safe message for the dashboard or worker; ``status`` is ContextForge's HTTP answer, if any."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def app() -> App:
    return next(item for item in cached().apps if "tool_gateway" in item.manifest.capabilities)


def endpoint(server_id: str) -> str:
    """A virtual server's MCP address, as chat apps reach it."""
    return f"{INTERNAL}/servers/{server_id}/mcp"


def public_origin() -> str:
    """ContextForge's private address on the tailnet, or "" before Tailscale has joined.

    Reads the name the status reconciler saved, so page loads never run host commands.
    """
    from ctl.store import records

    route = app().manifest.route
    host = str(records.get("status", "network").get("dns_name", ""))
    return f"https://{host}:{route.https_port}" if route and host else ""


def client(paths: RuntimePaths | None = None) -> ContextForge:
    """ContextForge on this server's loopback, signed in as Mu3Lab's administrator."""
    item = app()
    password = read_runtime_env((paths or RuntimePaths()).projects / item.id / ".env").get(PASSWORD_ENV, "")
    if not password:
        raise ContextForgeError("ContextForge's administrator password is missing; retry its setup.")
    return ContextForge(f"http://127.0.0.1:{item.manifest.service.local_port}", ADMIN_EMAIL, password)


def _items(data: Any, key: str) -> list[dict[str, Any]]:
    """List endpoints answer with a bare list, or a page holding one."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.get(key) or data.get("items") or data.get("data") or [])
    return []


def _field(item: dict[str, Any], snake: str) -> Any:
    """ContextForge answers in camelCase for some models and snake_case for others."""
    if snake in item:
        return item[snake]
    head, *rest = snake.split("_")
    return item.get(head + "".join(part.title() for part in rest))


class ContextForge:
    def __init__(self, origin: str, email: str, password: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.client = httpx.Client(base_url=origin.rstrip("/"), timeout=60, transport=transport)
        self.email = email
        self.password = password
        self.signed_in = False

    def __enter__(self) -> ContextForge:
        return self

    def __exit__(self, *_args: object) -> None:
        self.client.close()

    def _sign_in(self) -> None:
        data = self._send("POST", "/v1/auth/email/login", json={"email": self.email, "password": self.password})
        token = (data or {}).get("access_token") if isinstance(data, dict) else None
        if not token:
            raise ContextForgeError("ContextForge did not sign Mu3Lab in.")
        self.client.headers["Authorization"] = f"Bearer {token}"
        self.signed_in = True

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
            data = response.json() if response.content else None
        except (httpx.HTTPError, ValueError):
            raise ContextForgeError("ContextForge could not be reached.") from None
        if not response.is_success:
            raise ContextForgeError(
                f"ContextForge did not accept this request (HTTP {response.status_code}).", response.status_code
            )
        return data

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.signed_in:
            self._sign_in()
        return self._send(method, path, **kwargs)

    # --- Connectors (gateways) and their tools -----------------------------------

    def gateways(self) -> list[dict[str, Any]]:
        return _items(self.request("GET", "/v1/gateways", params={"limit": 0, "include_inactive": True}), "gateways")

    def ensure_gateway(
        self, name: str, url: str, token: str, description: str, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Register a connector, or point an existing registration at its current address, key and headers."""
        body: dict[str, Any] = {
            "name": name,
            "url": url,
            "description": description,
            "transport": "STREAMABLEHTTP",
            "visibility": "public",
        }
        if headers:
            sent = ([{"key": "Authorization", "value": f"Bearer {token}"}] if token else []) + [
                {"key": key, "value": value} for key, value in headers.items()
            ]
            body |= {"auth_type": "authheaders", "auth_headers": sent}
        elif token:
            body |= {"auth_type": "bearer", "auth_token": token}
        current = next((item for item in self.gateways() if item.get("name") == name), None)
        if current is None:
            return self.request("POST", "/v1/gateways", json=body)
        updated = self.request("PUT", f"/v1/gateways/{current['id']}", json=body)
        self.set_gateway_active(str(current["id"]), True)
        return updated or current

    def set_gateway_active(self, gateway_id: str, active: bool) -> None:
        self.request("POST", f"/v1/gateways/{gateway_id}/state", params={"activate": str(active).lower()})

    def tools(self, gateway_id: str) -> list[dict[str, Any]]:
        params = {"gateway_id": gateway_id, "limit": 0, "include_inactive": True}
        return _items(self.request("GET", "/v1/tools", params=params), "tools")

    # --- Virtual servers -----------------------------------------------------------

    def servers(self) -> list[dict[str, Any]]:
        return _items(self.request("GET", "/v1/servers", params={"limit": 0, "include_inactive": True}), "servers")

    def ensure_server(self, name: str, description: str, tool_ids: list[str]) -> dict[str, Any]:
        """Create a virtual server once; an existing one is left exactly as operators made it."""
        current = next((item for item in self.servers() if item.get("name") == name), None)
        if current is not None:
            return current
        server = {"name": name, "description": description, "associated_tools": tool_ids}
        return self.request("POST", "/v1/servers", json={"server": server, "visibility": "public"})

    # --- People and their keys (LobeChat) -----------------------------------------

    def ensure_user(self, email: str, full_name: str, password: str) -> None:
        """An account for someone who has not signed in yet; Authentik sign-in links to it by email.

        The password is random and never shown: ContextForge refuses password sign-in to
        anyone but its administrator while Authentik sign-in is on.
        """
        if self.user(email) is not None:
            return
        self.request(
            "POST",
            "/v1/auth/email/admin/users",
            json={"email": email, "password": password, "full_name": full_name, "is_admin": False},
        )

    def user(self, email: str) -> dict[str, Any] | None:
        """Someone's ContextForge account, or ``None`` when there is none; any other failure raises."""
        try:
            data = self.request("GET", f"/v1/auth/email/admin/users/{email}")
        except ContextForgeError as exc:
            if exc.status == 404:
                return None
            raise
        return data if isinstance(data, dict) else {}

    def remove_admin(self, email: str) -> None:
        """Take ContextForge's administrator rights from someone, if they have an account and the rights."""
        current = self.user(email)
        if current is None or not _field(current, "is_admin"):
            return
        self.request("PATCH", f"/v1/auth/email/admin/users/{email}", json={"is_admin": False})

    def keys(self, email: str = "") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": 1000}
        if email:
            params["user_email"] = email
        return [
            item
            for item in _items(self.request("GET", "/v1/tokens/admin/all", params=params), "tokens")
            if str(item.get("name", "")).startswith(KEY_PREFIX)
        ]

    def create_key(self, email: str, name: str, server_id: str) -> tuple[str, str]:
        """A key that acts as ``email`` and reaches only one virtual server: its ID and its text."""
        body = {
            "name": name,
            "description": "Created by Mu3Lab for LobeChat",
            "user_email": email,
            "scope": {"server_id": server_id},
        }
        data = self.request("POST", "/v1/tokens", json=body) or {}
        token = data.get("access_token") if isinstance(data, dict) else None
        key = data.get("token") if isinstance(data, dict) else None
        if not token or not isinstance(key, dict) or not key.get("id"):
            raise ContextForgeError("ContextForge did not return the new key.")
        return str(key["id"]), str(token)

    def revoke_key(self, key_id: str) -> None:
        self.request("DELETE", f"/v1/tokens/admin/{key_id}")

    # --- Sign-in providers -----------------------------------------------------------

    def ensure_chat_provider(self, dns_name: str) -> None:
        """Trust the Authentik sign-ins Open WebUI passes on with each person's tool calls.

        Only tokens Authentik issued to Open WebUI are accepted (``api_audience``).
        Its sign-in button is not for people: the client secret is not ContextForge's.
        """
        from ctl.chat_ops import by_workspace

        chat = by_workspace("open_webui")
        oidc = chat.manifest.sign_in.oidc
        if oidc is None:
            raise ContextForgeError("Open WebUI has no Authentik sign-in to trust.")
        client_id = oidc.client_id
        base = f"https://{dns_name}/application/o"
        # Authentik's per-application issuer (ctl/authentik_blueprints.py names the application).
        slug = f"mu3lab-{chat.id}"
        body = {
            "name": CHAT_PROVIDER,
            "display_name": "Mu3Lab chat (used by Open WebUI; not for signing in here)",
            "provider_type": "oidc",
            "client_id": client_id,
            "client_secret": "not-used-for-sign-in",
            "authorization_url": f"{base}/authorize/",
            "token_url": f"{base}/token/",
            "userinfo_url": f"{base}/userinfo/",
            "issuer": f"{base}/{slug}/",
            "jwks_uri": f"{base}/{slug}/jwks/",
            "scope": "openid profile email",
            "auto_create_users": True,
            "provider_metadata": {"groups_claim": "groups", "admin_groups": ["mu3lab-operators"]},
            "trusted_for_api_auth": True,
            "api_audience": client_id,
        }
        existing = {str(item.get("id")) for item in _items(self.request("GET", "/v1/auth/sso/admin/providers"), "")}
        if CHAT_PROVIDER in existing:
            self.request("PUT", f"/v1/auth/sso/admin/providers/{CHAT_PROVIDER}", json=body)
        else:
            self.request("POST", "/v1/auth/sso/admin/providers", json={"id": CHAT_PROVIDER, **body})


def tool_ids(tools: list[dict[str, Any]], wanted: set[str] | None) -> list[str]:
    """ContextForge IDs of a connector's tools by their own names; ``None`` means all of them."""
    return [
        str(tool["id"])
        for tool in tools
        if tool.get("id") and (wanted is None or str(_field(tool, "original_name") or tool.get("name")) in wanted)
    ]
