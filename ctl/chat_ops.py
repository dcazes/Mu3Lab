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


def desired_assistants(uid: str = "", email: str = "", *, operator: bool = False) -> list[dict[str, Any]]:
    """Everyone's assistants: one per installed app whose connector is switched on and has a ContextForge virtual server.

    Each is ``active`` only while its connector is live; one whose app is stopped stays, switched off.
    Each names its ``audience``: the household, or administrators only.

    With ``uid`` and ``email`` (LobeChat), each carries that person's own ContextForge key, and
    administrators-only assistants are left out unless ``operator``; Open WebUI passes each
    person's Authentik sign-in instead and keeps those assistants to its administrators.

    A private app (``per_person`` connector) has one assistant per person instead, pointing at
    their own virtual server (``ctl.person_connectors``): for LobeChat only that person's, and for
    Open WebUI everyone's, each with ``person`` (whose it is) and an ID of its own.
    """
    from ctl import contextforge, contextforge_ops, person_connectors
    from ctl.mcp_catalog import load as load_connectors
    from ctl.registry import load as load_registry

    state = ControlState.runtime()
    if state is None:
        return []
    # Per app: its switched-on connector, a live one first, and whether that one is live.
    live: dict[str, str] = {}
    active: dict[str, bool] = {}
    connectors = {connector.id: connector for connector in load_connectors(load_registry())}
    for connector in connectors.values():
        runtime = state.mcp_server(connector.id)
        if not runtime or not runtime["enabled"] or not contextforge_ops.virtual_server(connector.id):
            continue
        if not active.get(connector.service_id):
            live[connector.service_id] = connector.id
            active[connector.service_id] = runtime["state"] == "live"
    from ctl import agent_context
    from ctl.mcp_review import load as load_review

    dns_name = tailnet_dns_name() or ""
    # Every assistant names the others, so it can send people to the right one.
    titles = {
        app.id: app.manifest.chat.assistant.title
        for app in cached().apps
        if app.manifest.chat.assistant and app.id in live and app.manifest.chat.assistant.audience == "household"
    }
    result = []
    for app in cached().apps:
        assistant = app.manifest.chat.assistant
        installation = state.app_installation(app.id)
        if (
            not assistant
            or app.id not in live
            or not installation
            or installation["state"] not in {"running", "stopped", "degraded"}
        ):
            continue
        connector_id = live[app.id]
        connector = connectors[connector_id]
        try:
            review = load_review(connector.id, connector.review) if connector.review else None
        except (OSError, ValueError):
            review = None
        instructions = agent_context.app_instructions(
            app,
            per_person=connector.per_person,
            review=review,
            dns_name=dns_name,
            others=[title for other, title in titles.items() if other != app.id],
        )
        item = {
            "id": app.id,
            "title": assistant.title,
            "description": assistant.description,
            "instructions": instructions,
            "active": active[app.id],
            "audience": assistant.audience,
        }
        if uid and assistant.audience == "operators" and not operator:
            continue
        if connector.per_person:
            for person_uid, person_email, server_id, team_id in _personal_servers(app.id, connector_id):
                if uid and person_uid != uid:
                    continue
                own = item | {"url": contextforge.endpoint(server_id), "person": person_email}
                if not uid:
                    own["id"] = f"{app.id}--{person_connectors.tag(person_uid)}"
                if uid and email and own["active"]:
                    own["token"] = contextforge_ops.person_key(uid, email, connector_id, server_id, team_id=team_id)
                result.append(own)
            continue
        server_id = contextforge_ops.virtual_server(connector_id)
        item["url"] = contextforge.endpoint(server_id)
        if uid and email and item["active"]:
            item["token"] = contextforge_ops.person_key(uid, email, connector_id, server_id)
        result.append(item)
    return result


def _personal_servers(app_id: str, connector_id: str) -> list[tuple[str, str, str, str]]:
    """Each person's own server for a private app: (uid, email, server ID, team ID).

    The owner's is the app's own connector; everyone else's is their copy, once it is live.
    """
    from ctl import contextforge_ops, person_connectors
    from ctl.people import PeopleError

    try:
        people = person_connectors.household()
    except PeopleError:
        return []
    owner = person_connectors.owner_email(app_id)
    found = []
    for person in people:
        if person["email"] == owner:
            server_id, team_id = (
                contextforge_ops.virtual_server(connector_id),
                contextforge_ops.server_team(connector_id),
            )
        else:
            record = person_connectors.servers(person["uid"]).get(connector_id) or {}
            server_id, team_id = str(record.get("server_id") or ""), str(record.get("team_id") or "")
        if server_id and team_id:
            found.append((person["uid"], person["email"], server_id, team_id))
    return found


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
    """Take a person's app tools out of every installed chat app, and remove their own copies of private apps' tools."""
    import logging

    from ctl import person_connectors

    for app in chat_apps():
        if installed(app.id):
            backend(app).revoke(subject)
    person_connectors.forget(subject, logging.getLogger("mu3lab.access").info)
