"""Navidrome's people are created through its own API.

The browser trusts Authentik (Caddy passes the username), but phone apps sign
in with a Navidrome password. So Mu3Lab keeps its own Navidrome administrator
(``mu3lab-service``, a generated password) and gives every household member an
account with a generated password, saved to their vault. Administrators in
Mu3Lab are administrators in Navidrome. A deactivated person's password is
replaced with one nobody knows, which keeps their playlists and history for
when they come back.
"""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.request
from typing import Any

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError, request
from ctl.store import person_logins

SERVICE = "mu3lab-service"
PASSWORD_ENV = "NAVIDROME_ADMIN_PASSWORD"
READY_SECONDS = 180
HEADER = "x-nd-authorization"


# Navidrome allows a few sign-ins per 20 seconds from one address (ND_AUTHREQUESTLIMIT).
LIMIT_WINDOW = 21


def _post(port: int, path: str, data: dict[str, str]) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(data).encode(),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (OSError, ValueError) as exc:
        raise LoopbackError(f"Navidrome did not answer {path}.") from exc


def _token(port: int, password: str) -> str:
    """Sign in as Mu3Lab's administrator, waiting out Navidrome's sign-in limit once."""
    for attempt in (1, 2):
        status, body = _post(port, "/auth/login", {"username": SERVICE, "password": password})
        if status == 429 and attempt == 1:
            time.sleep(LIMIT_WINDOW)
            continue
        if status == 200 and isinstance(body, dict) and body.get("token"):
            return str(body["token"])
        break
    raise LoopbackError(f"Navidrome did not accept Mu3Lab's administrator (HTTP {status}).")


def _api(port: int, token: str, path: str, *, method: str = "GET", data: Any = None) -> Any:
    """Navidrome's admin API takes its token in its own header, not Authorization."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Accept": "application/json", "Content-Type": "application/json", HEADER: f"Bearer {token}"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise LoopbackError(f"Navidrome refused {method} {path.split('?')[0]} (HTTP {exc.code}).") from None
    except OSError as exc:
        raise LoopbackError(f"Navidrome did not answer {path.split('?')[0]}.") from exc
    return json.loads(raw) if raw else None


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        port = ctx.app.manifest.service.local_port
        password = ctx.env()[PASSWORD_ENV]
        deadline = time.monotonic() + READY_SECONDS
        while True:
            try:
                request(port, "/ping", json_reply=False)
                break
            except LoopbackError:
                if time.monotonic() > deadline:
                    ctx.fail("account_bootstrap", "account_provisioning_failed", "Navidrome did not start.")
                time.sleep(3)
        # Only possible while Navidrome has no users at all; it signs the new administrator in.
        status, body = _post(port, "/auth/createAdmin", {"username": SERVICE, "password": password})
        if status == 200 and isinstance(body, dict) and body.get("token"):
            return "Navidrome is ready; Mu3Lab creates each person's account next."
        try:
            _token(port, password)
        except LoopbackError as exc:
            ctx.fail(
                "account_bootstrap",
                "account_provisioning_failed",
                f"Navidrome's Mu3Lab administrator could not be created or signed in: {exc}",
            )
        return "Navidrome's Mu3Lab administrator already exists."

    def sync_people(self, ctx: HookContext, people: list[dict[str, Any]]) -> None:
        port = ctx.app.manifest.service.local_port
        paths = ctx.facts.paths if ctx.facts else None
        token = _token(port, ctx.env()[PASSWORD_ENV])
        users = {str(user["userName"]).casefold(): user for user in _api(port, token, "/api/user") or []}
        for person in people:
            username = person["username"]
            if username.casefold() == SERVICE:
                continue
            user = users.get(username.casefold())
            login = person_logins.get(ctx.app.id, username, paths)
            if not person["member"]:
                if user and login:
                    # Lock phone apps out; the browser is already closed by Authentik.
                    _api(
                        port,
                        token,
                        f"/api/user/{user['id']}",
                        method="PUT",
                        data=_body(user, secrets.token_urlsafe(24)),
                    )
                    person_logins.forget(ctx.app.id, username, paths)
                continue
            password = login["password"] if login else secrets.token_urlsafe(24)
            if user is None:
                body = {
                    "userName": username,
                    "name": person["name"],
                    "email": person["email"],
                    "isAdmin": person["admin"],
                    "password": password,
                }
                _api(port, token, "/api/user", method="POST", data=body)
            elif login is None or bool(user.get("isAdmin")) != person["admin"]:
                # A first visit through the browser created the account, or the role changed.
                _api(port, token, f"/api/user/{user['id']}", method="PUT", data=_body(user, password, person["admin"]))
            if login is None:
                person_logins.put(ctx.app.id, username, username, password, paths)


def _body(user: dict[str, Any], password: str, admin: bool | None = None) -> dict[str, Any]:
    return {
        "id": user["id"],
        "userName": user["userName"],
        "name": user.get("name") or user["userName"],
        "email": user.get("email") or "",
        "isAdmin": bool(user.get("isAdmin")) if admin is None else admin,
        "password": password,
    }
