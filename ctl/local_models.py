"""Local AI models on this server, and where the household's AI requests may go (R31).

The owner chooses from a reviewed list, never from everything a runner offers:

* the model runner that serves them (``ctl.model_runners``): Ollama, which
  the installer sets up;
* one local chat model (or none). Once downloaded it is always offered in
  chat as a private choice, ``mu3lab-local``;
* the embedding model that document search uses (``mu3lab-embed``);
* where chat runs: outside providers first (the default before a local model is
  chosen), local first (the installer's choice when a model fits), or local
  only. Local only is enforced where requests are routed: LiteLLM's settings
  then point every chat name at the local model and hold no chat route to the
  provider gateway at all. With no local chat model, chat fails rather than
  going outside;
* the voice assistant (``mu3lab-voice``) always follows chat: the same sources
  and the same local model, so only one chat model is ever loaded. The gateway's
  provider keys are switched off only when chat is local only.

Changing these settings is a durable job: the new models are downloaded and
checked first, so a failed download changes nothing. Then the routing is
written, the route is checked through LiteLLM, and the apps that follow the
models are updated. Models no longer used stay downloaded, so switching back is
instant; the owner removes one in Settings to free its disk space (``remove``).
A runner that is no longer chosen keeps its models, so switching back is quick.

Apps whose search index is sized for one embedding model (rule
``needs_embedding_model`` with ``index_tied``) cannot follow a change of
embedding model, so it is refused while one is installed.
Every other app needs its documents indexed again; the dashboard says so.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from ctl import model_runners
from ctl.runtime import RuntimePaths
from ctl.store import records

# The checkout the apps' Compose projects come from, for waking a stopped runner.
ROOT = Path(__file__).resolve().parents[1]
STOPPED_NOTE = "local models answer only while it runs. Start it from Home."

if TYPE_CHECKING:
    from ctl.manifest.catalog import App, Catalog

# Where chat may run, in the owner's order: the first answers by default, the second is a choice in
# chat beside it (LOCAL_ALIAS or OUTSIDE_ALIAS). It never answers on its own when the first fails,
# so a private chat is never sent outside without the person choosing to.
Source = Literal["local", "outside"]
SOURCES: tuple[str, ...] = ("local", "outside")
# The same choice in three words, for voice and for callers that only ask whether outside is allowed:
# outside_allowed (outside first, or alone), local_first (local first, outside second), local_only.
Privacy = Literal["outside_allowed", "local_first", "local_only"]
PRIVACY: tuple[str, ...] = ("outside_allowed", "local_first", "local_only")
SETTINGS = ("ai", "models")
SERVICE_ID = "ai:models"
# Chat names in LiteLLM. Chat apps use mu3lab-chat; checks use mu3lab-fast.
LOCAL_ALIAS = "mu3lab-local"
# Outside providers' chat name when chat runs locally first.
OUTSIDE_ALIAS = "mu3lab-outside"
# The voice assistant's chat name, routed by chat's setting with voice's own speed limits.
VOICE_ALIAS = "mu3lab-voice"
# Where voice kept its own setting before it followed chat; deleted when chat's setting is saved.
OLD_VOICE_SETTINGS = ("ai", "voice")
# FreeLLMAPI's list of outside models that answer with thinking off, quickly enough to talk to
# (apps/freellmapi/hooks.py keeps it); requests pick it as "auto:mu3lab-voice".
VOICE_OUTSIDE_LIST = "mu3lab-voice"
# Seconds a voice route may take to its first words before the next route is tried: a loaded
# local model starts in well under a second; an outside model answers whole, usually in 1 to 2 s.
VOICE_LOCAL_DEADLINE = 8
VOICE_OUTSIDE_DEADLINE = 4


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    # The exact Ollama tag, quantization included, so a download is the same model every time.
    tag: str
    download_gb: float
    # An estimate of the memory (GiB) it needs while answering with Ollama's whole context
    # (51,200 tokens, 8-bit cache; apps/ollama/docker-compose.yml) filled.
    memory_gb: float
    note: str
    dimensions: int = 0  # embedding models only
    # How its weights are stored, which the memory estimate assumes: Q4 is Q4_K_M; Gemma's QAT files
    # are Google's own 4-bit training; Ollama publishes the search models only at 16 bits.
    precision: str = "Q4"
    # Billions of parameters in the whole file (a Gemma "E4B" counts all 8), which sorts it into a size group.
    params_b: float = 0.0
    # Tags this model was listed under before; the runner's next start replaces those files.
    former_tags: tuple[str, ...] = ()


# Reviewed by the owner on 2026-10-10, and again on 2026-11-05 (Bonsai 2 27B added; Gemma 4 on
# Google's own 4-bit QAT files; Qwen3 8B and Phi-4-mini retired). Sizes are Ollama's published
# download sizes. Memory is for the 51,200-token context, from each file's layer layout: Qwen 3.5
# (Bonsai 2 is one, packed to ternary weights) keeps a cache in one layer of four and Gemma 4 mostly
# over a short window, so the cache stays small (Qwen 3.5 4B measured 4.1 GiB on an RTX 3060).
# Bonsai 2 27B's 11 GiB is that estimate, not yet measured.
CHAT: tuple[Model, ...] = (
    Model(
        "qwen3.5-2b",
        "Qwen 3.5 2B",
        "qwen3.5:2b-q4_K_M",
        1.9,
        3,
        "Fastest. Good for short answers and summaries.",
        params_b=2,
    ),
    Model(
        "qwen3.5-4b",
        "Qwen 3.5 4B",
        "qwen3.5:4b-q4_K_M",
        3.3,
        5,
        "A good balance on most home servers.",
        params_b=4,
    ),
    Model(
        "gemma4-e4b",
        "Gemma 4 E4B",
        "gemma4:e4b-it-qat",
        6.1,
        7.5,
        "Also understands images and audio.",
        precision="Q4 (QAT)",
        params_b=8,
        former_tags=("gemma4:e4b-it-q4_K_M",),
    ),
    Model(
        "qwen3.5-9b",
        "Qwen 3.5 9B",
        "qwen3.5:9b-q4_K_M",
        6.6,
        8,
        "Strongest Qwen here; best with a GPU.",
        params_b=9,
    ),
    Model(
        "gemma4-12b",
        "Gemma 4 12B",
        "gemma4:12b-it-qat",
        7.2,
        9,
        "Strong all-rounder that also reads images; needs a GPU to be quick.",
        precision="Q4 (QAT)",
        params_b=12,
        former_tags=("gemma4:12b-it-q4_K_M",),
    ),
    Model(
        "bonsai2-27b",
        "Bonsai 2 27B",
        "bonsai-2:27b",
        8.3,
        11,
        "The smartest choice: a 27B model packed small. Needs a GPU with 12 GB.",
        precision="Ternary",
        params_b=27,
    ),
)


class Retired(NamedTuple):
    """A chat model taken off the list: the file it was, and the listed model that replaces it."""

    tag: str
    successor: str


# A setting that names a retired model reads as its successor; the runner's next start downloads
# the successor and deletes the retired file (``bring_up_to_date``).
RETIRED: dict[str, Retired] = {
    "qwen3-8b": Retired("qwen3:8b-q4_K_M", "qwen3.5-9b"),
    "phi4-mini": Retired("phi4-mini:3.8b-q4_K_M", "qwen3.5-4b"),
}
EMBEDDING: tuple[Model, ...] = (
    Model(
        "bge-m3",
        "BGE-M3",
        "bge-m3:567m",
        1.2,
        2,
        "Many languages; long documents.",
        params_b=0.57,
        dimensions=1024,
        precision="16-bit",
    ),
    Model(
        "nomic-embed-text",
        "Nomic Embed Text",
        "nomic-embed-text:latest",
        0.3,
        1,
        "The earlier default; English-first and very light.",
        params_b=0.14,
        dimensions=768,
        precision="16-bit",
    ),
)
DEFAULT_EMBEDDING = "bge-m3"
# Installs made before this setting existed keep the model their search was indexed with.
LEGACY_EMBEDDING = "nomic-embed-text"

CHAT_BY_ID = {model.id: model for model in CHAT}

# The dashboard lists chat models in these groups, by billions of parameters: under 5, 5 to 10,
# 10 to 18, and 18 or more. Each is the lower bound of its group.
SIZE_GROUPS: tuple[tuple[str, float], ...] = (("xsmall", 0), ("small", 5), ("medium", 10), ("large", 18))


def size_group(model: Model) -> str:
    """The group a model is listed in: the last whose lower bound its parameter count reaches."""
    return [name for name, floor in SIZE_GROUPS if model.params_b >= floor][-1]


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


def privacy_of(sources: list[str]) -> str:
    """The order of sources as one of the three privacy words."""
    if "outside" not in sources:
        return "local_only"
    return "local_first" if sources[0] == "local" else "outside_allowed"


def _sources(new: dict[str, Any]) -> list[str]:
    """The ordered sources; a record saved before the order existed names a privacy word instead."""
    if "sources" in new:
        raw = new["sources"]
        # A job's parameters are strings, so there the order is "local,outside" (``as_params``).
        raw = raw.split(",") if isinstance(raw, str) else raw
        sources = [str(source) for source in raw] if isinstance(raw, list | tuple) else []
        if not sources or len(set(sources)) != len(sources) or any(source not in SOURCES for source in sources):
            raise ValueError("Turn on local models, outside providers or both.")
        return sources
    privacy = str(new.get("privacy", ""))
    if privacy not in PRIVACY:
        raise ValueError("Turn on local models, outside providers or both.")
    if privacy == "local_only":
        return ["local"]
    if privacy == "local_first":
        return ["local", "outside"]
    return ["outside", "local"] if new.get("chat_model") else ["outside"]


def validate(new: dict[str, Any]) -> dict[str, Any]:
    """The settings as stored; raises ValueError with a sentence for the person.

    ``chat_model`` is kept while local models are off, so turning them back on needs no new choice.
    """
    sources = _sources(new)
    chat = str(new.get("chat_model") or "")
    if chat and chat not in CHAT_BY_ID:
        raise ValueError("Choose a local chat model from the list.")
    embedding = str(new.get("embedding_model") or "")
    if embedding not in EMBEDDING_BY_ID:
        raise ValueError("Choose an embedding model from the list.")
    if "local" in sources and not chat:
        raise ValueError("Local models need a chat model. Choose one, or turn local models off.")
    runner = str(new.get("runner") or "")
    if runner and model_runners.find(runner) is None:
        raise ValueError("Choose a model runner from the list.")
    return {"sources": sources, "chat_model": chat, "embedding_model": embedding, "runner": runner}


def as_params(chosen: dict[str, Any]) -> dict[str, str]:
    """Validated settings as a job's string parameters."""
    return {**chosen, "sources": ",".join(chosen["sources"])}


