"""Bootstrap the gateway using its supported declarative config and HTTP API.

FreeLLMAPI v0.13.3 creates config.admin before opening its HTTP listener and
only when there are no users. This avoids /auth/setup's loopback/setup-code
restriction on Docker-published ports; no container shell fallback is needed.
Source: https://github.com/TashfeenAhmed/FreeLLMAPI/blob/v0.13.3/server/src/services/declarative-config.ts
"""

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import request

PROFILE_NAME = "Mu3Lab LiteLLM"
# FreeLLMAPI benches a model on every failed attempt, bad requests included, and
# lengthens the bench on repeats. With few keys one bad request can bench every
# model; a 1-minute cap (its lowest) lets them come back quickly.
COOLDOWN_CEILING_MS = 60_000


def _sign_in(ctx: HookContext, stage: str) -> str:
    values = ctx.env()
    session = request(
        ctx.app.manifest.service.local_port,
        "/api/auth/login",
        method="POST",
        data={
            "email": values.get("FREELLMAPI_ADMIN_EMAIL", ""),
            "password": values.get("FREELLMAPI_ADMIN_PASSWORD", ""),
        },
    )
    token = session.get("token", "") if isinstance(session, dict) else ""
    if not token:
        ctx.fail(stage, "account_provisioning_failed", "The provider gateway did not accept its administrator login.")
    return token


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        values = ctx.env()
        if values.get("FREELLMAPI_SERVICE_KEY", "").startswith("sk-cp-"):
            ctx.set_env({"MU3LAB_ADMIN_BOOTSTRAPPED": "true"})
            return "The internal provider gateway credential is already saved."
        port = ctx.app.manifest.service.local_port
        token = _sign_in(ctx, "account_bootstrap")
        profiles = request(port, "/api/client-profiles", token=token)
        if not isinstance(profiles, list):
            ctx.fail(
                "account_bootstrap",
                "account_provisioning_failed",
                "The provider gateway's client-profile API did not answer.",
            )
        # Keys are revealed once. Reuse the saved key on retries, but never mint
        # duplicate profiles if a previous interrupted run lost that key.
        if any(item.get("name") == PROFILE_NAME for item in profiles if isinstance(item, dict)):
            ctx.fail(
                "account_bootstrap",
                "client_credential_incomplete",
                "The provider gateway has a client profile whose key was not saved. Remove that incomplete profile in its dashboard, then retry core setup.",
            )
        response = request(port, "/api/client-profiles", method="POST", data={"name": PROFILE_NAME}, token=token)
        key = response.get("key", "") if isinstance(response, dict) else ""
        if not isinstance(key, str) or not key.startswith("sk-cp-"):
            ctx.fail(
                "account_bootstrap",
                "account_provisioning_failed",
                "The provider gateway did not return an internal client credential.",
            )
        ctx.set_env({"FREELLMAPI_SERVICE_KEY": key, "MU3LAB_ADMIN_BOOTSTRAPPED": "true"})
        return "The internal provider gateway credential was created and saved."

    def after_healthy(self, ctx: HookContext) -> None:
        # The declarative config has no cooldown field, so set it through the
        # routing API. "smartest" repeats the strategy the config already sets.
        request(
            ctx.app.manifest.service.local_port,
            "/api/fallback/routing",
            method="PUT",
            data={"strategy": "smartest", "cooldownCeilingMs": COOLDOWN_CEILING_MS},
            token=_sign_in(ctx, "routing_settings"),
        )
