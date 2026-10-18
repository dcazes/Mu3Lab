"""The household chat app and the app assistants inside it.

Two chat apps can be installed (capability ``chat``): Open WebUI, which the
installer sets up and is the default, and LobeChat, an optional app. The
owner picks the household one in Settings > AI; the dashboard's Chat page
shows it. Each installed chat app keeps its own assistants in step, through
its backend: ``ctl.openwebui_ops`` or ``ctl.lobehub_ops``.
"""

from __future__ import annotations

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


def desired_assistants(uid: str = "", email: str = "") -> list[dict[str, Any]]:
    """Everyone's assistants: one per installed app whose connector is switched on and has a ContextForge virtual server.

    Each is ``active`` only while its connector is live; one whose app is stopped stays, switched off.

    With ``uid`` and ``email`` (LobeChat), each carries that person's own ContextForge key;
    Open WebUI passes each person's Authentik sign-in instead.
    """
    from ctl import contextforge, contextforge_ops
    from ctl.mcp_catalog import load as load_connectors
    from ctl.registry import load as load_registry

    state = ControlState.runtime()
    if state is None:
        return []
    # Per app: its switched-on connector, a live one first, and whether that one is live.
    live: dict[str, str] = {}
    active: dict[str, bool] = {}
    for connector in load_connectors(load_registry()):
        runtime = state.mcp_server(connector.id)
        if not runtime or not runtime["enabled"] or not contextforge_ops.virtual_server(connector.id):
            continue
        if not active.get(connector.service_id):
            live[connector.service_id] = connector.id
            active[connector.service_id] = runtime["state"] == "live"
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
        server_id = contextforge_ops.virtual_server(live[app.id])
        item = {
            "id": app.id,
            "title": assistant.title,
            "description": assistant.description,
            "instructions": assistant.instructions,
            "url": contextforge.endpoint(server_id),
            "active": active[app.id],
        }
        if uid and email and item["active"]:
            item["token"] = contextforge_ops.person_key(uid, email, live[app.id], server_id)
        result.append(item)
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
