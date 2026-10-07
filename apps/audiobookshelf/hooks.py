"""Audiobookshelf is set up only through its API, never from settings.

On a new server Mu3Lab creates the root account under the owner's Authentik
username (with a generated password nobody uses), creates the Audiobooks
and Podcasts libraries, and switches sign-in to Authentik alone. The
owner's first OpenID sign-in then links to that root account by username;
everyone else is registered on first sign-in with the role Authentik's
``abs_roles`` claim names.
"""

from __future__ import annotations

from typing import Any

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError, request
from ctl.store import onboarding as onboarding_state

BASE_PATH = "/audiobookshelf"  # the image's default router base path
LIBRARIES = (
    {"name": "Audiobooks", "mediaType": "book", "icon": "audiobookshelf", "folders": [{"fullPath": "/audiobooks"}]},
    {"name": "Podcasts", "mediaType": "podcast", "icon": "podcast", "folders": [{"fullPath": "/podcasts"}]},
)


def openid_settings(host: str, app_id: str, client_id: str, client_secret: str) -> dict[str, Any]:
    """Authentik's endpoints for this app, plus Mu3Lab's sign-in policy."""
    issuer = f"https://{host}/application/o/mu3lab-{app_id}/"
    return {
        "authOpenIDIssuerURL": issuer,
        "authOpenIDAuthorizationURL": f"https://{host}/application/o/authorize/",
        "authOpenIDTokenURL": f"https://{host}/application/o/token/",
        "authOpenIDUserInfoURL": f"https://{host}/application/o/userinfo/",
        "authOpenIDJwksURL": issuer + "jwks/",
        "authOpenIDLogoutURL": issuer + "end-session/",
        "authOpenIDClientID": client_id,
        "authOpenIDClientSecret": client_secret,
        "authOpenIDTokenSigningAlgorithm": "RS256",
        "authOpenIDButtonText": "Sign in with Mu3Lab",
        "authOpenIDAutoLaunch": True,
        "authOpenIDAutoRegister": True,
        # The root account carries the owner's Authentik username.
        "authOpenIDMatchExistingBy": "username",
        # The phone apps finish sign-in here; the server itself redirects to them.
        "authOpenIDMobileRedirectURIs": ["audiobookshelf://oauth"],
        "authOpenIDGroupClaim": "abs_roles",
        # The web app lives under /audiobookshelf; sign-in returns there.
        "authOpenIDSubfolderForRedirectURLs": BASE_PATH,
        "authActiveAuthMethods": ["openid"],
    }


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        assert ctx.owner is not None and ctx.facts is not None
        port = ctx.app.manifest.service.local_port
        paths = ctx.facts.paths
        oidc = ctx.app.manifest.sign_in.oidc
        assert oidc is not None
        env = ctx.env()
        try:
            status = request(port, "/status")
            if status.get("isInit") and status.get("authMethods") == ["openid"]:
                return "Existing Audiobookshelf kept; Open uses Authentik."
            login = onboarding_state.prepare_login(ctx.app.id, paths)
            username = ctx.owner["username"]
            if not status.get("isInit"):
                root = {"newRoot": {"username": username, "password": login["password"]}}
                request(port, "/init", method="POST", data=root, json_reply=False)
            # A retry after an interrupted install resumes with the password saved before /init.
            try:
                auth = request(
                    port, "/login", method="POST", data={"username": username, "password": login["password"]}
                )
            except LoopbackError:
                ctx.fail(
                    "account_bootstrap",
                    "account_provisioning_failed",
                    "This Audiobookshelf data has an owner Mu3Lab did not create; it can only set up a new server.",
                )
                raise
            user = auth.get("user") or {}
            token = user.get("accessToken") or user.get("token") or ""
            if not token or user.get("type") != "root":
                ctx.fail(
                    "account_bootstrap", "account_provisioning_failed", "Audiobookshelf did not confirm its owner."
                )
            listed = request(port, "/api/libraries", token=token) or {}
            existing = {item.get("name") for item in listed.get("libraries", [])}
            for library in LIBRARIES:
                if library["name"] not in existing:
                    request(port, "/api/libraries", method="POST", data=library, token=token)
            settings = openid_settings(
                ctx.facts.dns_name, ctx.app.id, env.get(oidc.env.client_id, ""), env.get(oidc.env.client_secret, "")
            )
            request(port, "/api/auth-settings", method="PATCH", data=settings, token=token)
            onboarding_state.complete_login(ctx.app.id, username, ctx.facts.public_url(ctx.app), paths)
            if (request(port, "/status") or {}).get("authMethods") != ["openid"]:
                ctx.fail(
                    "account_bootstrap", "account_provisioning_failed", "Audiobookshelf did not switch to Authentik."
                )
        except LoopbackError as exc:
            ctx.fail("account_bootstrap", "account_provisioning_failed", str(exc))
        return "Audiobookshelf owner and libraries created; sign-in now uses Authentik only."
