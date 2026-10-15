"""The household chat app embedded in the dashboard, and the owner's choice of it."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter

from ctl import chat_connections, chat_ops
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import Member, OperatorMutation, OwnerMutation
from ctl.api.service_view import service_snapshot, service_states
from ctl.identity import launch_path, mode_for
from ctl.integrations.lobehub import ChatError, LobeHub
from ctl.lobehub_ops import origin
from ctl.mcp_registry import snapshot as mcp_snapshot

ROOT = Path(__file__).resolve().parents[3]

router = APIRouter(prefix="/api/v1/chat", tags=["chat"], route_class=ContractRoute)


@router.get("/status", response_model=models.ChatStatus, response_model_exclude_none=True)
def chat_status(operator: Member) -> models.ChatStatus:
    view = service_snapshot(operator)
    dns_name = view.tailnet_dns_name or ""
    registry = runtime.registry()
    app = chat_ops.selected()
    service = registry.get(app.id)
    state = next((item for item in view.services if item.id == service.id), None)
    ready = bool(state and state.health_state == "healthy" and dns_name and state.route_ready)
    # The launcher reuses the dashboard's Authentik session instead of showing the app's own sign-in button.
    url = state.url.rstrip("/") + launch_path(service.manifest) if state and state.url else ""
    provider = {
        "id": service.id,
        "name": service.name,
        "ready": ready,
        "url": url,
        "authentication": mode_for(service),
        "detail": f"{service.name} is ready."
        if ready
        else f"{service.name} is not ready; check its page in Apps and its private route.",
    }
    mcp = mcp_snapshot(registry, service_states(operator))
    uid = str(operator.get("subject_id") or "")
    return models.ChatStatus.model_validate(
        {
            "assistants": chat_ops.assistant_report(uid, operator=operator["is_admin"]),
            # Only LobeChat asks each person to connect once; Mu3Lab sets up Open WebUI itself.
            "connected": not chat_ops.needs_connect(app) or bool(chat_connections.records().get(uid, {}).get("key")),
            "app_id": app.id,
            "app_name": app.manifest.name,
            "ok": True,
            "ready": ready,
            "url": provider["url"],
            "authentication": provider["authentication"],
            "providers": [provider],
            "mcp_enabled_count": sum(1 for item in mcp["servers"] if item["enabled"]) if operator["is_admin"] else 0,
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
            ready, detail = chat_ops.sync_agents(lambda _line: None)
            result.update({"ready": ready, "detail": detail})
        return models.ChatPollResponse.model_validate({"ok": True, **result})
    except (ChatError, ValueError, OSError) as exc:
        raise ApiError(409, str(exc), code="chat_connect_failed") from None


@router.get("/apps", response_model=models.ChatApps)
def chat_apps(_member: Member) -> models.ChatApps:
    """The chat apps Mu3Lab can show on the Chat page, and which one it shows."""
    chosen = chat_ops.selected().id
    return models.ChatApps.model_validate(
        {
            "ok": True,
            "apps": [
                {
                    "id": app.id,
                    "name": app.manifest.name,
                    "installed": chat_ops.installed(app.id),
                    "selected": app.id == chosen,
                }
                for app in chat_ops.chat_apps()
            ],
        }
    )


@router.put("/apps", response_model=models.ChatApps)
def choose_chat_app(payload: models.ChatAppRequest, operator: OperatorMutation) -> models.ChatApps:
    """Make an installed chat app the household one."""
    if not operator.get("is_admin"):
        raise ApiError(403, "Only a Mu3Lab administrator can do this.", code="admin_required")
    try:
        app = chat_ops.choose(payload.app_id, str(operator["username"]))
    except chat_ops.ChatError as exc:
        raise ApiError(409, str(exc), code="chat_app_unavailable") from None
    runtime.job_store().record_audit(
        actor=str(operator["username"]), event="chat.app_chosen", detail=f"Chat now shows {app.manifest.name}."
    )
    return chat_apps(operator)
