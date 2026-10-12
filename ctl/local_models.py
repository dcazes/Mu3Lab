"""Local AI models on this server, and where the household's AI requests may go (R31).

The owner chooses from a reviewed list, never from everything Ollama offers:

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
written, the route is checked through LiteLLM, the chat app's model list
follows, and models no longer used are deleted to free disk space.

Apps whose search index is sized for one embedding model (SurfSense) cannot
follow a change of embedding model, so it is refused while one is installed.
Every other app needs its documents indexed again; the dashboard says so.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ctl.runtime import RuntimePaths
from ctl.store import records

Privacy = Literal["outside_allowed", "local_only"]
PRIVACY: tuple[str, ...] = ("outside_allowed", "local_only")
SETTINGS = ("ai", "models")
SERVICE_ID = "ai:models"
# Chat names in LiteLLM. Chat apps use mu3lab-chat; checks use mu3lab-fast.
LOCAL_ALIAS = "mu3lab-local"
OLLAMA = "http://127.0.0.1:11434"


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


# Reviewed by the owner on 2026-10-10. Sizes are Ollama's published download sizes.
CHAT: tuple[Model, ...] = (
    Model("qwen3.5-2b", "Qwen 3.5 2B", "qwen3.5:2b-q4_K_M", 1.9, 3, "Fastest. Good for short answers and summaries."),
    Model("phi4-mini", "Phi-4-mini 3.8B", "phi4-mini:3.8b-q4_K_M", 2.5, 4, "Small and quick; English-first."),
    Model("qwen3.5-4b", "Qwen 3.5 4B", "qwen3.5:4b-q4_K_M", 3.3, 5, "A good balance on most home servers."),
    Model("qwen3-8b", "Qwen3 8B", "qwen3:8b-q4_K_M", 5.2, 7, "Stronger reasoning; slower without a GPU."),
    Model("gemma4-e4b", "Gemma 4 E4B", "gemma4:e4b-it-q4_K_M", 6.6, 8, "Also understands images and audio."),
    Model("qwen3.5-9b", "Qwen 3.5 9B", "qwen3.5:9b-q4_K_M", 6.6, 8, "Strongest Qwen here; best with a GPU."),
    Model("gemma4-12b", "Gemma 4 12B", "gemma4:12b-it-q4_K_M", 8.0, 10, "The largest choice; needs a GPU to be quick."),
)
EMBEDDING: tuple[Model, ...] = (
    Model("bge-m3", "BGE-M3", "bge-m3:567m", 1.2, 2, "Many languages; long documents.", dimensions=1024),
    Model(
        "nomic-embed-text",
        "Nomic Embed Text",
        "nomic-embed-text:latest",
        0.3,
        1,
        "The earlier default; English-first and very light.",
        dimensions=768,
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
    return {"privacy": privacy, "chat_model": chat, "embedding_model": embedding}


def settings(paths: RuntimePaths | None = None) -> dict[str, str]:
    paths = paths or RuntimePaths()
    stored = records.get(*SETTINGS, paths)
    defaults = {"privacy": "outside_allowed", "chat_model": "", "embedding_model": _installed_embedding(paths)}
    try:
        clean = validate({**defaults, **stored})
    except ValueError:
        # An existing invalid record cannot authorize external inference.
        clean = {
            "privacy": "local_only",
            "chat_model": str(stored.get("chat_model") or "") if stored.get("chat_model") in CHAT_BY_ID else "",
            "embedding_model": (
                str(stored["embedding_model"])
                if stored.get("embedding_model") in EMBEDDING_BY_ID
                else defaults["embedding_model"]
            ),
        }
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


def chat_list(paths: RuntimePaths | None = None) -> str:
    """LobeChat's model list, each name saying where the model runs (R31: honest labels in chat)."""
    chosen = settings(paths)
    model = CHAT_BY_ID.get(chosen["chat_model"])
    if chosen["privacy"] == "local_only":
        return f"-all,+mu3lab-chat={model.name} (local only, on this server)" if model else "-all"
    outside = "-all,+mu3lab-chat=Mu3Lab chat (outside providers)"
    if model is None:
        return outside
    return f"{outside},+{LOCAL_ALIAS}={model.name} (private, on this server)"


def litellm_models(
    api_base_env: str, service_key_env: str, runner_api_base_env: str, embedding_provider: str, paths: RuntimePaths
) -> list[dict]:
    """LiteLLM's chat and embedding routes for the current settings."""
    chosen = settings(paths)
    local = CHAT_BY_ID.get(chosen["chat_model"])

    def on_runner(name: str, model: str) -> dict:
        return {"model_name": name, "litellm_params": {"model": model, "api_base": f"os.environ/{runner_api_base_env}"}}

    routes: list[dict] = []
    if chosen["privacy"] == "local_only":
        # No route to the provider gateway exists; with no local model, chat fails instead of going outside.
        if local:
            routes += [on_runner(name, f"ollama_chat/{local.tag}") for name in ("mu3lab-chat", "mu3lab-fast")]
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
        routes.append(on_runner(LOCAL_ALIAS, f"ollama_chat/{local.tag}"))
    routes.append(on_runner("mu3lab-embed", f"{embedding_provider}/{EMBEDDING_BY_ID[chosen['embedding_model']].tag}"))
    return routes