def settings(paths: RuntimePaths | None = None) -> dict[str, Any]:
    """The stored settings, with ``privacy`` worked out from the order of sources."""
    paths = paths or RuntimePaths()
    stored = records.get(*SETTINGS, paths)
    if stored.get("chat_model") in RETIRED:
        stored = {**stored, "chat_model": RETIRED[stored["chat_model"]].successor}
    defaults = {
        "sources": ["outside"],
        "chat_model": "",
        "embedding_model": _installed_embedding(paths),
        "runner": "",
    }
    merged = {**defaults, **stored}
    if "privacy" in stored and "sources" not in stored:
        del merged["sources"]  # saved before the order existed: its privacy word says the sources
    try:
        clean = validate(merged)
    except ValueError:
        # An existing invalid record cannot authorize external inference.
        clean = {
            "sources": ["local"],
            "chat_model": str(stored.get("chat_model") or "") if stored.get("chat_model") in CHAT_BY_ID else "",
            "embedding_model": (
                str(stored["embedding_model"])
                if stored.get("embedding_model") in EMBEDDING_BY_ID
                else defaults["embedding_model"]
            ),
            "runner": defaults["runner"],
        }
    return {
        **clean,
        "privacy": privacy_of(clean["sources"]),
        "changed_at": str(stored.get("changed_at") or ""),
        "changed_by": str(stored.get("changed_by") or ""),
    }


