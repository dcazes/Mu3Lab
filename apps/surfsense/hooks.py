"""SurfSense has no single sign-on, so Mu3Lab registers the owner's account itself.

The generated password goes to the owner's vault (``save_login_to_vault``) for
Bitwarden to fill; Authentik still guards the address.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from http.cookies import CookieError, SimpleCookie

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError, request
from ctl.store import onboarding as onboarding_state

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
