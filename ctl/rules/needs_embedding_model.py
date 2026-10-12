"""Make sure the local embedding model the app relies on is downloaded and answers.

Checked before the app starts, so an install never ends with search that
cannot index anything. The model runs in the model runner the owner chose
(``ctl.model_runners``); it is downloaded through it when missing. The model
is the one the owner chose for search (``ctl.local_models``). On a runner
itself (``at: after_healthy``) the check runs only while it is the chosen one.
``index_tied`` marks an app whose search index works only with the model it
was built with, so that model cannot be changed while the app is installed.
"""

from __future__ import annotations

from typing import Literal

from ctl import local_models, model_runners
from ctl.engine.hooks import HookContext
from ctl.rules import Params, Rule, register


class EmbeddingParams(Params):
    index_tied: bool = False
    at: Literal["before_start", "after_healthy"] = "before_start"


@register
class NeedsEmbeddingModel(Rule):
    name = "needs_embedding_model"
    Params = EmbeddingParams
    summary = "Downloads and checks the local embedding model before the app starts."
    params: EmbeddingParams

    def before_start(self, ctx: HookContext) -> None:
        if self.params.at == "before_start":
            self._check(ctx)

    def after_healthy(self, ctx: HookContext) -> None:
        if self.params.at == "after_healthy":
            self._check(ctx)

    def _check(self, ctx: HookContext) -> None:
        assert ctx.facts is not None
        paths = ctx.facts.paths
        runner = local_models.runner(paths, ctx.facts.catalog)
        if model_runners.CAPABILITY in ctx.app.manifest.capabilities and runner.id != ctx.app.id:
            return  # another runner serves search; this one waits until the owner chooses it
        chosen = local_models.embedding_model(paths)
        ctx.stage("embedding_check", "Verifying the local embedding model.")
        try:
            engine = model_runners.engine(runner, paths)
            if not engine.has(chosen, engine.downloaded()):
                ctx.stage("embedding_model_pull", "Downloading the local embedding model.")
                engine.pull(chosen, ctx.log)
            engine.check_embedding(chosen)
        except model_runners.ENGINE_ERRORS as exc:
            ctx.fail(
                "embedding_check",
                "embedding_probe_failed",
                f"The local embedding model is not ready in {runner.manifest.name}: {exc}",
            )