def save(chosen: dict[str, Any], actor: str, paths: RuntimePaths | None = None) -> None:
    paths = paths or RuntimePaths()
    records.put(*SETTINGS, {**validate(chosen), "changed_at": _now(), "changed_by": actor}, paths)
    records.delete(*OLD_VOICE_SETTINGS, paths)


def local_only(paths: RuntimePaths | None = None) -> bool:
    return settings(paths)["privacy"] == "local_only"


def chat_model(paths: RuntimePaths | None = None) -> Model | None:
    """The local chat model, while local models are on."""
    chosen = settings(paths)
    return CHAT_BY_ID.get(chosen["chat_model"]) if "local" in chosen["sources"] else None


def embedding_model(paths: RuntimePaths | None = None) -> Model:
    return EMBEDDING_BY_ID[settings(paths)["embedding_model"]]


def runner(paths: RuntimePaths | None = None, catalog: Catalog | None = None) -> App:
    """The model runner requests go to: the owner's choice while it is installed, else the default."""
    return model_runners.effective(settings(paths)["runner"], catalog)


# --- The voice assistant ------------------------------------------------------
#
# Voice always follows chat: the same sources and the same local model, so the
# runner never holds two chat models at once. Only its routes differ (``_voice_routes``).


def voice_settings(paths: RuntimePaths | None = None) -> dict[str, str]:
    """Where the voice assistant's answers come from: chat's privacy word and its local model."""
    chat = settings(paths)
    return {"privacy": chat["privacy"], "model": chat["chat_model"] if "local" in chat["sources"] else ""}


