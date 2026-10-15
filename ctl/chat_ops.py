"""The household chat app and the app assistants inside it.

Two chat apps can be installed (capability ``chat``): Open WebUI, which the
installer sets up and is the default, and LobeChat, an optional app. The
owner picks the household one in Settings > AI; the dashboard's Chat page
shows it. Each installed chat app keeps its own assistants in step, through
its backend: ``ctl.openwebui_ops`` or ``ctl.lobehub_ops``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import ModuleType
from typing import Any

from ctl.control_state import ControlState
from ctl.manifest.catalog import App, Catalog, cached
from ctl.service_state import tailnet_dns_name

CAPABILITY = "chat"
Log = Callable[[str], None]


class ChatError(RuntimeError):
    """A safe message for the dashboard or worker."""


def chat_apps(catalog: Catalog | None = None) -> list[App]:
    return [app for app in (catalog or cached()).apps if CAPABILITY in app.manifest.capabilities]


def by_workspace(workspace: str, catalog: Catalog | None = None) -> App:
    return next(app for app in chat_apps(catalog) if app.manifest.chat.workspace == workspace)


def default_app(catalog: Catalog | None = None) -> App:
    """The chat app the installer sets up (the core one)."""
    return next(app for app in chat_apps(catalog) if app.manifest.tier == "core")


def installed(app_id: str, state: ControlState | None = None) -> bool:
    state = state or ControlState.runtime()
    record = state.installation(app_id) if state else None
    return bool(record and record["state"] != "not_installed")


def selected(catalog: Catalog | None = None) -> App:
    """The owner's choice while it is installed, else the default."""
    state = ControlState.runtime()
    chosen = state.chat_app() if state else ""
    for app in chat_apps(catalog):
        if app.id == chosen and installed(app.id, state):
            return app
    return default_app(catalog)


def choose(app_id: str, actor: str) -> App:
    app = next((item for item in chat_apps() if item.id == app_id), None)
    if app is None:
        raise ChatError("That is not a chat app.")
    if not installed(app.id):
        raise ChatError(f"Install {app.manifest.name} from Apps first.")
    state = ControlState.runtime()
    if state is None:
        raise ChatError("Mu3Lab's settings are not available yet.")
    state.set_chat_app(app.id, actor)
    return app


def origin(app: App) -> str:
    route = app.manifest.route
    host = tailnet_dns_name()
    if not route or not host:
        raise ChatError(f"{app.manifest.name}'s private address is not available yet.")
    return f"https://{host}:{route.https_port}"


def backend(app: App) -> ModuleType:
    if app.manifest.chat.workspace == "lobe":
        from ctl import lobehub_ops

        return lobehub_ops
    from ctl import openwebui_ops

    return openwebui_ops


def needs_connect(app: App) -> bool:
    """LobeChat asks each person for a one-time approval; Mu3Lab manages Open WebUI itself."""
    return app.manifest.chat.workspace == "lobe"


def desired_assistants(subject: str) -> list[dict[str, Any]]:
    """The assistants an operator should have: one per installed app whose reviewed connector is live."""
    from ctl import mcp_gateway
    from ctl.mcp_catalog import load as load_connectors
    from ctl.registry import load as load_registry

    state = ControlState.runtime()
    if state is None:
        return []
    live = set()
    for connector in load_connectors(load_registry()):
        runtime = state.mcp_server(connector.id)
        if connector.gateway and runtime and runtime["enabled"] and runtime["state"] == "live":
            live.add(connector.service_id)
    result = []
    for app in cached().apps:
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
                "token": mcp_gateway.operator_token(subject, app.id),
            }
        )
    return result


def sync_agents(log: Log) -> tuple[bool, str]:
    """Keep every installed chat app's assistants in step; the household one decides the answer."""
    household = selected()
    result = (True, "Assistants are prepared when chat is installed.")
    for app in chat_apps():
        if not installed(app.id):
            continue
        ok, detail = backend(app).sync_agents(log)
        if app.id == household.id:
            result = (ok, detail)
    return result


def assistant_report(uid: str, *, operator: bool = False) -> list[dict[str, str]]:
    return backend(selected()).assistant_report(uid, operator=operator)


def revoke(subject: str) -> None:
    """Take a person's app tools out of every installed chat app."""
    for app in chat_apps():
        if installed(app.id):
            backend(app).revoke(subject)


# The connector hooks mcp_ops calls; the gateway enforces tool switches, so only assistants change.
def sync_gateway(service_id: str, app_name: str, tools: list[dict[str, Any]], *, enabled: bool, log: Log):
    return sync_agents(log)


def sync_mcp(server: Any, tools: list[dict[str, Any]], token: str, *, enabled: bool, log: Log):
    # Unreviewed connectors are never exposed outside the enforcing gateway.
    return True, "Review this connector before exposing it to chat."


def set_tool_permission(server_id: str, tool_name: str, permission: str) -> tuple[bool, str]:
    return True, "Tool permission is enforced by the gateway."
