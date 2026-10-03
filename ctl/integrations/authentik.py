"""Authentik through its documented REST API (``/api/v3``), and nothing else.

Mu3Lab's API token comes from Authentik's own automated-install settings
(``AUTHENTIK_BOOTSTRAP_TOKEN``, read on its first start). Configuration is
delivered as Blueprint documents applied through the import endpoint, which
applies them immediately and stores nothing, so the order is Mu3Lab's and no
watched folder can re-apply a stale file.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

TOKEN_ENV = "AUTHENTIK_BOOTSTRAP_TOKEN"
DEFAULT_URL = "http://127.0.0.1:9001"
# Django's PBKDF2 format; Authentik validates and stores it as given, so the
# owner's password never has to be written down in plain text.
PBKDF2_ITERATIONS = 1_000_000


class AuthentikError(RuntimeError):
    """Authentik refused or could not be reached; the message is fit to show."""


def password_hash(password: str, *, salt: str | None = None, iterations: int = PBKDF2_ITERATIONS) -> str:
    """The ``pbkdf2_sha256`` hash Authentik accepts in ``AUTHENTIK_BOOTSTRAP_PASSWORD_HASH``."""
    salt = salt or secrets.token_urlsafe(16).replace("-", "").replace("_", "")[:22]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode()}"


def api_token(paths: RuntimePaths | None = None) -> str:
    return read_runtime_env((paths or RuntimePaths()).projects / "authentik" / ".env").get(TOKEN_ENV, "")


@dataclass
class Authentik:
    token: str
    base_url: str = DEFAULT_URL
    timeout: float = 30.0
    transport: httpx.BaseTransport | None = None

    @classmethod
    def runtime(cls, paths: RuntimePaths | None = None) -> Authentik:
        token = api_token(paths)
        if not token:
            raise AuthentikError("Mu3Lab has no Authentik API token yet; run ./install.sh to finish sign-in setup.")
        return cls(token)

    # --- transport -------------------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url.rstrip("/") + "/api/v3",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            timeout=self.timeout,
            transport=self.transport,
        )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            with self._client() as client:
                response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AuthentikError(f"Authentik could not be reached ({type(exc).__name__}).") from None
        if response.status_code >= 400:
            raise AuthentikError(f"Authentik refused {method} {path} (HTTP {response.status_code}).")
        return response.json() if response.content else None

    def _all(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            data = self.request("GET", path, params={**params, "page": page, "page_size": 100})
            yield from data.get("results", [])
            if not data.get("pagination", {}).get("next"):
                return
            page += 1

    def ready(self) -> bool:
        try:
            self.request("GET", "/core/users/me/")
        except AuthentikError:
            return False
        return True

    # --- Blueprints --------------------------------------------------------------

    def apply_blueprint(self, name: str, content: str) -> None:
        """Apply one Blueprint document now, synchronously, without leaving a stored record.

        Authentik's import endpoint validates and applies in the request; a
        stored instance would instead be applied later by its worker, in any
        order, which is exactly what made removals undo reinstalls.
        """
        try:
            with self._client() as client:
                response = client.post(
                    "/managed/blueprints/import/",
                    files={"file": (f"{name}.yaml", content.encode(), "application/yaml")},
                )
        except httpx.HTTPError as exc:
            raise AuthentikError(f"Authentik could not be reached ({type(exc).__name__}).") from None
        result = response.json() if response.content else {}
        if response.status_code >= 400 or not result.get("success"):
            reasons = [str(log.get("event", "")) for log in result.get("logs", []) if isinstance(log, dict)]
            raise AuthentikError(f"Authentik rejected {name}: " + ("; ".join(reasons[:3]) or f"HTTP {response.status_code}"))

    # --- People ----------------------------------------------------------------

    def users(self, **filters: Any) -> list[dict[str, Any]]:
        return list(self._all("/core/users/", {"include_groups": "true", **filters}))

    def user(self, username: str) -> dict[str, Any] | None:
        return next((item for item in self.users(username=username) if item["username"] == username), None)

    def create_user(self, username: str, name: str, email: str) -> dict[str, Any]:
        body = {"username": username, "name": name, "email": email, "is_active": True, "type": "internal"}
        return self.request("POST", "/core/users/", json=body)

    def update_user(self, pk: int, **fields: Any) -> dict[str, Any]:
        return self.request("PATCH", f"/core/users/{pk}/", json=fields)

    def set_password(self, pk: int, password: str) -> None:
        self.request("POST", f"/core/users/{pk}/set_password/", json={"password": password})

    def recovery_link(self, pk: int, hours: int) -> str:
        data = self.request("POST", f"/core/users/{pk}/recovery/", json={"token_duration": f"hours={hours}"})
        return str(data["link"])

    def group(self, name: str, *, create: bool = True) -> dict[str, Any] | None:
        found = next((item for item in self._all("/core/groups/", {"name": name}) if item["name"] == name), None)
        if found is None and create:
            found = self.request("POST", "/core/groups/", json={"name": name})
        return found

    def add_to_group(self, group_name: str, user_pk: int) -> None:
        group = self.group(group_name)
        assert group is not None
        self.request("POST", f"/core/groups/{group['pk']}/add_user/", json={"pk": user_pk})

    def remove_from_group(self, group_name: str, user_pk: int) -> None:
        group = self.group(group_name, create=False)
        if group is not None:
            self.request("POST", f"/core/groups/{group['pk']}/remove_user/", json={"pk": user_pk})