def voice_model(paths: RuntimePaths | None = None) -> Model | None:
    """The local model voice runs on (chat's), or None while voice uses outside providers."""
    return chat_model(paths) if settings(paths)["privacy"] in {"local_only", "local_first"} else None


def outside_used(paths: RuntimePaths | None = None) -> bool:
    """Whether chat (and so voice) may use outside providers, so their keys must stay on."""
    return not local_only(paths)


def in_use(paths: RuntimePaths | None = None) -> set[Model]:
    """Every model a setting uses now; nothing here is deleted to free space."""
    chat = chat_model(paths)
    return {embedding_model(paths)} | ({chat} if chat else set())


def chat_list(paths: RuntimePaths | None = None) -> str:
    """LobeChat's model list, each name saying where the model runs (R31: honest labels in chat)."""
    chosen = settings(paths)
    model = chat_model(paths)
    if chosen["sources"] == ["local"]:
        return f"-all,+mu3lab-chat={model.name} (local only, on this server)" if model else "-all"
    if chosen["sources"][0] == "local" and model:
        return f"-all,+mu3lab-chat={model.name} (private, on this server),+{OUTSIDE_ALIAS}=Outside providers"
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
    local = chat_model(paths)
    engine = model_runners.engine(runner(paths, catalog), paths)

    def on_runner(name: str, model: str) -> dict:
        params = {"model": model, "api_base": f"os.environ/{runner_api_base_env}"}
        if engine.api_key():
            params["api_key"] = f"os.environ/{runner_api_key_env}"
        return {"model_name": name, "litellm_params": params}

    def outside(name: str, model: str) -> dict:
        return {
            "model_name": name,
            "litellm_params": {
                "model": model,
                "api_base": f"os.environ/{api_base_env}",
                "api_key": f"os.environ/{service_key_env}",
            },
        }

    routes: list[dict] = []
    if chosen["sources"][0] == "local":
        # Without outside providers no chat route to the provider gateway exists: if the local model fails, chat fails.
        if local:
            routes += [on_runner(name, engine.chat_route(local)) for name in ("mu3lab-chat", "mu3lab-fast")]
        if "outside" in chosen["sources"]:
            routes.append(outside(OUTSIDE_ALIAS, "openai/auto:smartest"))
    else:
        routes += [outside("mu3lab-chat", "openai/auto:smartest"), outside("mu3lab-fast", "openai/auto:fastest")]
    if local:
        routes.append(on_runner(LOCAL_ALIAS, engine.chat_route(local)))
    routes += _voice_routes(on_runner, outside, engine, paths)
    routes.append(on_runner("mu3lab-embed", engine.embedding_route(EMBEDDING_BY_ID[chosen["embedding_model"]])))
    return routes


