"""Immich creates its administrator only through its password sign-up API.

Mu3Lab signs up the owner with a generated password, marks onboarding done,
and saves the login so the owner is never asked for it. The
``password_login_off_after_setup`` rule then switches password login off,
leaving Authentik as the only way in.
"""

from __future__ import annotations

from ctl import onboarding_state
from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError, request


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        assert ctx.owner is not None and ctx.facts is not None
        port = ctx.app.manifest.service.local_port
        paths = ctx.facts.paths
        try:
            config = request(port, "/api/server/config")
            record = onboarding_state.read(ctx.app.id, paths)
            # An existing installation never gets a new administrator or password.
            if config.get("isInitialized") and (record.get("provisioned") or not record.get("password")):
                return "Existing Immich administrator kept; Open uses Authentik."
            login = onboarding_state.prepare_login(ctx.app.id, paths)
            email = ctx.owner["email"]
            if not config.get("isInitialized"):
                name = ctx.owner["display_name"] or ctx.owner["username"]
                body = {"email": email, "name": name, "password": login["password"]}
                request(port, "/api/auth/admin-sign-up", method="POST", data=body)
            auth = request(port, "/api/auth/login", method="POST", data={"email": email, "password": login["password"]})
            token = auth.get("accessToken", "")
            if not token or not auth.get("isAdmin"):
                ctx.fail(
                    "account_bootstrap", "account_provisioning_failed", "Immich did not confirm its administrator."
                )
            try:
                request(port, "/api/users/me/onboarding", method="PUT", data={"isOnboarded": True}, token=token)
                url = ctx.facts.public_url(ctx.app) + "/auth/login?autoLaunch=0"
                onboarding_state.complete_login(ctx.app.id, email, url, paths)
            finally:
                request(port, "/api/auth/logout", method="POST", token=token)
        except LoopbackError as exc:
            ctx.fail("account_bootstrap", "account_provisioning_failed", str(exc))
        return "Immich administrator created and onboarding finished; Open uses Authentik."
