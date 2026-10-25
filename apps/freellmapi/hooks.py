"""Bootstrap the gateway using its supported declarative config and HTTP API.

FreeLLMAPI v0.13.3 creates config.admin before opening its HTTP listener and
only when there are no users. This avoids /auth/setup's loopback/setup-code
restriction on Docker-published ports; no container shell fallback is needed.
Source: https://github.com/TashfeenAhmed/FreeLLMAPI/blob/v0.13.3/server/src/services/declarative-config.ts
"""

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError, request
from ctl.local_models import VOICE_OUTSIDE_LIST

PROFILE_NAME = "Mu3Lab LiteLLM"
# FreeLLMAPI benches a model on every failed attempt, bad requests included, and
# lengthens the bench on repeats. With few keys one bad request can bench every
# model; a 1-minute cap (its lowest) lets them come back quickly.
COOLDOWN_CEILING_MS = 60_000
# The voice assistant's outside models, in the order tried: each answered with thinking off
# ("none", thinking budget 0) and can call tools, tested on 2026-10-25. Gemini 3 Flash Preview
# answered in about 1 s every time; the others took 1 to 36 s, as Google's free tier allowed.
# Left out: Gemini 3.5 Flash-Lite and both Gemma 4 models refuse thinking off (an error, and
# FreeLLMAPI benches the model), and Gemini Robotics-ER and Gemma cannot call tools.
VOICE_MODELS = (
    ("google", "gemini-3-flash-preview"),
    ("google", "gemini-3.1-flash-lite"),
    ("google", "gemini-3.1-flash-lite-preview"),
    ("google", "gemini-flash-latest"),
    ("google", "gemini-3.8-flash"),
    ("google", "gemini-3.7-flash"),
)


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
        token = _sign_in(ctx, "routing_settings")
        request(
            ctx.app.manifest.service.local_port,
            "/api/fallback/routing",
            method="PUT",
            data={"strategy": "smartest", "cooldownCeilingMs": COOLDOWN_CEILING_MS},
            token=token,
        )
        try:
            self._voice_list(ctx, token)
        except LoopbackError as exc:
            # Voice then uses any model, with a little thinking (ctl/local_models.py).
            ctx.log(f"The voice assistant's model list could not be saved: {exc}")

    def _voice_list(self, ctx: HookContext, token: str) -> None:
        """Keep the voice list (a FreeLLMAPI profile, "auto:mu3lab-voice") to exactly VOICE_MODELS."""
        port = ctx.app.manifest.service.local_port
        profiles = request(port, "/api/profiles", token=token)
        profile = next((p for p in profiles if str(p.get("name", "")).lower() == VOICE_OUTSIDE_LIST), None)
        if profile is None:
            # Empty: a new profile otherwise starts as a copy of the whole catalog.
            profile = request(
                port, "/api/profiles", method="POST", data={"name": VOICE_OUTSIDE_LIST, "empty": True}, token=token
            )
        catalog = {(m["platform"], m["modelId"]): m["id"] for m in request(port, "/api/models", token=token)}
        known = [catalog[model] for model in VOICE_MODELS if model in catalog]
        request(
            port,
            f"/api/profiles/{profile['id']}/reorder",
            method="PUT",
            data=[{"modelDbId": model, "priority": order, "enabled": True} for order, model in enumerate(known, 1)],
            token=token,
        )
        ctx.log(f"The voice assistant's model list has {len(known)} of {len(VOICE_MODELS)} models.")
