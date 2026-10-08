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
import json
import secrets
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from ctl.jobs import redact_data
from ctl.platform_apps import by_capability
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

TOKEN_ENV = "AUTHENTIK_BOOTSTRAP_TOKEN"
DEFAULT_URL = "http://127.0.0.1:9001"
# An import is one synchronous transaction that took up to ~45s on a loaded
# server. Abandoning it does not cancel it: it keeps its locks and the next
# import queues behind it, so waiting is the only way to get a true answer.
# A stopped Authentik still fails fast on connect.
BLUEPRINT_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
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
    return read_runtime_env((paths or RuntimePaths()).projects / by_capability("identity_provider").id / ".env").get(
        TOKEN_ENV, ""
    )


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
            detail = ""
            if response.headers.get("content-type", "").startswith("application/json"):
                try:
                    errors = response.json()
                except ValueError:
                    errors = {}
                if isinstance(errors, dict):
                    reasons = {key: errors[key] for key in ("detail", "non_field_errors") if key in errors}
                    if reasons:
                        detail = " " + json.dumps(redact_data(reasons), ensure_ascii=False).replace(
                            self.token, "[redacted]"
                        )
            raise AuthentikError(f"Authentik refused {method} {path} (HTTP {response.status_code}).{detail}")
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
                    timeout=BLUEPRINT_TIMEOUT,
                )
        except httpx.HTTPError as exc:
            raise AuthentikError(f"Authentik could not be reached ({type(exc).__name__}).") from None
        result = response.json() if response.content else {}
        if response.status_code >= 400 or not result.get("success"):
            logs = [
                log
                for log in result.get("logs", [])
                if isinstance(log, dict)
                and str(log.get("log_level", "")).lower() in {"warning", "error", "warn", "critical"}
            ]
            detail = json.dumps(redact_data(logs), ensure_ascii=False) if logs else f"HTTP {response.status_code}"
            raise AuthentikError(f"Authentik rejected {name}: {detail}")

    def wait_for_defaults(self, timeout: float = 300) -> None:
        """A healthy fresh server may still be waiting for the worker's built-in blueprints."""
        defaults = (
            ("/flows/instances/", "slug", "default-authentication-flow"),
            ("/flows/instances/", "slug", "default-provider-authorization-implicit-consent"),
            ("/flows/instances/", "slug", "default-provider-invalidation-flow"),
            ("/crypto/certificatekeypairs/", "name", "authentik Self-signed Certificate"),
        )
        deadline = time.monotonic() + timeout
        while True:
            try:
                ready = all(
                    any(
                        item.get(key) == value
                        for item in self.request("GET", path, params={key: value}).get("results", [])
                    )
                    for path, key, value in defaults
                )
            except AuthentikError:
                ready = False
            if ready:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthentikError(
                    "Authentik's default sign-in flows and signing certificate are not ready yet. Try setup again."
                )
            time.sleep(min(2, remaining))

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

    def end_sessions(self, username: str) -> int:
        """Delete a person's Authentik browser sessions and their API/app-password tokens."""

        def owned(item: dict[str, Any], field: str) -> bool:
            # The filter is authoritative; an embedded owner, when present, must agree.
            owner = item.get(field)
            return not isinstance(owner, dict) or owner.get("username") == username

        ended = 0
        params = {"user__username": username}
        for session in list(self._all("/core/authenticated_sessions/", params)):
            if owned(session, "user"):
                self.request("DELETE", f"/core/authenticated_sessions/{session['uuid']}/")
                ended += 1
        for token in list(self._all("/core/tokens/", params)):
            if owned(token, "user_obj"):
                self.request("DELETE", f"/core/tokens/{token['identifier']}/")
                ended += 1
        return ended

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
