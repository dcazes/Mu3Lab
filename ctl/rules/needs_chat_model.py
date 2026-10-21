"""Keep a chat app stopped until some chat model can answer, and say what to do first.

Chat answers through outside providers when they are allowed and one is
connected, or through the local chat model chosen in Settings, AI. With
neither, the app installs but stays stopped ("Needs setup") with a sentence
saying what to open; the check runs again when the owner presses Start.
"""

from __future__ import annotations

from ctl import local_models
from ctl.engine.hooks import HookContext
from ctl.rules import Rule, model_ready, register

CHAT_PLACE = "Settings, AI"


@register
class NeedsChatModel(Rule):
    name = "needs_chat_model"
    summary = "Keeps a chat app stopped until an outside provider or a local chat model can answer."

    def setup_check(self, ctx: HookContext) -> None:
        assert ctx.facts is not None
        paths = ctx.facts.paths
        outside = not local_models.local_only(paths)
        if outside and model_ready.provider_connected(paths):
            return
        chosen = local_models.chat_model(paths)
        if chosen is None:
            name = ctx.app.manifest.name
            ctx.needs_setup(
                "chat_check",
                "no_outside_provider" if outside else "chat_model_not_chosen",
                f"{name} is set to use outside providers, but none is connected. Connect one in {CHAT_PLACE}, "
                "or choose a local chat model there. Then start this app again."
                if outside
                else f"No local chat model is chosen for {name}. Open {CHAT_PLACE}, choose one and save. "
                "Then start this app again.",
            )
        model_ready.local_model(ctx, chosen, CHAT_PLACE)
