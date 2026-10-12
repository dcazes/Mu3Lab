"""The app that runs the household's local AI models, and how Mu3Lab drives it.

Two model runners can be installed (capability ``model_runner``): Unsloth
Studio, which the installer sets up and is the default, and Ollama, an
optional app. The owner picks one in Settings > AI, as part of the AI model
settings (``ctl.local_models``): the chosen models are downloaded and checked
in it before any request moves there.

Each runner's ``model_runner.engine`` says which API Mu3Lab speaks to it. An
engine knows how to download, check and delete the reviewed models, and how
LiteLLM reaches it.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from ctl.control_state import ControlState
from ctl.integrations.unsloth_studio import CONTAINER_PORT, KEY_ENV, Studio
from ctl.manifest.catalog import App, Catalog, load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

if TYPE_CHECKING:
    from ctl.local_models import Model

CAPABILITY = "model_runner"
Log = Callable[[str], None]


class RunnerError(RuntimeError):
    """A safe message for the dashboard or a job log."""


def runners(catalog: Catalog | None = None) -> list[App]:
    return [app for app in (catalog or load()).apps if CAPABILITY in app.manifest.capabilities]


def default_app(catalog: Catalog | None = None) -> App:
    """The runner the installer sets up (the core one)."""
    return next(app for app in runners(catalog) if app.manifest.tier == "core")


def installed(app_id: str, state: ControlState | None = None) -> bool:
    state = state or ControlState.runtime()
    record = state.installation(app_id) if state else None
    return bool(record and record["state"] != "not_installed")


def find(app_id: str, catalog: Catalog | None = None) -> App | None:
    return next((app for app in runners(catalog) if app.id == app_id), None)


def effective(chosen: str, catalog: Catalog | None = None) -> App:
    """The owner's choice while it is installed; else the default, or whichever runner is installed."""
    state = ControlState.runtime()
    apps = runners(catalog)
    for app in sorted(apps, key=lambda app: (app.id != chosen, app.manifest.tier != "core")):
        if installed(app.id, state):
            return app
    return default_app(catalog)


# --- Engines ----------------------------------------------------------------------


class Engine(Protocol):
    app: App
    # True when the runner serves search from its start-up settings, so a new search model answers after a restart.
    embedding_follows_restart: bool

    def api_base(self) -> str: ...
    def api_key(self) -> str: ...
    def chat_route(self, model: Model) -> str: ...
    def embedding_route(self, model: Model) -> str: ...
    def downloaded(self) -> set[str]: ...
    def has(self, model: Model, present: set[str]) -> bool: ...
    def pull(self, model: Model, log: Log) -> None: ...
    def delete(self, model: Model) -> None: ...
    def check_chat(self, model: Model) -> None: ...
    def check_embedding(self, model: Model) -> None: ...


