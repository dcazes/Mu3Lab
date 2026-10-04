"""Status of the LobeChat surface embedded in the dashboard."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter

from ctl import chat_connections
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import Member, OwnerMutation
from ctl.api.service_view import service_snapshot, service_states
from ctl.identity import mode_for
from ctl.integrations.lobehub import ChatError, LobeHub
from ctl.lobehub_ops import origin, sync_agents
from ctl.mcp_registry import snapshot as mcp_snapshot
from ctl.platform_apps import by_capability

ROOT = Path(__file__).resolve().parents[3]

router = APIRouter(prefix="/api/v1/chat", tags=["chat"], route_class=ContractRoute)


@router.get("/status", response_model=models.ChatStatus, response_model_exclude_none=True)
def chat_status(operator: Member) -> models.ChatStatus:
    view = service_snapshot(operator)
    dns_name = view.tailnet_dns_name or ""
    registry = runtime.registry()
    service = registry.get(by_capability("chat").id)
    state = next((item for item in view.services if item.id == service.id), None)
    ready = bool(state and state.health_state == "healthy" and dns_name and state.route_ready)
    provider = {
        "id": service.id,
        "name": service.name,
        "ready": ready,
        "url": state.url if state else "",
        "authentication": mode_for(service),
        "detail": "LobeChat is ready." if ready else "LobeChat is not ready; check core setup and its private route.",
    }
    mcp = mcp_snapshot(registry, service_states(operator))
    return models.ChatStatus.model_validate(
        {
            "connected": bool(chat_connections.records().get(str(operator.get("subject_id") or ""), {}).get("key")),
            "ok": True,
            "ready": ready,
            "url": provider["url"],
            "authentication": provider["authentication"],
            "providers": [provider],
            "mcp_enabled_count": sum(1 for item in mcp["servers"] if item["enabled"]),
            "detail": provider["detail"],
        }
    )


@router.post("/connect", response_model=models.ChatConnectResponse, response_model_exclude_none=True)
def connect_chat(person: OwnerMutation) -> models.ChatConnectResponse:
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
        return models.ChatConnectResponse.model_validate(
            {
                "ok": True,
                "verification_uri_complete": device["verification_uri_complete"],
                "user_code": device["user_code"],
                "interval": interval,
            }
        )
    except (ChatError, ValueError, OSError) as exc:
        raise ApiError(409, str(exc), code="chat_connect_failed") from None


@router.post("/connect/poll", response_model=models.ChatPollResponse, response_model_exclude_none=True)
def poll_chat(person: OwnerMutation) -> models.ChatPollResponse:
    try:
        with LobeHub(origin()) as client:
            result = chat_connections.poll(str(person["subject_id"]), client)
        if result["state"] == "connected":
            ready, detail = sync_agents(lambda _line: None)
            result.update({"ready": ready, "detail": detail})
        return models.ChatPollResponse.model_validate({"ok": True, **result})
    except (ChatError, ValueError, OSError) as exc:
        raise ApiError(409, str(exc), code="chat_connect_failed") from None