def _voice_routes(
    on_runner: Callable[[str, str], dict],
    outside: Callable[[str, str], dict],
    engine: model_runners.Engine,
    paths: RuntimePaths,
) -> list[dict]:
    """The voice name's routes, tried in order: the local model, then outside if allowed.

    Outside, FreeLLMAPI's voice list comes first, then any model as a last resort, for when the
    list is used up (each free model allows a few dozen requests a day) or missing. Thinking is set
    per route, because the agent cannot tell where its model runs: "none" turns it off (LiteLLM
    turns any other level into Ollama's think: true, about 10 s of thinking), and "low" is the
    lowest level every outside model accepts. A route with another after it gives up when its
    first words are late, without retrying, so a stuck model (free models sometimes wait a
    minute) hands over to the next while the person is still listening. Local only with no model
    leaves no route, and the voice app will not start.
    """
    voice = voice_settings(paths)
    routes: list[tuple[dict, str, int]] = []
    local = voice_model(paths)
    if local:
        routes.append((on_runner(VOICE_ALIAS, engine.chat_route(local)), "none", VOICE_LOCAL_DEADLINE))
    if voice["privacy"] != "local_only":
        routes.append((outside(VOICE_ALIAS, f"openai/auto:{VOICE_OUTSIDE_LIST}"), "none", VOICE_OUTSIDE_DEADLINE))
        routes.append((outside(VOICE_ALIAS, "openai/auto:smartest"), "low", 0))
    for order, (route, effort, deadline) in enumerate(routes, 1):
        route["litellm_params"] |= {"reasoning_effort": effort, "order": order}
        if order < len(routes):
            route["litellm_params"] |= {"stream_timeout": deadline, "max_retries": 0, "num_retries": 0}
    return [route for route, _, _ in routes]


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
        # A stopped runner still shows what it has downloaded.
        present, reachable = engine.on_disk(), False

    from ctl import model_fit

    try:
        fit = model_fit.report(paths)
    except Exception:  # informational: a failed measurement must not hide the settings
        fit = {"suggested": "", "reason": "", "fits": {}}
    used = {model.id for model in in_use(paths)}

    def view(model: Model) -> dict[str, Any]:
        verdict = fit["fits"].get(model.id) or {}
        listed = {key: value for key, value in asdict(model).items() if key != "former_tags"}
        return {
            **listed,
            "downloaded": engine.has(model, present),
            "in_use": model.id in used,
            "size_group": size_group(model),
            "fit": verdict.get("fit", ""),
            "fit_where": verdict.get("where", ""),
        }

    return {
        "suggested": fit["suggested"],
        "suggestion_reason": fit["reason"],
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
        # Stopped from Home: trying, removing or switching a model starts it for that and stops it again.
        "runner_stopped": model_runners.stopped(current.id),
    }


TRY_PROMPT = "In three short sentences, explain what a home server is and why someone might want one."