class OllamaEngine:
    """Ollama's own API on its loopback port; models are named by their exact tag."""

    embedding_follows_restart = False

    def __init__(self, app: App, paths: RuntimePaths) -> None:
        self.app = app
        self.base = f"http://127.0.0.1:{app.manifest.service.local_port}"

    def _call(self, path: str, payload: dict | None = None, *, method: str = "POST", timeout: float = 300) -> Any:
        request = urllib.request.Request(
            f"{self.base}{path}",
            method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
        return json.loads(body) if body.strip() else {}

    @staticmethod
    def name(tag: str) -> str:
        return tag if ":" in tag else f"{tag}:latest"

    def api_base(self) -> str:
        return f"http://{self.app.id}:{self.app.manifest.service.local_port}"

    def api_key(self) -> str:
        return ""

    def chat_route(self, model: Model) -> str:
        return f"ollama_chat/{model.tag}"

    def embedding_route(self, model: Model) -> str:
        return f"ollama/{model.tag}"

    def downloaded(self) -> set[str]:
        tags = self._call("/api/tags", method="GET", timeout=15)
        return {self.name(str(item.get("name", ""))) for item in tags.get("models", []) if isinstance(item, dict)}

    def has(self, model: Model, present: set[str]) -> bool:
        return self.name(model.tag) in present

    def pull(self, model: Model, log: Log) -> None:
        """Download through Ollama, logging progress about every tenth of the way."""
        request = urllib.request.Request(
            f"{self.base}/api/pull",
            method="POST",
            data=json.dumps({"model": model.tag, "stream": True}).encode(),
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
                        log(f"Downloading {model.tag}: {tenth * 10}% of {total / 1e9:.1f} GB")
                if event.get("status") == "success":
                    return
        raise RuntimeError(f"Ollama stopped before {model.tag} finished downloading.")

    def delete(self, model: Model) -> None:
        self._call("/api/delete", {"model": model.tag}, method="DELETE", timeout=60)

    def check_chat(self, model: Model) -> None:
        """Ask for a short answer directly in Ollama (the first one loads it into memory)."""
        answer = self._call(
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

    def check_embedding(self, model: Model) -> None:
        answer = self._call("/api/embed", {"model": model.tag, "input": "Mu3Lab readiness"}, timeout=600)
        vectors = answer.get("embeddings")
        if not (isinstance(vectors, list) and vectors and len(vectors[0]) == model.dimensions):
            raise RuntimeError(f"{model.name} did not return {model.dimensions}-number vectors.")


class StudioEngine:
    """Unsloth Studio's API with Mu3Lab's key; models are GGUF files from Hugging Face."""

    embedding_follows_restart = True

    def __init__(self, app: App, paths: RuntimePaths) -> None:
        self.app = app
        self.key = read_runtime_env(paths.projects / app.id / ".env").get(KEY_ENV, "")

    def _studio(self) -> Studio:
        if not self.key:
            raise RunnerError(f"{self.app.manifest.name} has not finished setting up yet.")
        return Studio(self.app.manifest.service.local_port, token=self.key)

    def api_base(self) -> str:
        return f"http://{self.app.id}:{CONTAINER_PORT}/v1"

    def api_key(self) -> str:
        return self.key

    def chat_route(self, model: Model) -> str:
        return f"openai/{model.gguf}:{model.quant}"

    def embedding_route(self, model: Model) -> str:
        # Studio's search model is set at start (RAG_EMBEDDING_MODEL) and answers to its repository's name.
        return f"openai/{model.gguf}"

    def downloaded(self) -> set[str]:
        with self._studio() as studio:
            return studio.downloaded()

    def has(self, model: Model, present: set[str]) -> bool:
        return model.gguf in present

    def pull(self, model: Model, log: Log) -> None:
        log(f"Downloading {model.name} ({model.gguf}, {model.quant}).")
        with self._studio() as studio:
            studio.download(model.gguf, model.quant, log)

    def delete(self, model: Model) -> None:
        with self._studio() as studio:
            studio.delete(model.gguf, model.quant)

    def check_chat(self, model: Model) -> None:
        with self._studio() as studio:
            studio.chat(f"{model.gguf}:{model.quant}")

    def check_embedding(self, model: Model) -> None:
        with self._studio() as studio:
            vector = studio.embed(model.gguf)
        if len(vector) != model.dimensions:
            raise RuntimeError(
                f"{model.name} did not return {model.dimensions}-number vectors. If it was just chosen, "
                f"{self.app.manifest.name} restarts with it first."
            )


ENGINES: dict[str, type[OllamaEngine] | type[StudioEngine]] = {
    "ollama_api": OllamaEngine,
    "unsloth_studio": StudioEngine,
}
# Errors any engine raises for an unreachable runner or a refused request (StudioError is a RuntimeError).
ENGINE_ERRORS = (OSError, ValueError, RuntimeError)


def engine(app: App, paths: RuntimePaths | None = None) -> Engine:
    if app.manifest.model_runner is None:
        raise RunnerError(f"{app.manifest.name} is not a model runner.")
    return ENGINES[app.manifest.model_runner.engine](app, paths or RuntimePaths())
