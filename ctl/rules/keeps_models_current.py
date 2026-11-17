"""Bring a model runner onto Mu3Lab's reviewed model list after the list changed.

A release may move a listed model to a better file of itself (a new tag) or
retire one for a successor (``ctl.local_models.RETIRED``). The settings that
route chat already name the new file, so each time the runner starts the
chosen chat model is downloaded there when only an earlier file of it is, and
earlier files are deleted. Only the runner the owner chose acts; a failed
download keeps every file and never stops the runner.
"""

from __future__ import annotations

from ctl import local_models, model_runners
from ctl.engine.hooks import HookContext
from ctl.rules import Rule, register


@register
class KeepsModelsCurrent(Rule):
    name = "keeps_models_current"
    summary = "Replaces earlier model files with the reviewed list's current ones when the runner starts."

    def after_healthy(self, ctx: HookContext) -> None:
        assert ctx.facts is not None
        paths = ctx.facts.paths
        if local_models.runner(paths, ctx.facts.catalog).id != ctx.app.id:
            return  # another runner serves the models
        try:
            local_models.bring_up_to_date(model_runners.engine(ctx.app, paths), ctx.log)
        except model_runners.ENGINE_ERRORS as exc:
            ctx.log(f"Earlier model files were left in place: {exc}")
