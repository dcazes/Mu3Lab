"""Point SurfSense at the household chat model, through its own API.

SurfSense 2.x keeps one library per install and no accounts, so there is nobody
to register: Authentik guards the address and SurfSense's password is off. What
it needs is a model. Mu3Lab adds the AI gateway (LiteLLM) as an OpenAI-compatible
connection with the app's own gateway key, allows SurfSense to reach it (every
outside host starts switched off), picks the household chat model for chat, and
finishes SurfSense's first-run setup so nobody is shown it. Search keeps the
built-in embedding model, the only kind SurfSense supports.

Each step finds what an earlier run made, so running it again changes nothing.
Source: https://github.com/MODSetter/SurfSense/blob/v2.3.1/docs/architecture/connections.md
"""

from __future__ import annotations

from typing import Any

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError, request

GATEWAY = "http://litellm:4000/v1"
LABEL = "Mu3Lab AI"
CHAT_MODEL = "mu3lab-chat"


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        port = ctx.app.manifest.service.local_port
        key = ctx.env().get("MU3LAB_AI_KEY", "")
        if not key:
            ctx.fail("chat_model", "gateway_key_missing", "SurfSense has no AI gateway key.")
        ctx.stage("chat_model", "Connecting SurfSense to the household chat model.")
        try:
            # The gateway is not loopback, so SurfSense refuses to reach it until it is allowed.
            request(port, "/api/egress", method="PUT", data={"destinations": ["host:litellm"], "enabled": True})
            connection = _connection(port, key)
            selected = {"provider": "openai_compatible", "connection_id": connection, "name": CHAT_MODEL}
            # The gateway lists only the models this key may use, so mu3lab-chat is always listed.
            request(port, "/api/llm/selection/text_gen", method="PUT", data=selected)
            if not request(port, "/api/llm/onboarding").get("completed"):
                request(port, "/api/llm/onboarding", method="POST", data={})
        except LoopbackError as exc:
            ctx.fail("chat_model", "chat_model_failed", f"SurfSense did not accept the household chat model: {exc}")


def _connection(port: int, key: str) -> int:
    """The id of Mu3Lab's gateway connection, created or given the current key."""
    body: dict[str, Any] = {"label": LABEL, "base_url": GATEWAY, "api_key": key, "catalog_provider": "custom"}
    existing = request(port, "/api/llm/connections")
    current = next((item for item in existing if item.get("base_url") == GATEWAY), None) if existing else None
    if current:
        return int(request(port, f"/api/llm/connections/{current['id']}", method="PUT", data=body)["id"])
    return int(request(port, "/api/llm/connections", method="POST", data=body)["id"])
