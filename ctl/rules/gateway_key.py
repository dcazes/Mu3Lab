"""Give an app its own key to Mu3Lab's AI gateway (LiteLLM), limited to the models it uses.

LiteLLM's master key can do anything, including creating and deleting keys
and calling every model, so apps never hold it. An app that follows this rule
gets a key of its own instead (chat apps, for example, cannot reach the voice
assistant's route, which may go outside while chat is local only): the value is one of the app's generated secrets
(``key_env``, which must start with ``sk-`` as LiteLLM requires), and LiteLLM
accepts it only for ``models`` and at most ``rpm_limit`` requests a minute.

The key is registered before the app starts, and again whenever the
gateway is reinstalled (``rewire``), since its database then starts empty.
Registering updates the app's key in place, or replaces an earlier key of the same
app, so it is safe to repeat.
"""

from __future__ import annotations

from pydantic import Field

from ctl.engine.hooks import HookContext
from ctl.integrations.litellm import LiteLLM, LiteLLMError
from ctl.platform_apps import by_capability
from ctl.rules import Params, Rule, register
from ctl.secrets import read_runtime_env

MASTER_KEY_ENV = "LITELLM_MASTER_KEY"


class GatewayKeyParams(Params):
    key_env: str
    models: tuple[str, ...] = Field(min_length=1)
    rpm_limit: int = Field(default=120, ge=1, le=10_000)


def key_alias(app_id: str) -> str:
    return f"mu3lab-app-{app_id}"


@register
class GatewayKey(Rule):
    name = "gateway_key"
    Params = GatewayKeyParams
    summary = "Registers the app's own AI gateway key, limited to the models it uses."
    params: GatewayKeyParams

    def before_start(self, ctx: HookContext) -> None:
        # Before the app's first start, so it never starts with a key LiteLLM refuses.
        self._register(ctx)

    def rewire(self, ctx: HookContext) -> None:
        self._register(ctx)

    def _register(self, ctx: HookContext) -> None:
        assert ctx.facts is not None
        key = ctx.env().get(self.params.key_env, "")
        if not key.startswith("sk-"):
            ctx.fail("gateway_key", "gateway_key_missing", "The app's AI gateway key was not generated.")
            return
        gateway = by_capability("model_proxy", ctx.facts.catalog)
        master = read_runtime_env(ctx.facts.paths.projects / gateway.id / ".env").get(MASTER_KEY_ENV, "")
        if not master:
            ctx.fail("gateway_key", "gateway_not_ready", "The AI gateway is not set up yet.")
            return
        ctx.stage("gateway_key", "Registering this app's key with the AI gateway.")
        alias = key_alias(ctx.app.id)
        try:
            with LiteLLM(f"http://127.0.0.1:{gateway.manifest.service.local_port}", master) as client:
                models = list(self.params.models)
                if client.keys(alias):
                    try:
                        # The same key value: update it in place, so the app never goes without one.
                        client.update(key=key, models=models, rpm_limit=self.params.rpm_limit)
                        return
                    except LiteLLMError:
                        # A different value under this alias (the secret changed): replace it.
                        client.delete([alias])
                client.generate(user_id=alias, alias=alias, models=models, rpm_limit=self.params.rpm_limit, key=key)
        except LiteLLMError as exc:
            ctx.fail("gateway_key", "gateway_key_failed", f"The AI gateway did not accept this app's key: {exc}")
