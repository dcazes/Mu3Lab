"""Generate private provider routing files from each app's declared routing rule."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import model_validator

from ctl.app_settings import AppSettings
from ctl.control_state import ControlState
from ctl.manifest.catalog import App, Catalog, cached
from ctl.provider_catalog import BY_ID
from ctl.rules import Params
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl.store.providers import records


class RoutingParams(Params):
    mode: Literal["gateway", "models"]
    config_file: str
    gateway: str = ""
    runner: str = ""
    service_key_env: str = ""
    master_key_env: str = ""
    api_base_env: str = ""
    runner_api_base_env: str = ""
    embedding_model: str = ""
    embedding_provider: str = ""
    # Set by integrates_with when a speech app is installed; empty otherwise.
    speech_url_env: str = ""
    admin_email_env: str = ""
    admin_password_env: str = ""
    bootstrap_flag_env: str = ""

    @model_validator(mode="after")
    def _complete(self) -> RoutingParams:
        if Path(self.config_file).name != self.config_file or self.config_file in {"", ".", "..", ".env"}:
            raise ValueError("routing config_file must be a project-local filename")
        required = (
            ("admin_email_env", "admin_password_env", "bootstrap_flag_env")
            if self.mode == "gateway"
            else (
                "gateway",
                "runner",
                "service_key_env",
                "master_key_env",
                "api_base_env",
                "runner_api_base_env",
                "embedding_model",
                "embedding_provider",
            )
        )
        if any(not getattr(self, key) for key in required):
            raise ValueError(f"{self.mode} routing requires: {', '.join(required)}")
        return self


@dataclass(frozen=True)
class RoutingProject:
    app: App
    params: RoutingParams

    def files(self, paths: RuntimePaths) -> tuple[Path, Path]:
        project = paths.projects / self.app.id
        return project / ".env", project / self.params.config_file


def routing_projects(catalog: Catalog | None = None) -> list[RoutingProject]:
    projects = [
        RoutingProject(app, RoutingParams.model_validate(ref.with_))
        for app in (catalog or cached()).apps
        for ref in app.manifest.rules
        if ref.rule == "provider_routing"
    ]
    # Gateway settings and its client credential precede the model proxy's settings.
    return sorted(projects, key=lambda item: (item.params.mode != "gateway", item.app.id))


def _provider_keys(paths: RuntimePaths) -> list[dict]:
    state = ControlState.runtime(paths)

    def routable(provider_id: str) -> bool:
        if state is None:
            return True
        connection = state.provider(provider_id)
        return bool(connection and connection["enabled"] and connection["state"] in {"verifying", "verified"})

    # Import adds and updates keys; include disabled keys so they cannot keep routing.
    return [
        {"platform": item["id"], "key": item["api_key"], "label": item["label"], "enabled": routable(item["id"])}
        for item in records(paths)
        if item["id"] in BY_ID
    ]


# Stable names clients use, whichever speech models the speech app is set to.
SPEECH_TO_TEXT = "mu3lab-stt"
TEXT_TO_SPEECH = "mu3lab-tts"


def speech_models(env: dict[str, str], params: RoutingParams) -> list[dict]:
    """Route the stable speech names to the installed speech app, if there is one."""
    base = env.get(params.speech_url_env, "") if params.speech_url_env else ""
    stt, tts = env.get("MU3LAB_STT_MODEL", ""), env.get("MU3LAB_TTS_MODEL", "")
    if not base or not stt or not tts:
        return []
    return [
        {
            "model_name": name,
            # The speech app is internal-only and needs no key; LiteLLM's client still requires one.
            "litellm_params": {"model": f"openai/{model}", "api_base": base, "api_key": "unused"},
            "model_info": {"mode": mode},
        }
        for name, model, mode in (
            (SPEECH_TO_TEXT, stt, "audio_transcription"),
            (TEXT_TO_SPEECH, tts, "audio_speech"),
        )
    ]


def write_routing(app: App, params: RoutingParams, paths: RuntimePaths, catalog: Catalog) -> dict[str, bool | int]:
    env_path, config_path = RoutingProject(app, params).files(paths)
    env = read_runtime_env(env_path)
    if not env:
        raise ValueError(f"{app.manifest.name}'s private project has not been prepared.")
    keys = _provider_keys(paths)
    if params.mode == "gateway":
        config: dict = {"keys": keys, "routing": {"strategy": "smartest"}}
        if env.get(params.bootstrap_flag_env) != "true":
            email, password = env.get(params.admin_email_env), env.get(params.admin_password_env)
            if not email or not password:
                raise ValueError("The provider gateway's first administrator settings are missing.")
            config["admin"] = {"email": email, "password": password}
        write_atomic(config_path, (json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n").encode())
    else:
        gateway = catalog.get(params.gateway)
        runner = catalog.get(params.runner)
        gateway_env = read_runtime_env(paths.projects / gateway.id / ".env")
        service_key = gateway_env.get(params.service_key_env, "")
        if not service_key or not env.get(params.master_key_env):
            raise ValueError("The provider gateway's internal service credential is not ready.")
        env[params.service_key_env] = service_key
        env[params.api_base_env] = f"http://{gateway.id}:{gateway.manifest.service.local_port}/v1"
        env[params.runner_api_base_env] = f"http://{runner.id}:{runner.manifest.service.local_port}"
        # Canonical first, so the next render of the model proxy keeps them.
        AppSettings(app.id, paths).set_generated(
            {name: env[name] for name in (params.service_key_env, params.api_base_env, params.runner_api_base_env)}
        )
        models = [
            {
                "model_name": name,
                "litellm_params": {
                    "model": model,
                    "api_base": f"os.environ/{params.api_base_env}",
                    "api_key": f"os.environ/{params.service_key_env}",
                },
            }
            for name, model in (("mu3lab-chat", "openai/auto:smartest"), ("mu3lab-fast", "openai/auto:fastest"))
        ]
        models.append(
            {
                "model_name": "mu3lab-embed",
                "litellm_params": {
                    "model": f"{params.embedding_provider}/{params.embedding_model}",
                    "api_base": f"os.environ/{params.runner_api_base_env}",
                },
            }
        )
        models.extend(speech_models(env, params))
        config = {
            "model_list": models,
            # Keys and budgets live in LiteLLM's database; what people say or ask is never logged there.
            "general_settings": {"master_key": f"os.environ/{params.master_key_env}", "disable_spend_logs": True},
            "litellm_settings": {"drop_params": True},
        }
        write_atomic(config_path, yaml.safe_dump(config, sort_keys=False).encode())
        write_atomic(env_path, runtime_env_text(env).encode())
    count = sum(1 for item in keys if item["enabled"])
    return {"provider_count": count, "chat_configured": bool(count)}


def configure(paths: RuntimePaths = RuntimePaths(), catalog: Catalog | None = None) -> dict[str, bool | int]:
    """Refresh already prepared projects after provider settings change."""
    catalog = catalog or cached()
    result: dict[str, bool | int] = {"provider_count": 0, "chat_configured": False}
    for project in routing_projects(catalog):
        result = write_routing(project.app, project.params, paths, catalog)
    return result
