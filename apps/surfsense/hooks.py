"""SurfSense has no single sign-on, so Mu3Lab registers everyone's account itself.

The owner's account is made at install and its generated password goes to their
vault (``save_login_to_vault``). Every other household member gets their own
account and private workspace the same way (``per_person_logins``), saved to
their own vault, for Bitwarden to fill; Authentik still guards the address.
SurfSense has no administrator API, so a removed person's account is switched
off in its database, and on again if they come back.
"""

from __future__ import annotations

import json
import secrets
import urllib.parse
import urllib.request
from http.cookies import CookieError, SimpleCookie
from typing import Any

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError, request
from ctl.store import onboarding as onboarding_state
from ctl.store import person_logins

SESSION_COOKIE = "surfsense_session"


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        assert ctx.owner is not None and ctx.facts is not None
        port = ctx.app.manifest.service.local_port
        paths = ctx.facts.paths
        email = ctx.owner["email"]
        if onboarding_state.read(ctx.app.id, paths).get("provisioned"):
            return "The SurfSense account already exists."
        login = onboarding_state.prepare_login(ctx.app.id, paths)
        try:
            # Registration runs SurfSense's own default-workspace hook.
            request(port, "/auth/register", method="POST", data={"email": email, "password": login["password"]})
        except LoopbackError:
            pass  # an earlier attempt may have registered it; the login below decides
        token = _login(port, email, login["password"])
        if not token:
            ctx.fail(
                "account_bootstrap", "account_provisioning_failed", "SurfSense did not accept the generated login."
            )
        try:
            profile = request(port, "/users/me", token=token)
            if str(profile.get("email", "")).lower() != email.lower() or not profile.get("is_active"):
                ctx.fail("account_bootstrap", "account_provisioning_failed", "SurfSense did not confirm the account.")
            workspaces = request(port, "/api/v1/workspaces?owned_only=true", token=token)
            if isinstance(workspaces, list) and not workspaces:
                # Recover a failed registration hook without showing a setup form.
                body = {"name": "Personal", "description": "Your personal workspace", "citations_enabled": True}
                request(port, "/api/v1/workspaces", method="POST", data=body, token=token)
        except LoopbackError as exc:
            ctx.fail("account_bootstrap", "account_provisioning_failed", str(exc))
        onboarding_state.complete_login(ctx.app.id, email, ctx.facts.public_url(ctx.app), paths)
        return "SurfSense account created; its login is saved to your vault."

    def sync_people(self, ctx: HookContext, people: list[dict[str, Any]]) -> None:
        port = ctx.app.manifest.service.local_port
        paths = ctx.facts.paths if ctx.facts else None
        # The owner's account and vault login come from bootstrap_account.
        owner = str((onboarding_state.read(ctx.app.id, paths).get("owner") or {}).get("email") or "").casefold()
        known: list[tuple[str, bool]] = []
        for person in people:
            email = person["email"]
            if not email or email.casefold() == owner:
                continue
            login = person_logins.get(ctx.app.id, person["username"], paths)
            if login:
                known.append((str(login["login"]), person["member"]))
                continue
            if not person["member"]:
                continue
            password = secrets.token_urlsafe(24)
            try:
                # Registration also makes their private "Personal" workspace.
                request(port, "/auth/register", method="POST", data={"email": email, "password": password})
            except LoopbackError as exc:
                raise RuntimeError(f"SurfSense did not create {person['name']}'s account: {exc}") from exc
            if not _login(port, email, password):
                raise RuntimeError(f"SurfSense did not accept {person['name']}'s new login.")
            person_logins.put(ctx.app.id, person["username"], email, password, paths)
        if known:
            _set_active(ctx, known)


def _set_active(ctx: HookContext, accounts: list[tuple[str, bool]]) -> None:
    """Switch accounts on or off; psql quotes each email, so none is read as SQL."""
    args: list[str] = []
    lines: list[str] = []
    for index, (email, active) in enumerate(accounts):
        args += ["-v", f"e{index}={email}", "-v", f"a{index}={'true' if active else 'false'}"]
        lines.append(f"UPDATE \"user\" SET is_active = :'a{index}'::boolean WHERE lower(email) = lower(:'e{index}');")
    env = ctx.env()
    interpreter = [
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        env.get("DB_USER", "surfsense"),
        "-d",
        env.get("DB_NAME", "surfsense"),
    ]
    result = ctx.compose.run_script("db", interpreter, "\n".join(lines) + "\n", ctx.log, args=args, timeout=60)
    if not result.ok:
        raise RuntimeError(result.error or "SurfSense's database did not update its accounts.")


def _login(port: int, email: str, password: str) -> str:
    """SurfSense 0.0.40 returns its token in a session cookie; it still accepts it as a bearer token."""
    body = urllib.parse.urlencode({"username": email, "password": password}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/auth/jwt/login",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            auth = json.load(response)
            cookies = SimpleCookie()
            for header in response.headers.get_all("Set-Cookie") or []:
                cookies.load(header)
    except (OSError, ValueError, CookieError):
        return ""
    session = cookies.get(SESSION_COOKIE)
    return str(auth.get("access_token", "")) or (session.value if session else "")
