"""Checks shared by the rules that need an AI model before their app is any use.

Each check stops the app with ``ctx.needs_setup`` and a sentence saying what
the owner should open; only the AI gateway being down is a real failure.
"""

from __future__ import annotations

from ctl import local_models, model_runners
from ctl.engine.hooks import HookContext
from ctl.integrations.litellm import LiteLLM, LiteLLMError
from ctl.local_models import Model
from ctl.platform_apps import by_capability
from ctl.rules.gateway_key import MASTER_KEY_ENV
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

PROVIDERS_PLACE = "Settings, AI"


def provider_connected(paths: RuntimePaths) -> bool:
    """Whether at least one outside provider is connected and allowed to answer."""
    from ctl.core_wiring import _provider_keys

    return any(item["enabled"] for item in _provider_keys(paths))


def local_model(ctx: HookContext, chosen: Model, place: str) -> None:
    """The chosen local model's runner is installed and answering, and the model is downloaded there."""
    assert ctx.facts is not None
    runner = local_models.runner(ctx.facts.paths, ctx.facts.catalog)
    if not model_runners.installed(runner.id):
        ctx.needs_setup(
            "model_check",
            "runner_not_installed",
            f"{runner.manifest.name} runs {chosen.name} and is not installed. "
            "Install it from Apps, then start this app again.",
        )
    ctx.stage("model_check", f"Checking {chosen.name} in {runner.manifest.name}.")
    try:
        engine = model_runners.engine(runner, ctx.facts.paths)
        present = engine.downloaded()
    except model_runners.ENGINE_ERRORS as exc:
        ctx.needs_setup(
            "model_check",
            "runner_not_answering",
            f"{runner.manifest.name} is installed but not answering ({exc}). "
            "Start it from Apps, then start this app again.",
        )
    if not engine.has(chosen, present):
        ctx.needs_setup(
            "model_check",
            "model_not_downloaded",
            f"{chosen.name} is not downloaded in {runner.manifest.name} yet. Save the choice again in "
            f"{place} so it downloads, then start this app again.",
        )


def routed(ctx: HookContext) -> set[str]:
    """Every model name the AI gateway routes; the gateway being down fails the step."""
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
            return client.model_names()
    except LiteLLMError:
        ctx.fail(
            "gateway_routes",
            "gateway_not_answering",
            f"The AI gateway ({gateway.manifest.name}) is not answering. Start it from Apps, "
            "then start this app again.",
        )
