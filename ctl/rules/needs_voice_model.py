"""Keep the voice assistant stopped until the models it asks for are ready, and say what to do first.

The app lists the models it asks LiteLLM for (``models``). The steps are
checked in the order the owner would do them, and the first one that is
missing leaves the app installed but stopped ("Needs setup") with a sentence
saying what to open:

1. When the app uses the voice name (``mu3lab-voice``), the voice setting
   (Settings, Voice Assistant) is ready: with a local model, its model runner is
   installed and answering and the model is downloaded there; with outside
   providers, at least one provider is connected and allowed.
2. The AI gateway (LiteLLM) routes every listed model. Speech
   (``mu3lab-stt``, ``mu3lab-tts``) is routed once Speaches is installed and running.

Only the gateway itself being down fails the install. The check runs again
when the owner presses Start.
"""

from __future__ import annotations

from pydantic import Field

from ctl import duplex_voice, local_models
from ctl.engine.hooks import HookContext
from ctl.rules import Params, Rule, model_ready, register

VOICE_PLACE = "Settings, Voice Assistant"


class NeedsVoiceModelParams(Params):
    models: tuple[str, ...] = Field(min_length=1)


@register
class NeedsVoiceModel(Rule):
    name = "needs_voice_model"
    Params = NeedsVoiceModelParams
    summary = "Keeps the voice assistant stopped until its model is ready and every model it uses is routed."
    params: NeedsVoiceModelParams

    def setup_check(self, ctx: HookContext) -> None:
        assert ctx.facts is not None
        # The full-duplex model hears and speaks itself; none of these models is used meanwhile.
        if duplex_voice.duplex_on(ctx.facts.paths):
            return
        if local_models.VOICE_ALIAS in self.params.models:
            self._check_voice(ctx)
        self._check_routes(ctx)

    def _check_voice(self, ctx: HookContext) -> None:
        assert ctx.facts is not None
        paths = ctx.facts.paths
        if local_models.voice_settings(paths)["privacy"] == "outside_allowed":
            if not model_ready.provider_connected(paths):
                ctx.needs_setup(
                    "voice_check",
                    "no_outside_provider",
                    "The voice assistant is set to use outside providers, but none is connected. Connect one in "
                    f"{model_ready.PROVIDERS_PLACE}, or choose a local model in {VOICE_PLACE}. "
                    "Then start this app again.",
                )
            return
        chosen = local_models.voice_model(paths)
        if chosen is None:
            ctx.needs_setup(
                "voice_check",
                "voice_model_not_chosen",
                f"No local model is chosen for the voice assistant. Open {VOICE_PLACE}, choose one and save. "
                "Then start this app again.",
            )
        model_ready.local_model(ctx, chosen, VOICE_PLACE)

    def _check_routes(self, ctx: HookContext) -> None:
        routed = model_ready.routed(ctx)
        missing = [name for name in self.params.models if name not in routed]
        if not missing:
            return
        advice = []
        if local_models.VOICE_ALIAS in missing:
            advice.append(f"The voice model is not routed yet. Save the choice again in {VOICE_PLACE}.")
        speech = [name for name in missing if name != local_models.VOICE_ALIAS]
        if speech:
            advice.append(
                f"Not routed: {', '.join(speech)}. Install Speaches from Apps and start it, then start this app again."
            )
        ctx.needs_setup("gateway_routes", "gateway_route_missing", " ".join(advice))
