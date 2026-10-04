"""Status of the LobeChat surface embedded in the dashboard."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ctl import chat_connections
from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import Member, OwnerMutation
from ctl.api.service_view import service_states
from ctl.identity import mode_for
from ctl.integrations.lobehub import ChatError, LobeHub
from ctl.lobehub_ops import origin, sync_agents
from ctl.mcp_registry import snapshot as mcp_snapshot
from ctl.platform_apps import by_capability
from ctl.registry import RegistryError
from ctl.service_state import status as service_status
from ctl.service_state import tailnet_dns_name, tailnet_serve_ports

ROOT = Path(__file__).resolve().parents[3]

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.get("/status")
def chat_status(operator: Member) -> dict[str, Any]:
    dns_name = tailnet_dns_name()
    registry = runtime.registry()
    try:
        service = registry.get(by_capability("chat").id)
    except RegistryError as exc:
        raise ApiError(503, str(exc)) from exc
    state = service_status(service, dns_name, ROOT, tailnet_serve_ports())
    ready = state["health_state"] == "healthy" and bool(dns_name) and bool(state.get("route_ready"))
    provider = {
        "id": service.id,
        "name": service.name,
        "ready": ready,
        "url": str(state.get("url") or ""),
        "authentication": mode_for(service),
        "detail": "LobeChat is ready." if ready else "LobeChat is not ready; check core setup and its private route.",
    }
    mcp = mcp_snapshot(registry, service_states(operator))
    return {
        "connected": bool(chat_connections.records().get(str(operator.get("subject_id") or ""), {}).get("key")),
        "ok": True,
        "ready": ready,
        "url": provider["url"],
        "authentication": provider["authentication"],
        "providers": [provider],
        "mcp_enabled_count": sum(1 for item in mcp["servers"] if item["enabled"]),
        "detail": provider["detail"],
    }


@router.post("/connect")
def connect_chat(person: OwnerMutation) -> dict[str, Any]:
    uid = str(person["subject_id"])
    try:
        with LobeHub(origin()) as client:
            device = client.start_device_login()
        interval = max(5, int(device.get("interval", 5)))
        chat_connections.begin(
            uid,
            {
                "device_code": device["device_code"],
                "interval": interval,
                "expires_at": time.time() + int(device["expires_in"]),
                "next_poll": time.time() + interval,
            },
        )
        return {
            "ok": True,
            "verification_uri_complete": device["verification_uri_complete"],
            "user_code": device["user_code"],
            "interval": interval,
        }
    except (ChatError, ValueError, OSError) as exc:
        raise ApiError(409, str(exc), code="chat_connect_failed") from None


@router.post("/connect/poll")
def poll_chat(person: OwnerMutation) -> dict[str, Any]:
    try:
        with LobeHub(origin()) as client:
            result = chat_connections.poll(str(person["subject_id"]), client)
        if result["state"] == "connected":
            ready, detail = sync_agents(lambda _line: None)
            result.update({"ready": ready, "detail": detail})
        return {"ok": True, **result}
    except (ChatError, ValueError, OSError) as exc:
        raise ApiError(409, str(exc), code="chat_connect_failed") from None
