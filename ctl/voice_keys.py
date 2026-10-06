"""Personal keys that let a person's own devices and programs use Mu3Lab's speech.

A key works only for the speech models (``mu3lab-stt``, ``mu3lab-tts``), is
limited to a modest request rate, and belongs to one Authentik person. Mu3Lab
shows it once and keeps no copy; LiteLLM stores only a hash. Devices call
LiteLLM's private address with it, which skips the Authentik browser gate
for bearer-key requests only.
"""

from __future__ import annotations

from typing import Any

from ctl.core_wiring import SPEECH_TO_TEXT, TEXT_TO_SPEECH
from ctl.integrations.litellm import LiteLLM, LiteLLMError
from ctl.manifest.catalog import Catalog, cached
from ctl.platform_apps import by_capability
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

MODELS = [SPEECH_TO_TEXT, TEXT_TO_SPEECH]
RPM_LIMIT = 60
MASTER_KEY_ENV = "LITELLM_MASTER_KEY"


def alias(uid: str) -> str:
    return f"mu3lab-voice-{uid}"


def speech_installed(catalog: Catalog, paths: RuntimePaths) -> bool:
    return any(
        "speech" in app.manifest.capabilities and (paths.projects / app.id / "docker-compose.yml").is_file()
        for app in catalog.apps
    )


def _client(catalog: Catalog, paths: RuntimePaths) -> LiteLLM:
    proxy = by_capability("model_proxy", catalog)
    master = read_runtime_env(paths.projects / proxy.id / ".env").get(MASTER_KEY_ENV, "")
    if not master:
        raise LiteLLMError("The AI gateway is not set up yet.")
    return LiteLLM(f"http://127.0.0.1:{proxy.manifest.service.local_port}", master)


def base_url(dns_name: str, catalog: Catalog) -> str:
    route = by_capability("model_proxy", catalog).manifest.route
    if not dns_name or route is None:
        return ""
    return f"https://{dns_name}:{route.https_port}/v1"


def status(uid: str, catalog: Catalog | None = None, paths: RuntimePaths | None = None) -> dict[str, Any]:
    catalog, paths = catalog or cached(), paths or RuntimePaths()
    with _client(catalog, paths) as client:
        mine = [key for key in client.keys(uid) if key.get("key_alias") == alias(uid)]
    return {"exists": bool(mine), "created_at": str(mine[0].get("created_at") or "") if mine else ""}


def create(uid: str, catalog: Catalog | None = None, paths: RuntimePaths | None = None) -> str:
    """Replace the person's voice key with a new one and return it (shown once)."""
    catalog, paths = catalog or cached(), paths or RuntimePaths()
    with _client(catalog, paths) as client:
        if any(key.get("key_alias") == alias(uid) for key in client.keys(uid)):
            client.delete([alias(uid)])
        return client.generate(user_id=uid, alias=alias(uid), models=MODELS, rpm_limit=RPM_LIMIT)


def revoke(uid: str, catalog: Catalog | None = None, paths: RuntimePaths | None = None) -> None:
    catalog, paths = catalog or cached(), paths or RuntimePaths()
    with _client(catalog, paths) as client:
        if any(key.get("key_alias") == alias(uid) for key in client.keys(uid)):
            client.delete([alias(uid)])