def try_model(target: str, prompt: str = "", paths: RuntimePaths | None = None) -> dict[str, Any]:
    """One test answer from a downloaded local model, or from outside providers (``target`` "outside")."""
    import json as _json
    import urllib.request

    paths = paths or RuntimePaths()
    prompt = (prompt or TRY_PROMPT).strip()[:2000]
    if target != "outside":
        model = CHAT_BY_ID.get(target)
        if model is None:
            raise ValueError("Choose a local chat model from the list.")
        engine = model_runners.engine(runner(paths), paths)
        if not engine.has(model, engine.on_disk() if model_runners.stopped(engine.app.id) else engine.downloaded()):
            raise ValueError(f"Download {model.name} first.")
        with model_runners.awake(engine, ROOT, lambda _line: None) as started:
            answer = engine.try_chat(model, prompt)
        return {"where": "this server", "name": model.name, **answer, "runner_started": started}
    chosen = settings(paths)
    if "outside" not in chosen["sources"]:
        raise ValueError("Outside providers are switched off: AI runs only on this server.")
    alias = OUTSIDE_ALIAS if chosen["sources"][0] == "local" else "mu3lab-chat"
    from ctl.platform_apps import by_capability
    from ctl.secrets import read_runtime_env

    key = read_runtime_env(paths.projects / by_capability("model_proxy").id / ".env").get("LITELLM_MASTER_KEY", "")
    request = urllib.request.Request(
        "http://127.0.0.1:4000/v1/chat/completions",
        data=_json.dumps(
            {"model": alias, "messages": [{"role": "user", "content": prompt}], "max_tokens": 400}
        ).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    began = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            answer = _json.load(response)
    except OSError as exc:
        raise ValueError(f"Your outside providers did not answer: {exc}") from None
    seconds = time.monotonic() - began
    choices = answer.get("choices") or [{}]
    tokens = int((answer.get("usage") or {}).get("completion_tokens") or 0)
    return {
        "where": "outside providers",
        "name": str(answer.get("model") or "Outside providers"),
        "answer": str((choices[0].get("message") or {}).get("content") or "").strip(),
        "seconds": round(seconds, 1),
        "loading_seconds": 0.0,
        "tokens_per_second": round(tokens / seconds, 1) if seconds else 0.0,
    }


def remove(model_id: str, paths: RuntimePaths | None = None) -> str:
    """Delete one downloaded model nothing uses now; returns a sentence for the person."""
    paths = paths or RuntimePaths()
    model = CHAT_BY_ID.get(model_id) or EMBEDDING_BY_ID.get(model_id)
    if model is None:
        raise ValueError("Choose a model from the list.")
    if model in in_use(paths):
        raise ValueError(f"{model.name} is in use. Choose another model first.")
    current = runner(paths)
    engine = model_runners.engine(current, paths)
    if not engine.has(model, engine.on_disk()):
        return f"{model.name} is not downloaded."
    # The runner removes its own models, so a stopped one is started for it and stopped again.
    with model_runners.awake(engine, ROOT, lambda _line: None):
        if not engine.has(model, engine.downloaded()):
            return f"{model.name} is not downloaded."
        engine.delete(model)
    return f"Removed {model.name}; {model.download_gb} GB of disk space is free again."


def bring_up_to_date(engine, log: Callable[[str], None]) -> None:
    """Move the runner onto the reviewed list after the list changed; called each time it starts.

    The chosen chat model downloads under its current tag when the runner holds only an earlier
    file of it (a former tag, or the retired model it replaces). Then the earlier files are deleted:
    nothing routes to them, and the dashboard lists only current models, so it cannot remove them.
    A download that fails keeps every file; the chat apps then say to save the choice again.
    """
    present = engine.downloaded()
    chosen = chat_model(engine.paths)
    earlier = {
        model.id: [*model.former_tags, *(old.tag for old in RETIRED.values() if old.successor == model.id)]
        for model in CHAT
    }
    replacing = (
        chosen is not None
        and not engine.has(chosen, present)
        and any(engine.has(replace(chosen, tag=tag), present) for tag in earlier[chosen.id])
    )
    if chosen and replacing:
        log(f"Downloading {chosen.name} ({chosen.download_gb} GB) to replace the earlier model file it follows.")
        try:
            engine.pull(chosen, log)
            engine.check_chat(chosen)
        except model_runners.ENGINE_ERRORS as exc:
            log(f"{chosen.name} could not be downloaded ({exc}); its earlier file is kept.")
            return
        present = engine.downloaded()
    # No earlier file is in use now: the chosen model has its current file, or never had one.
    for model in CHAT:
        for tag in earlier[model.id]:
            if engine.has(replace(model, tag=tag), present):
                engine.delete(replace(model, tag=tag))
                log(f"Deleted {tag}, an earlier file that nothing uses now.")


# --- The settings job -------------------------------------------------------


def _probe_litellm(log, alias: str = LOCAL_ALIAS) -> None:
    from ctl.platform_apps import by_capability
    from ctl.provider_ops import _probe_stream
    from ctl.secrets import read_runtime_env

    key = read_runtime_env(RuntimePaths().projects / by_capability("model_proxy").id / ".env").get(
        "LITELLM_MASTER_KEY", ""
    )
    probe = _probe_stream(alias, key, gateway="LiteLLM", url="http://127.0.0.1:4000/v1/chat/completions")
    log(f"LiteLLM check of {alias}: {probe.error_code or 'complete'}")
    if not probe.success:
        raise RuntimeError(f"Chat through LiteLLM did not answer as {alias}: {probe.detail}")


def _apply(paths: RuntimePaths, root: Path, log) -> None:
    """Write the routing for the stored settings and restart only what changed."""
    from ctl.provider_ops import _reconcile

    ok, detail, _ = _reconcile(root, log)
    if not ok:
        raise RuntimeError(detail)


def _pull_reporter(service_id: str, job_id: str) -> tuple[Callable[[int, int], None], Callable[[], None]]:
    """A byte-progress reporter for a download, where the dashboard reads it (the same bar as app
    images), and the call that marks it done."""
    from ctl.image_downloads import ImageDownloadStore

    downloads = ImageDownloadStore.runtime()
    started = time.monotonic()
    last = 0.0

    def report(done: int, total: int) -> None:
        nonlocal last
        now = time.monotonic()
        # About once a second is enough for a bar, and keeps the database quiet.
        if downloads is None or (now - last < 1 and done < total):
            return
        last = now
        rate = done / (now - started) if now > started else 0
        downloads.update(service_id, job_id, {"total_bytes": total, "done_bytes": done, "rate_bps": rate})

    def finish() -> None:
        if downloads is not None:
            downloads.update(service_id, job_id, {"state": "done"})

    return report, finish


def _pull(engine, model: Model, log, job_id: str) -> None:
    """Download a model into the runner, showing its byte progress."""
    report, finish = _pull_reporter(SERVICE_ID, job_id)
    engine.pull(model, log, report)
    finish()


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

    def roll_back(reason: str, code: str, step: str) -> None:
        """Put the earlier settings back; if that fails too, never claim more privacy than may be true."""
        save(before, before.get("changed_by") or actor, paths)
        try:
            _apply(paths, root, log)
        except (OSError, ValueError, RuntimeError) as undo:
            # The routing may still be the new one, so the record must not say local only when either allows outside.
            outside = any("outside" in sources for sources in (before["sources"], chosen["sources"]))
            if outside:
                save(
                    {**before, "sources": ["outside", *(s for s in before["sources"] if s != "outside")]}, actor, paths
                )
            where = " Outside providers may still be used." if outside else ""
            fail(
                f"{reason} Putting the earlier settings back also failed ({redact(str(undo))}).{where} "
                "Choose your AI settings again to retry.",
                "rollback_failed",
                step,
            )
            return
        fail(f"The earlier settings were kept: {reason}", code, step)

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
    chat = CHAT_BY_ID.get(chosen["chat_model"]) if "local" in chosen["sources"] else None
    embed = EMBEDDING_BY_ID[chosen["embedding_model"]]
    target = model_runners.find(chosen["runner"], catalog) if chosen["runner"] else runner(paths, catalog)
    if target is None or not model_runners.installed(target.id):
        fail("Install that model runner from Apps first.", "runner_missing", "validate")
        return
    chosen["runner"] = target.id
    engine = model_runners.engine(target, paths)
    # A runner stopped from Home is started for the change and stopped again after it.
    wake = ExitStack()
    try:
        started = wake.enter_context(model_runners.awake(engine, root, log))
    except model_runners.ENGINE_ERRORS as exc:
        fail(f"Nothing was changed: {exc}", "runner_unavailable", "download")
        return
    with wake:
        # 1. Download and check the models first: until this succeeds nothing has changed.
        try:
            present = engine.downloaded()
            for model, check in ((chat, engine.check_chat), (embed, engine.check_embedding)):
                if model is None:
                    continue
                if not engine.has(model, present):
                    log(f"Downloading {model.name} into {target.manifest.name} ({model.download_gb} GB).")
                    _pull(engine, model, log, job_id)
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
            roll_back(str(exc), "routing_failed", "route")
            return
        # 3. Apps whose settings follow the models: the chat app's list and the search apps.
        facts = Facts(tailnet_dns_name(), paths, catalog)

        def follow() -> list[str]:
            """Rewire every follower; return the names of those still on the old settings."""
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
            return incomplete

        incomplete = follow()
    if incomplete:
        # An app still on the old settings may still need an old model, so nothing is deleted.
        fail(
            "AI routing changed, but these apps still need updating: "
            + ", ".join(incomplete)
            + ". All previously downloaded models were retained. Retry when the apps are ready.",
            "followers_incomplete",
            "followers",
        )
        return
    # Models no longer used are kept, so switching back is instant; Settings removes one (``remove``).
    where = {
        ("local",): "only on this server",
        ("local", "outside"): "on this server first, with your outside providers as a choice",
        ("outside", "local"): "on your outside providers first, with this server as a private choice",
    }.get(tuple(chosen["sources"]), "only on your outside providers")
    detail = (
        f"AI now runs {where}, in {target.manifest.name}. "
        f"Chat model: {chat.name if chat else 'none'}. Search: {embed.name}."
    )
    if started:
        detail += f" {target.manifest.name} was stopped, so it ran for this change and is stopped again: {STOPPED_NOTE}"
    store.record_audit(actor=actor, event="ai.models_changed", detail=detail, job_id=job_id)
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
