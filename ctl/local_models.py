"""Local AI models on this server, and where the household's AI requests may go (R31).

The owner chooses from a reviewed list, never from everything a runner offers:

* the model runner that serves them (``ctl.model_runners``): Unsloth Studio,
  which the installer sets up, or Ollama once it is installed;
* one local chat model (or none). Once downloaded it is always offered in
  chat as a private choice, ``mu3lab-local``;
* the embedding model that document search uses (``mu3lab-embed``);
* one household switch: outside providers allowed (the default), or local
  only. Local only is enforced where requests are routed: LiteLLM's settings
  then point every chat name at the local model and hold no route to the
  provider gateway at all, and the gateway's provider keys are switched off.
  With no local chat model, chat fails rather than going outside.

Changing these settings is a durable job: the new models are downloaded and
checked first, so a failed download changes nothing. Then the routing is
written, the route is checked through LiteLLM, the apps that follow the
models are updated, and models no longer used are deleted to free disk space.
A runner that is no longer chosen keeps its models, so switching back is quick.

Apps whose search index is sized for one embedding model (SurfSense) cannot
follow a change of embedding model, so it is refused while one is installed.
Every other app needs its documents indexed again; the dashboard says so.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ctl import model_runners
from ctl.runtime import RuntimePaths
from ctl.store import records

if TYPE_CHECKING:
    from ctl.manifest.catalog import App, Catalog

Privacy = Literal["outside_allowed", "local_only"]
PRIVACY: tuple[str, ...] = ("outside_allowed", "local_only")
SETTINGS = ("ai", "models")
SERVICE_ID = "ai:models"
# Chat names in LiteLLM. Chat apps use mu3lab-chat; checks use mu3lab-fast.
LOCAL_ALIAS = "mu3lab-local"


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    # The exact Ollama tag, quantization included, so a download is the same model every time.
    tag: str
    download_gb: float
    # An estimate of the memory it needs while answering, with a modest conversation.
    memory_gb: float
    note: str
    dimensions: int = 0  # embedding models only
    # The same model for Unsloth Studio: a Hugging Face GGUF repository and the file's quantization.
    gguf: str = ""
    quant: str = "Q4_K_M"


# Reviewed by the owner on 2026-10-10. Sizes are Ollama's published download sizes.
CHAT: tuple[Model, ...] = (
    Model(
        "qwen3.5-2b",
        "Qwen 3.5 2B",
        "qwen3.5:2b-q4_K_M",
        1.9,
        3,
        "Fastest. Good for short answers and summaries.",
        gguf="unsloth/Qwen3.5-2B-GGUF",
    ),
    Model(
        "phi4-mini",
        "Phi-4-mini 3.8B",
        "phi4-mini:3.8b-q4_K_M",
        2.5,
        4,
        "Small and quick; English-first.",
        gguf="unsloth/Phi-4-mini-instruct-GGUF",
    ),
    Model(
        "qwen3.5-4b",
        "Qwen 3.5 4B",
        "qwen3.5:4b-q4_K_M",
        3.3,
        5,
        "A good balance on most home servers.",
        gguf="unsloth/Qwen3.5-4B-GGUF",
    ),
    Model(
        "qwen3-8b",
        "Qwen3 8B",
        "qwen3:8b-q4_K_M",
        5.2,
        7,
        "Stronger reasoning; slower without a GPU.",
        gguf="unsloth/Qwen3-8B-GGUF",
    ),
    Model(
        "gemma4-e4b",
        "Gemma 4 E4B",
        "gemma4:e4b-it-q4_K_M",
        6.6,
        8,
        "Also understands images and audio.",
        gguf="unsloth/gemma-4-E4B-it-GGUF",
    ),
    Model(
        "qwen3.5-9b",
        "Qwen 3.5 9B",
        "qwen3.5:9b-q4_K_M",
        6.6,
        8,
        "Strongest Qwen here; best with a GPU.",
        gguf="unsloth/Qwen3.5-9B-GGUF",
    ),
    Model(
        "gemma4-12b",
        "Gemma 4 12B",
        "gemma4:12b-it-q4_K_M",
        8.0,
        10,
        "The largest choice; needs a GPU to be quick.",
        gguf="unsloth/gemma-4-12b-it-GGUF",
    ),
)
EMBEDDING: tuple[Model, ...] = (
    # Unsloth publishes no BGE-M3 GGUF; GPUStack's conversion is the widely used one.
    Model(
        "bge-m3",
        "BGE-M3",
        "bge-m3:567m",
        1.2,
        2,
        "Many languages; long documents.",
        dimensions=1024,
        gguf="gpustack/bge-m3-GGUF",
        quant="Q8_0",
    ),
    Model(
        "nomic-embed-text",
        "Nomic Embed Text",
        "nomic-embed-text:latest",
        0.3,
        1,
        "The earlier default; English-first and very light.",
        dimensions=768,
        gguf="nomic-ai/nomic-embed-text-v1.5-GGUF",
        quant="Q8_0",
    ),
)
DEFAULT_EMBEDDING = "bge-m3"
# Installs made before this setting existed keep the model their search was indexed with.
LEGACY_EMBEDDING = "nomic-embed-text"

CHAT_BY_ID = {model.id: model for model in CHAT}
EMBEDDING_BY_ID = {model.id: model for model in EMBEDDING}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _installed_embedding(paths: RuntimePaths) -> str:
    """The embedding model an existing install already routes, from its model proxy's settings."""
    from ctl.platform_apps import by_capability

    try:
        text = (paths.projects / by_capability("model_proxy").id / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_EMBEDDING
    return LEGACY_EMBEDDING if "nomic-embed-text" in text else DEFAULT_EMBEDDING


def validate(new: dict[str, Any]) -> dict[str, str]:
    """The settings as stored; raises ValueError with a sentence for the person."""
    privacy = str(new.get("privacy", ""))
    if privacy not in PRIVACY:
        raise ValueError("Choose whether outside AI providers may be used.")
    chat = str(new.get("chat_model") or "")
    if chat and chat not in CHAT_BY_ID:
        raise ValueError("Choose a local chat model from the list.")
    embedding = str(new.get("embedding_model") or "")
    if embedding not in EMBEDDING_BY_ID:
        raise ValueError("Choose an embedding model from the list.")
    if privacy == "local_only" and not chat:
        raise ValueError("Local only needs a local chat model. Choose one, or allow outside providers.")
    runner = str(new.get("runner") or "")
    if runner and model_runners.find(runner) is None:
        raise ValueError("Choose a model runner from the list.")
    return {"privacy": privacy, "chat_model": chat, "embedding_model": embedding, "runner": runner}


def settings(paths: RuntimePaths | None = None) -> dict[str, str]:
    paths = paths or RuntimePaths()
    stored = records.get(*SETTINGS, paths)
    defaults = {
        "privacy": "outside_allowed",
        "chat_model": "",
        "embedding_model": _installed_embedding(paths),
        "runner": "",
    }
    try:
        clean = validate({**defaults, **stored})
    except ValueError:
        clean = defaults
    return {
        **clean,
        "changed_at": str(stored.get("changed_at") or ""),
        "changed_by": str(stored.get("changed_by") or ""),
    }


def save(chosen: dict[str, str], actor: str, paths: RuntimePaths | None = None) -> None:
    records.put(*SETTINGS, {**validate(chosen), "changed_at": _now(), "changed_by": actor}, paths or RuntimePaths())


def local_only(paths: RuntimePaths | None = None) -> bool:
    return settings(paths)["privacy"] == "local_only"


def chat_model(paths: RuntimePaths | None = None) -> Model | None:
    return CHAT_BY_ID.get(settings(paths)["chat_model"])


def embedding_model(paths: RuntimePaths | None = None) -> Model:
    return EMBEDDING_BY_ID[settings(paths)["embedding_model"]]


def runner(paths: RuntimePaths | None = None, catalog: Catalog | None = None) -> App:
    """The model runner requests go to: the owner's choice while it is installed, else the default."""
    return model_runners.effective(settings(paths)["runner"], catalog)


def chat_list(paths: RuntimePaths | None = None) -> str:
    """LobeChat's model list, each name saying where the model runs (R31: honest labels in chat)."""
    chosen = settings(paths)
    model = CHAT_BY_ID.get(chosen["chat_model"])
    if chosen["privacy"] == "local_only" and model:
        return f"-all,+mu3lab-chat={model.name} (local only, on this server)"
    outside = "-all,+mu3lab-chat=Mu3Lab chat (outside providers)"
    if model is None:
        return outside
    return f"{outside},+{LOCAL_ALIAS}={model.name} (private, on this server)"


def litellm_models(
    api_base_env: str,
    service_key_env: str,
    runner_api_base_env: str,
    runner_api_key_env: str,
    paths: RuntimePaths,
    catalog: Catalog | None = None,
) -> list[dict]:
    """LiteLLM's chat and embedding routes for the current settings, through the chosen runner."""
    chosen = settings(paths)
    local = CHAT_BY_ID.get(chosen["chat_model"])
    engine = model_runners.engine(runner(paths, catalog), paths)

    def on_runner(name: str, model: str) -> dict:
        params = {"model": model, "api_base": f"os.environ/{runner_api_base_env}"}
        if engine.api_key():
            params["api_key"] = f"os.environ/{runner_api_key_env}"
        return {"model_name": name, "litellm_params": params}

    routes: list[dict] = []
    if chosen["privacy"] == "local_only":
        # No route to the provider gateway exists; with no local model, chat fails instead of going outside.
        if local:
            routes += [on_runner(name, engine.chat_route(local)) for name in ("mu3lab-chat", "mu3lab-fast")]
    else:
        routes += [
            {
                "model_name": name,
                "litellm_params": {
                    "model": model,
                    "api_base": f"os.environ/{api_base_env}",
                    "api_key": f"os.environ/{service_key_env}",
                },
            }
            for name, model in (("mu3lab-chat", "openai/auto:smartest"), ("mu3lab-fast", "openai/auto:fastest"))
        ]
    if local:
        routes.append(on_runner(LOCAL_ALIAS, engine.chat_route(local)))
    routes.append(on_runner("mu3lab-embed", engine.embedding_route(EMBEDDING_BY_ID[chosen["embedding_model"]])))
    return routes


# --- Apps that follow these settings ------------------------------------------

# Rules whose generated settings depend on the chosen models.
FOLLOWING_RULES = frozenset({"hide_model_providers", "needs_embedding_model"})


def index_tied(catalog, paths: RuntimePaths) -> list[str]:
    """Installed apps whose search index works with only the embedding model it was built with."""
    from ctl.engine.project import installed

    return [
        app.manifest.name
        for app in catalog.apps
        if installed(app, paths)
        and any(ref.rule == "needs_embedding_model" and ref.with_.get("index_tied") for ref in app.manifest.rules)
    ]


def _followers(catalog, paths: RuntimePaths) -> list:
    from ctl.engine.project import installed

    return [
        app
        for app in catalog.apps
        if installed(app, paths)
        and (paths.projects / app.id / "docker-compose.yml").is_file()
        and any(ref.rule in FOLLOWING_RULES for ref in app.manifest.rules)
    ]


def status(paths: RuntimePaths | None = None) -> dict[str, Any]:
    paths = paths or RuntimePaths()
    current = runner(paths)
    engine = model_runners.engine(current, paths)
    try:
        present = engine.downloaded()
        reachable = True
    except model_runners.ENGINE_ERRORS:
        present, reachable = set(), False

    def view(model: Model) -> dict[str, Any]:
        return {**asdict(model), "downloaded": engine.has(model, present)}

    return {
        "settings": settings(paths),
        "chat_models": [view(model) for model in CHAT],
        "embedding_models": [view(model) for model in EMBEDDING],
        "runner": current.id,
        "runners": [
            {
                "id": app.id,
                "name": app.manifest.name,
                "installed": model_runners.installed(app.id),
                "has_ui": app.manifest.ui.available,
            }
            for app in model_runners.runners()
        ],
        "runner_reachable": reachable,
    }


# --- The settings job -------------------------------------------------------


def _probe_litellm(log) -> None:
    from ctl.platform_apps import by_capability
    from ctl.provider_ops import _probe_stream
    from ctl.secrets import read_runtime_env

    key = read_runtime_env(RuntimePaths().projects / by_capability("model_proxy").id / ".env").get(
        "LITELLM_MASTER_KEY", ""
    )
    probe = _probe_stream(LOCAL_ALIAS, key, gateway="LiteLLM", url="http://127.0.0.1:4000/v1/chat/completions")
    log(f"LiteLLM check of {LOCAL_ALIAS}: {probe.error_code or 'complete'}")
    if not probe.success:
        raise RuntimeError(f"Chat through LiteLLM did not answer with the local model: {probe.detail}")


def _apply(paths: RuntimePaths, root: Path, log) -> None:
    """Write the routing for the stored settings and restart only what changed."""
    from ctl.provider_ops import _reconcile

    ok, detail, _ = _reconcile(root, log)
    if not ok:
        raise RuntimeError(detail)


def execute_claimed(store, job: dict, worker_id: str, root: Path) -> None:
    from ctl import resource_locks
    from ctl.engine.hooks import StepFailed
    from ctl.engine.project import Facts
    from ctl.engine.rewire import LOCK_WAIT_SECONDS, rewire_one
    from ctl.jobs import job_params, redact
    from ctl.registry import load
    from ctl.service_state import tailnet_dns_name

    job_id = str(job["id"])
    actor = str(job.get("actor") or worker_id)
    paths = RuntimePaths()

    def log(line: str) -> None:
        store.append_event(job_id, "log", line)

    def fail(detail: str, code: str, step: str) -> None:
        store.transition(job_id, "failed", actor=actor, detail=redact(detail), error_code=code, step_id=step)

    catalog = load().catalog
    before = settings(paths)
    try:
        chosen = validate(job_params(job))
    except ValueError as exc:
        fail(str(exc), "invalid_settings", "validate")
        return
    if chosen["embedding_model"] != before["embedding_model"] and index_tied(catalog, paths):
        names = ", ".join(index_tied(catalog, paths))
        fail(f"{names} keeps a search index for one embedding model. Remove it first.", "index_tied", "validate")
        return
    chat, embed = CHAT_BY_ID.get(chosen["chat_model"]), EMBEDDING_BY_ID[chosen["embedding_model"]]
    target = model_runners.find(chosen["runner"], catalog) if chosen["runner"] else runner(paths, catalog)
    if target is None or not model_runners.installed(target.id):
        fail("Install that model runner from Apps first.", "runner_missing", "validate")
        return
    chosen["runner"] = target.id
    engine = model_runners.engine(target, paths)
    # A runner that serves search from its own settings (Unsloth Studio) restarts with a new search model first.
    later = engine.embedding_follows_restart and embed.id != before["embedding_model"]
    # 1. Download and check the models first: until this succeeds nothing has changed.
    try:
        present = engine.downloaded()
        for model, check in ((chat, engine.check_chat), (embed, engine.check_embedding)):
            if model is None:
                continue
            if not engine.has(model, present):
                log(f"Downloading {model.name} into {target.manifest.name} ({model.download_gb} GB).")
                engine.pull(model, log)
            if model is embed and later:
                continue
            log(f"Checking {model.name} in {target.manifest.name}.")
            check(model)
    except model_runners.ENGINE_ERRORS as exc:
        fail(
            f"Nothing was changed: the model could not be downloaded or did not answer ({exc}).",
            "model_failed",
            "download",
        )
        return
    # 2. Route requests by the new settings, and check chat's own path.
    save(chosen, actor, paths)
    try:
        _apply(paths, root, log)
        if chat:
            _probe_litellm(log)
    except (OSError, ValueError, RuntimeError) as exc:
        save(before, before.get("changed_by") or actor, paths)
        try:
            _apply(paths, root, log)
        except (OSError, ValueError, RuntimeError) as undo:
            log(f"Putting the earlier settings back also failed: {redact(str(undo))}")
        fail(f"The earlier settings were kept: {exc}", "routing_failed", "route")
        return
    # 3. Apps whose settings follow the models: the chat app's list, the runners' search model.
    facts = Facts(tailnet_dns_name(), paths, catalog)

    def follow() -> None:
        for app in _followers(catalog, paths):
            try:
                with resource_locks.hold(f"app:{app.id}", timeout=LOCK_WAIT_SECONDS, paths=paths):
                    if rewire_one(app, facts, log):
                        log(f"{app.manifest.name} now uses the new settings.")
            except resource_locks.Busy:
                log(f"{app.manifest.name} is busy; it picks up the new settings the next time it starts.")
            except (StepFailed, OSError, ValueError, RuntimeError) as exc:
                log(f"{app.manifest.name} could not be updated: {redact(str(exc))}")

    follow()
    if later:
        try:
            log(f"Checking {embed.name} in {target.manifest.name}.")
            engine.check_embedding(embed)
        except model_runners.ENGINE_ERRORS as exc:
            save(before, before.get("changed_by") or actor, paths)
            try:
                _apply(paths, root, log)
                follow()
            except (OSError, ValueError, RuntimeError) as undo:
                log(f"Putting the earlier settings back also failed: {redact(str(undo))}")
            fail(f"The earlier settings were kept: {embed.name} did not answer ({exc}).", "model_failed", "check")
            return
    # 4. Free the disk space of models nothing uses now, in the runner that serves them.
    for model in (*CHAT, *EMBEDDING):
        if model in (chat, embed) or not engine.has(model, present):
            continue
        try:
            engine.delete(model)
            log(f"Deleted {model.name}, which is no longer used.")
        except model_runners.ENGINE_ERRORS as exc:
            log(f"{model.name} could not be deleted: {exc}")
    where = "only on this server" if chosen["privacy"] == "local_only" else "on this server or your outside providers"
    detail = (
        f"AI now runs {where}, in {target.manifest.name}. "
        f"Chat model: {chat.name if chat else 'none'}. Search: {embed.name}."
    )
    store.record_audit(actor=actor, event="ai.models_changed", detail=detail, job_id=job_id)
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
