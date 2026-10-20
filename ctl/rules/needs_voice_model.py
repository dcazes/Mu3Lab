"""Keep the voice assistant from starting until the models it asks for are ready, and say what to do first.

The app lists the models it asks LiteLLM for (``models``). Before it starts, the
steps are checked in the order the owner would do them, and the first one that
is missing stops the start with a sentence saying what to open:

1. When the app uses the voice name (``mu3lab-voice``), the voice setting
   (Settings, Voice Assistant) is ready: with a local model, its model runner is
   installed and answering and the model is downloaded there; with outside
   providers, at least one provider is connected and allowed.
2. The AI gateway (LiteLLM) answers and routes every listed model. Speech
   (``mu3lab-stt``, ``mu3lab-tts``) is routed once Speech is installed and running.

A check that fails here is shown on the app's card, so nothing is left to guess.
"""

from __future__ import annotations

from pydantic import Field

from ctl import local_models, model_runners
from ctl.engine.hooks import HookContext
from ctl.integrations.litellm import LiteLLM, LiteLLMError
from ctl.platform_apps import by_capability
from ctl.rules import Params, Rule, register
from ctl.rules.gateway_key import MASTER_KEY_ENV
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

VOICE_PLACE = "Settings, Voice Assistant"


class NeedsVoiceModelParams(Params):
    models: tuple[str, ...] = Field(min_length=1)


@register
class NeedsVoiceModel(Rule):
    name = "needs_voice_model"
    Params = NeedsVoiceModelParams
    summary = "Stops the voice assistant from starting until its model is ready and every model it uses is routed."
    params: NeedsVoiceModelParams

    def before_start(self, ctx: HookContext) -> None:
        assert ctx.facts is not None
        if local_models.VOICE_ALIAS in self.params.models:
            self._check_voice(ctx, ctx.facts.paths)
        self._check_routes(ctx)

    def _check_voice(self, ctx: HookContext, paths: RuntimePaths) -> None:
        assert ctx.facts is not None
        voice = local_models.voice_settings(paths)
        if voice["privacy"] == "outside_allowed":
            from ctl.core_wiring import _provider_keys

            if not any(item["enabled"] for item in _provider_keys(paths)):
                ctx.fail(
                    "voice_check",
                    "no_outside_provider",
                    "The voice assistant is set to use outside providers, but none is connected. Connect one in "
                    f"Settings, AI, or choose a local model in {VOICE_PLACE}. Then start this app again.",
                )
            return
        chosen = local_models.voice_model(paths)
        if chosen is None:
            ctx.fail(
                "voice_check",
                "voice_model_not_chosen",
                f"No local model is chosen for the voice assistant. Open {VOICE_PLACE}, choose one and save. "
                "Then start this app again.",
            )
        runner = local_models.runner(paths, ctx.facts.catalog)
        if not model_runners.installed(runner.id):
            ctx.fail(
                "voice_check",
                "runner_not_installed",
                f"{runner.manifest.name} runs the voice model and is not installed. "
                "Install it from Apps, then start this app again.",
            )
        ctx.stage("voice_check", f"Checking {chosen.name} in {runner.manifest.name}.")
        try:
            engine = model_runners.engine(runner, paths)
            present = engine.downloaded()
        except model_runners.ENGINE_ERRORS as exc:
            ctx.fail(
                "voice_check",
                "runner_not_answering",
                f"{runner.manifest.name} is installed but not answering ({exc}). "
                "Start it from Apps, then start this app again.",
            )
        if not engine.has(chosen, present):
            ctx.fail(
                "voice_check",
                "voice_model_not_downloaded",
                f"{chosen.name} is not downloaded in {runner.manifest.name} yet. Save the choice again in "
                f"{VOICE_PLACE} so it downloads, then start this app again.",
            )

    def _check_routes(self, ctx: HookContext) -> None:
        assert ctx.facts is not None
        gateway = by_capability("model_proxy", ctx.facts.catalog)
        master = read_runtime_env(ctx.facts.paths.projects / gateway.id / ".env").get(MASTER_KEY_ENV, "")
        if not master:
            ctx.fail(
                "gateway_routes",
                "gateway_not_ready",
                f"The AI gateway ({gateway.manifest.name}) is not set up yet. Install it from Apps, "
                "then start this app again.",
            )
        ctx.stage("gateway_routes", "Checking the AI gateway routes this app uses.")
        try:
            with LiteLLM(f"http://127.0.0.1:{gateway.manifest.service.local_port}", master) as client:
                routed = client.model_names()
        except LiteLLMError:
            ctx.fail(
                "gateway_routes",
                "gateway_not_answering",
                f"The AI gateway ({gateway.manifest.name}) is not answering. Start it from Apps, "
                "then start this app again.",
            )
        missing = [name for name in self.params.models if name not in routed]
        if not missing:
            return
        advice = []
        if local_models.VOICE_ALIAS in missing:
            advice.append(f"The voice model is not routed yet. Save the choice again in {VOICE_PLACE}.")
        speech = [name for name in missing if name != local_models.VOICE_ALIAS]
        if speech:
            advice.append(
                f"Not routed: {', '.join(speech)}. Install Speech from Apps and start it, then start this app again."
            )
        ctx.fail("gateway_routes", "gateway_route_missing", " ".join(advice))
