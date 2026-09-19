"""Generate the private AI integration contract from one secret store.

Mu3Lab owns the interface between user credentials, FreeLLMAPI, LiteLLM and
the applications.  Generated files live under /srv, are mode 0600, and are
never served by the dashboard or committed to Git.
"""

from __future__ import annotations

import os
from pathlib import Path

import json
import yaml

from ctl.provider_secrets import records
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

EMBEDDING_MODEL = "nomic-embed-text"


def _write_private(path: Path, content: str) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    return path


def _env_text(values: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def configure(paths: RuntimePaths = RuntimePaths()) -> dict[str, Path | bool | int]:
    """Create deterministic LiteLLM wiring and private provider hand-off.

    Provider credentials intentionally remain in FreeLLMAPI's private config
    boundary. LiteLLM gets one generated internal service credential, never a
    user provider key.
    """
    project_root = paths.projects
    litellm_env_path = project_root / "litellm" / ".env"
    free_env_path = project_root / "freellmapi" / ".env"
    litellm_env = read_runtime_env(litellm_env_path)
    free_env = read_runtime_env(free_env_path)
    service_key = free_env.get("FREELLMAPI_SERVICE_KEY")
    if not service_key or not litellm_env.get("LITELLM_MASTER_KEY"):
        raise ValueError("core service credentials have not been initialized")

    providers = records(paths)
    from ctl.control_state import ControlState
    state = ControlState.runtime(paths)
    if state:
        providers = [item for item in providers
                     if (connection := state.provider(item["id"]))
                     and connection["enabled"] and connection["state"] in {"verifying", "verified"}]
    # This is the Mu3Lab-owned, supported declarative hand-off.  The adapter
    # records provider names/keys privately; the runtime verifier refuses to
    # claim chat readiness until the pinned FreeLLMAPI image accepts it.
    free_config = {
        "keys": [{"platform": item["id"], "key": item["api_key"],
                  "label": item["label"], "enabled": True} for item in providers],
        "routing": {"strategy": "smartest"},
    }
    free_config_path = _write_private(
        project_root / "freellmapi" / "freellmapi.config.json",
        json.dumps(free_config, sort_keys=True, separators=(",", ":")) + "\n",
    )
    free_env["FREEAPI_CONFIG_PATH"] = "/mu3lab/config/freellmapi.config.json"
    free_env.setdefault("FREELLMAPI_SERVICE_KEY", service_key)
    _write_private(free_env_path, _env_text(free_env))

    litellm_env["FREELLMAPI_API_BASE"] = "http://freellmapi:3001/v1"
    litellm_env["FREELLMAPI_SERVICE_KEY"] = service_key
    litellm_config = {
        "model_list": [
            {"model_name": "mu3lab-chat", "litellm_params": {
                "model": "openai/auto:smartest", "api_base": "os.environ/FREELLMAPI_API_BASE",
                "api_key": "os.environ/FREELLMAPI_SERVICE_KEY"}},
            {"model_name": "mu3lab-fast", "litellm_params": {
                "model": "openai/auto:fastest", "api_base": "os.environ/FREELLMAPI_API_BASE",
                "api_key": "os.environ/FREELLMAPI_SERVICE_KEY"}},
            {"model_name": "mu3lab-embed", "litellm_params": {
                "model": f"ollama/{EMBEDDING_MODEL}", "api_base": "os.environ/OLLAMA_API_BASE"}},
        ],
        "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY"},
        "litellm_settings": {"drop_params": True},
    }
    litellm_config_path = _write_private(
        project_root / "litellm" / "config.yaml", yaml.safe_dump(litellm_config, sort_keys=False),
    )
    _write_private(litellm_env_path, _env_text(litellm_env))
    return {"litellm_config": litellm_config_path, "freellmapi_config": free_config_path,
            "provider_count": len(providers), "chat_configured": bool(providers)}
