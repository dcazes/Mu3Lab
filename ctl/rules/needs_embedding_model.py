"""Make sure the local embedding model the app relies on is downloaded and answers.

Checked before the app starts, so an install never ends with search that
cannot index anything. The model runner is the app named by ``runner``
(Ollama); the model is downloaded through it when missing.
"""

from __future__ import annotations

import httpx

from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext
from ctl.manifest.catalog import compose_images
from ctl.rules import Params, Rule, register


class EmbeddingParams(Params):
    model: str
    runner: str = "ollama"  # the app that serves local models


@register
class NeedsEmbeddingModel(Rule):
    name = "needs_embedding_model"
    Params = EmbeddingParams
    summary = "Downloads and checks the local embedding model before the app starts."
    params: EmbeddingParams

    def before_start(self, ctx: HookContext) -> None:
        assert ctx.facts is not None
        runner = ctx.facts.catalog.get(self.params.runner)
        base = f"http://127.0.0.1:{runner.manifest.service.local_port}"
        model = self.params.model
        ctx.stage("embedding_check", "Verifying the local embedding model.")
        try:
            tags = httpx.get(f"{base}/api/tags", timeout=10).json()
        except (httpx.HTTPError, ValueError) as exc:
            ctx.fail("embedding_check", "embedding_probe_failed", f"{runner.manifest.name} is not available: {exc}")
            return
        present = {
            str(item.get("name", "")).split(":", 1)[0] for item in tags.get("models", []) if isinstance(item, dict)
        }
        if model not in present:
            ctx.stage("embedding_model_pull", "Downloading the local embedding model.")
            project = ctx.facts.paths.projects / runner.id
            folder = project if (project / "docker-compose.yml").is_file() else runner.folder
            service = next(iter(compose_images(folder / "docker-compose.yml")), runner.id)
            rc, _ = Compose(folder).exec(service, ["ollama", "pull", model], ctx.log, timeout=600)
            if rc:
                ctx.fail(
                    "embedding_check", "embedding_probe_failed", "The local embedding model could not be downloaded."
                )
        try:
            vector = (
                httpx.post(f"{base}/api/embeddings", json={"model": model, "prompt": "Mu3Lab readiness"}, timeout=120)
                .json()
                .get("embedding")
            )
        except (httpx.HTTPError, ValueError) as exc:
            ctx.fail("embedding_check", "embedding_probe_failed", f"The local embedding check failed: {exc}")
            return
        if not isinstance(vector, list) or not vector:
            ctx.fail("embedding_check", "embedding_probe_failed", "The local model returned no embedding.")