# --- Ollama -----------------------------------------------------------------


def _ollama(path: str, payload: dict | None = None, *, method: str = "POST", timeout: float = 300) -> Any:
    request = urllib.request.Request(
        f"{OLLAMA}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body) if body.strip() else {}


def name(tag: str) -> str:
    return tag if ":" in tag else f"{tag}:latest"


def downloaded() -> set[str]:
    tags = _ollama("/api/tags", method="GET", timeout=15)
    return {name(str(item.get("name", ""))) for item in tags.get("models", []) if isinstance(item, dict)}


def pull(tag: str, log) -> None:
    """Download ``tag`` through Ollama, logging progress about every tenth of the way."""
    request = urllib.request.Request(
        f"{OLLAMA}/api/pull",
        method="POST",
        data=json.dumps({"model": tag, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    reported = -1
    with urllib.request.urlopen(request, timeout=3600) as response:
        for line in response:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("error"):
                raise RuntimeError(str(event["error"]))
            total, done = event.get("total") or 0, event.get("completed") or 0
            if total:
                tenth = int(done * 10 / total)
                if tenth != reported:
                    reported = tenth
                    log(f"Downloading {tag}: {tenth * 10}% of {total / 1e9:.1f} GB")
            if event.get("status") == "success":
                return
    raise RuntimeError(f"Ollama stopped before {tag} finished downloading.")


def delete(tag: str) -> None:
    _ollama("/api/delete", {"model": tag}, method="DELETE", timeout=60)


def check_chat(model: Model) -> None:
    """Ask the model for a short answer directly in Ollama (the first one loads it into memory)."""
    answer = _ollama(
        "/api/chat",
        {
            "model": model.tag,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "stream": False,
            "think": False,
            "options": {"num_predict": 16},
        },
        timeout=600,
    )
    if not isinstance(answer.get("message"), dict):
        raise RuntimeError(f"{model.name} gave no answer.")


def check_embedding(model: Model) -> None:
    answer = _ollama("/api/embed", {"model": model.tag, "input": "Mu3Lab readiness"}, timeout=600)
    vectors = answer.get("embeddings")
    if not (isinstance(vectors, list) and vectors and len(vectors[0]) == model.dimensions):
        raise RuntimeError(f"{model.name} did not return {model.dimensions}-number vectors.")


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
    try:
        present = downloaded()
        reachable = True
    except (OSError, ValueError):
        present, reachable = set(), False

    def view(model: Model) -> dict[str, Any]:
        return {**asdict(model), "downloaded": name(model.tag) in present}

    return {
        "settings": settings(paths),
        "chat_models": [view(model) for model in CHAT],
        "embedding_models": [view(model) for model in EMBEDDING],
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
    # 1. Download and check the models first: until this succeeds nothing has changed.
    try:
        present = downloaded()
        for model, check in ((chat, check_chat), (embed, check_embedding)):
            if model is None:
                continue
            if name(model.tag) not in present:
                log(f"Downloading {model.name} ({model.download_gb} GB).")
                pull(model.tag, log)
            log(f"Checking {model.name} on this server.")
            check(model)
    except (OSError, ValueError, RuntimeError) as exc:
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
    # 3. Apps whose settings follow the models: the chat app's list, search apps' embedding model.
    facts = Facts(tailnet_dns_name(), paths, catalog)
    incomplete = []
    for app in _followers(catalog, paths):
        try:
            with resource_locks.hold(f"app:{app.id}", timeout=LOCK_WAIT_SECONDS, paths=paths):
                if rewire_one(app, facts, log):
                    log(f"{app.manifest.name} now uses the new settings.")
        except resource_locks.Busy:
            incomplete.append(app.manifest.name)
            log(f"{app.manifest.name} is busy; it picks up the new settings the next time it starts.")
        except (StepFailed, OSError, ValueError, RuntimeError) as exc:
            incomplete.append(app.manifest.name)
            log(f"{app.manifest.name} could not be updated: {redact(str(exc))}")
    if incomplete:
        fail(
            "AI routing changed, but these apps still need updating: "
            + ", ".join(incomplete)
            + ". All previously downloaded models were retained. Retry when the apps are ready.",
            "followers_incomplete",
            "followers",
        )
        return
    # 4. Free the disk space of models nothing uses now.
    keep = {name(model.tag) for model in (chat, embed) if model}
    for model in (*CHAT, *EMBEDDING):
        if name(model.tag) in present and name(model.tag) not in keep:
            try:
                delete(model.tag)
                log(f"Deleted {model.name}, which is no longer used.")
            except (OSError, ValueError) as exc:
                log(f"{model.name} could not be deleted: {exc}")
    where = "only on this server" if chosen["privacy"] == "local_only" else "on this server or your outside providers"
    detail = f"AI now runs {where}. Chat model: {chat.name if chat else 'none'}. Search: {embed.name}."
    store.record_audit(actor=actor, event="ai.models_changed", detail=detail, job_id=job_id)
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
