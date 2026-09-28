"""Status of the LobeChat surface embedded in the dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import Operator
from ctl.api.service_view import service_states
from ctl.identity import mode_for
from ctl.mcp_registry import snapshot as mcp_snapshot
from ctl.registry import RegistryError
from ctl.service_state import status as service_status
from ctl.service_state import tailnet_dns_name, tailnet_serve_ports

ROOT = Path(__file__).resolve().parents[3]

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.get("/status")
def chat_status(operator: Operator) -> dict[str, Any]:
    dns_name = tailnet_dns_name()
    registry = runtime.registry()
    try:
        service = registry.get("lobehub")
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
        "ok": True,
        "ready": ready,
        "url": provider["url"],
        "authentication": provider["authentication"],
        "providers": [provider],
        "mcp_enabled_count": sum(1 for item in mcp["servers"] if item["enabled"]),
        "detail": provider["detail"],
    }
