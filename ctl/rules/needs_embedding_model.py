"""Make sure the local embedding model the app relies on is downloaded and answers.

Checked before the app starts, so an install never ends with search that
cannot index anything. The model runner is the app named by ``runner``
(Ollama); the model is downloaded through it when missing. The model is the
one the owner chose for search (``ctl.local_models``). ``index_tied`` marks
an app whose search index works only with the model it was built with, so
that model cannot be changed while the app is installed.
"""

from __future__ import annotations

from typing import Literal

import httpx

from ctl import local_models
from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext
from ctl.manifest.catalog import compose_images
from ctl.rules import Params, Rule, register


class EmbeddingParams(Params):
    index_tied: bool = False
    at: Literal["before_start", "after_healthy"] = "before_start"
    runner: str = ""
    pull_command: tuple[str, ...] = ()


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
        runner = ctx.facts.catalog.get(self.params.runner)
        base = f"http://127.0.0.1:{runner.manifest.service.local_port}"
        chosen = local_models.embedding_model(ctx.facts.paths)
        model = chosen.tag
        ctx.stage("embedding_check", "Verifying the local embedding model.")
        try:
            tags = httpx.get(f"{base}/api/tags", timeout=10).json()
        except (httpx.HTTPError, ValueError) as exc:
            ctx.fail("embedding_check", "embedding_probe_failed", f"{runner.manifest.name} is not available: {exc}")
            return
        present = {
            local_models.name(str(item.get("name", ""))) for item in tags.get("models", []) if isinstance(item, dict)
        }
        if local_models.name(model) not in present:
            ctx.stage("embedding_model_pull", "Downloading the local embedding model.")
            project = ctx.facts.paths.projects / runner.id
            folder = project
            service = next(iter(compose_images(folder / "docker-compose.yml")), runner.id)
            rc, _ = Compose(folder).exec(service, [*self.params.pull_command, model], ctx.log, timeout=600)
            if rc:
                ctx.fail(
                    "embedding_check", "embedding_probe_failed", "The local embedding model could not be downloaded."
                )
        try:
            vectors = (
                httpx.post(f"{base}/api/embed", json={"model": model, "input": "Mu3Lab readiness"}, timeout=120)
                .json()
                .get("embeddings")
            )
        except (httpx.HTTPError, ValueError) as exc:
            ctx.fail("embedding_check", "embedding_probe_failed", f"The local embedding check failed: {exc}")
            return
        if not (isinstance(vectors, list) and vectors and len(vectors[0]) == chosen.dimensions):
            ctx.fail("embedding_check", "embedding_probe_failed", f"{chosen.name} returned no usable embedding.")
