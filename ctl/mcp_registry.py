"""Browser-safe projection of reviewed application-data MCP integrations."""

from __future__ import annotations

from typing import Any

from ctl.control_state import ControlState
from ctl.mcp_activity import McpActivity
from ctl.mcp_catalog import load as load_catalog
from ctl.registry import Registry
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env


def credential_path(server_id: str):
    return RuntimePaths().projects / f"mcp-{server_id}" / ".env"


def missing_credentials(server) -> list[str]:
    values = read_runtime_env(credential_path(server.id))
    return [str(field["key"]) for field in server.credentials
            if field.get("required") and not values.get(str(field["env"]))]


def snapshot(registry: Registry, service_states: dict[str, str]) -> dict[str, Any]:
    services = {service.id: service for service in registry.services}
    persisted = ControlState.runtime()
    activity = McpActivity()
    servers: list[dict[str, Any]] = []
    for server in load_catalog(registry):
        service = services[server.service_id]
        installation = persisted.installation(service.id) if persisted else None
        # MCP is an enhancement for an application the operator has actually
        # installed, never an alternate application installer.
        if not installation or not installation.get("installed_at"):
            continue
        app_state = service_states.get(service.id, "unknown")
        runtime = persisted.mcp_server(server.id) if persisted else None
        enabled = bool(runtime and runtime["enabled"])
        missing = missing_credentials(server)
        if server.status != "accepted" or not server.compose_dir:
            state = "unavailable"
            error = server.review_note or ("No public MCP is currently available for this app." if server.status == "not_available"
                                           else "Candidate requires a completed security/runtime review.")
        elif app_state == "stopped":
            state, error = "stopped", "Start the application before connecting or verifying its MCP integration."
        elif service.is_blocked or app_state in {"blocked", "planned", "not_installed", "config_required"}:
            state, error = "unavailable", service.blocked_reason or "Install the application first."
        elif missing:
            state, error = "authentication_required", "Configure: " + ", ".join(missing)
        elif runtime:
            state, error = str(runtime["state"]), (runtime.get("last_error") or {}).get("message")
        else:
            state, error = "disabled", None
        manifest = service.mcp
        tools = runtime.get("tool_snapshot", []) if runtime and runtime.get("tool_snapshot") else [
            {"id": str(tool.get("id", "")), "title": str(tool.get("title", "")),
             "risk": str(tool.get("risk", "read")), "enabled": enabled}
            for tool in manifest.get("tools", []) if isinstance(tool, dict)
        ]
        tools = [tool | {"permission": activity.permission(server.id, str(tool.get("id", "")),
                                                       str(tool.get("risk", "write")))} for tool in tools]
        servers.append({
            "id": server.id, "name": server.name, "service_id": server.service_id,
            "kind": server.provenance, "transport": server.transport,
            "endpoint": server.endpoint, "app_state": app_state,
            "enabled": enabled, "state": state, "error": error,
            # A server that still lacks an app credential needs an operator.
            # Once configured, Mu3Lab owns preparation, registration, health
            # checks, and lifecycle reconciliation without further setup.
            "setup_mode": "manual" if missing else "automatic",
            "setup_detail": (
                "Add the application credential below; Mu3Lab will handle the rest."
                if missing else
                "Mu3Lab manages this connection and keeps it aligned with the application."
            ),
            "prepared": bool(runtime and runtime["state"] in {"prepared", "stopped", "live"}),
            "review": {"status": server.status, "repository": server.repository,
                       "revision": server.revision, "preferred": server.preferred,
                       "note": server.review_note},
            "last_verified_at": str((runtime or {}).get("last_verified_at", "")),
            "auth": {"type": "service-credential" if server.credentials else "none",
                     "scopes": list(manifest.get("scopes", [])), "configured": not missing},
            "configuration": [{key: value for key, value in field.items() if key != "env"} | {
                "secret_present": bool(read_runtime_env(credential_path(server.id)).get(str(field["env"])))
                    if field["type"] == "secret" else False,
                "value": None if field["type"] == "secret" else
                    read_runtime_env(credential_path(server.id)).get(str(field["env"]), "")
            } for field in server.credentials],
            "tools": tools,
        })
    summary_states = ("live", "degraded", "authentication_required", "disabled", "unavailable",
                      "starting", "incompatible", "failed", "stopped")
    summary = {state: sum(item["state"] == state for item in servers) for state in summary_states}
    return {"ok": True, "servers": servers, "summary": summary,
            "policy": "Application data only; infrastructure lifecycle and Vaultwarden are excluded."}
