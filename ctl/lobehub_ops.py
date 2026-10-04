"""Synchronize connected people's assistants through the supported chat API."""

from __future__ import annotations

import json
from typing import Any

from ctl import chat_connections, mcp_gateway
from ctl.control_state import ControlState
from ctl.integrations.lobehub import ChatError, LobeHub
from ctl.manifest.catalog import load
from ctl.mcp_catalog import load as load_connectors
from ctl.platform_apps import by_capability
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.service_state import tailnet_dns_name


def origin() -> str:
    app = by_capability("chat")
    route = app.manifest.route
    host = tailnet_dns_name()
    if not route or not host:
        raise ChatError("Chat's private address is not available yet.")
    return f"https://{host}:{route.https_port}"


def desired_assistants() -> list[dict[str, Any]]:
    state = ControlState.runtime()
    if state is None:
        return []
    live = set()
    for connector in load_connectors(load_registry()):
        runtime = state.mcp_server(connector.id)
        if connector.gateway and runtime and runtime["enabled"] and runtime["state"] == "live":
            live.add(connector.service_id)
    result = []
    for app in load().apps:
        assistant = app.manifest.chat.assistant
        installation = state.installation(app.id)
        if (
            not assistant
            or app.id not in live
            or not installation
            or installation["state"] not in {"running", "stopped", "degraded"}
        ):
            continue
        instructions = assistant.instructions
        policy_file = mcp_gateway.project() / "policy" / "policy.json"
        if policy_file.is_file():
            policy = json.loads(policy_file.read_text())
            instructions = policy.get("apps", {}).get(app.id, {}).get("instructions") or instructions
        result.append(
            {
                "id": app.id,
                "title": assistant.title,
                "description": assistant.description,
                "instructions": instructions,
                "url": mcp_gateway.endpoint(app.id),
                "token": mcp_gateway.app_token(app.id),
            }
        )
    return result


def sync_agents(log) -> tuple[bool, str]:
    paths = RuntimePaths()
    records = chat_connections.records(paths)
    if not any(record.get("key") for record in records.values()):
        return True, "Assistants will be prepared when each person connects chat."
    try:
        desired = desired_assistants()
        state = ControlState.runtime()
        if state is None:
            return True, "Chat synchronization waits for application state."
        installed = {
            app.id
            for app in load().apps
            if (record := state.installation(app.id))
            and record.get("installed_at")
            and record["state"] != "not_installed"
        }
        address = origin()
    except (ChatError, OSError, ValueError) as exc:
        log(str(exc))
        return False, str(exc)
    failures = []
    for uid, record in records.items():
        if not record.get("key"):
            continue
        # One person's revoked key or failed request must not stop everyone else's assistants.
        try:
            with LobeHub(address, record["key"]) as client:
                managed = client.ensure_assistants(desired, record.get("managed", {}), installed=installed)
            chat_connections.save(uid, {**record, "managed": managed}, paths)
        except (ChatError, OSError, ValueError) as exc:
            failures.append(str(exc))
    if failures:
        detail = failures[0] if len(failures) == 1 else f"{len(failures)} people's assistants were not updated."
        log(detail)
        return False, detail
    return True, "Your assistants are ready."


def reconcile(log) -> tuple[bool, str]:
    return sync_agents(log)


def sync_mcp(server, tools: list[dict], token: str, *, enabled: bool, log) -> tuple[bool, str]:
    # Unreviewed connectors are never exposed outside the enforcing gateway.
    return True, "Review this connector before exposing it to chat."


def sync_gateway(service_id: str, app_name: str, tools: list[dict], *, enabled: bool, log) -> tuple[bool, str]:
    return sync_agents(log)


def set_tool_permission(server_id: str, tool_name: str, permission: str) -> tuple[bool, str]:
    # The tool gateway enforces the decision; no chat database permissions exist.
    return True, "Tool permission is enforced by the gateway."
