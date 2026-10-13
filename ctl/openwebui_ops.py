"""Keep each operator's app assistants in Open WebUI, through its admin API.

Unlike LobeChat, nobody connects chat by hand: Mu3Lab holds an administrator
key (``OPEN_WEBUI_ADMIN_API_KEY``, registered by the app's owner script) and
gives every operator who has signed in to Open WebUI one assistant per app
whose reviewed connector is live. Each assistant's tools reach the tool
gateway with that person's own token, so every change an assistant asks for
is still approved in Mu3Lab. Household members get no app tools.
"""

from __future__ import annotations

from collections.abc import Callable

from ctl import chat_ops, mcp_gateway
from ctl.control_state import ControlState
from ctl.integrations.openwebui import ChatError, OpenWebUI
from ctl.manifest.catalog import App, load
from ctl.mcp_catalog import load as load_connectors
from ctl.people import PeopleError, chat_people
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.store import records

Log = Callable[[str], None]
# Per person (Authentik ID): which apps have an assistant, and why the last sync failed.
SCOPE = "openwebui-chat"
KEY_ENV = "OPEN_WEBUI_ADMIN_API_KEY"


def _app() -> App:
    return chat_ops.by_workspace("open_webui")


def client(paths: RuntimePaths | None = None) -> OpenWebUI:
    """Open WebUI on this server's loopback, as Mu3Lab's administrator key."""
    app = _app()
    key = read_runtime_env((paths or RuntimePaths()).projects / app.id / ".env").get(KEY_ENV, "")
    if not key:
        raise ChatError("Open WebUI's administrator key is missing; retry its setup.")
    return OpenWebUI(f"http://127.0.0.1:{app.manifest.service.local_port}", "sk-" + key)


def sync_agents(log: Log) -> tuple[bool, str]:
    from ctl import access, resource_locks

    with resource_locks.hold(access.CHAT_LOCK):
        return _sync(log)


def revoke(subject: str) -> None:
    """A person lost administration: their assistants and tool connections go (chats stay)."""
    from ctl import access, resource_locks

    # People without administration lose their assistants on every sync; run one now.
    with resource_locks.hold(access.CHAT_LOCK):
        _sync(lambda _line: None)


def _sync(log: Log) -> tuple[bool, str]:
    try:
        people = chat_people()
        with client() as api:
            accounts = {str(user.get("email") or "").strip().lower(): str(user["id"]) for user in api.users()}
            desired: dict[str, list[dict]] = {}
            owners: dict[str, str] = {}
            for uid, person in people.items():
                user_id = accounts.get(person["email"])
                mcp_gateway.grant(uid, operator=person["operator"])
                if not user_id or not person["operator"]:
                    continue
                desired[user_id] = chat_ops.desired_assistants(uid)
                owners[user_id] = uid
            managed = api.ensure(desired)
    except (ChatError, PeopleError, OSError, ValueError, KeyError) as exc:
        log(f"Open WebUI assistants were not updated: {exc}")
        return False, str(exc)
    paths = RuntimePaths()
    for user_id, uid in owners.items():
        records.put(SCOPE, uid, {"managed": managed.get(user_id, []), "error": ""}, paths)
    for uid, person in people.items():
        if uid not in owners.values():
            records.put(SCOPE, uid, {"managed": [], "error": "", "account": person["email"] in accounts}, paths)
    return True, "Your assistants are ready."


def assistant_report(uid: str, *, operator: bool = False) -> list[dict[str, str]]:
    """Why each installed app does or does not have an assistant in this person's Open WebUI."""
    state = ControlState.runtime()
    if state is None:
        return []
    record = records.get(SCOPE, uid, RuntimePaths())
    connectors = load_connectors(load_registry())
    report = []
    for app in load().apps:
        installation = state.installation(app.id)
        if not app.manifest.chat.assistant or not installation or installation["state"] == "not_installed":
            continue
        reviewed = [connector for connector in connectors if connector.service_id == app.id and connector.gateway]
        runtimes = [state.mcp_server(connector.id) for connector in reviewed]
        live = any(runtime and runtime["enabled"] and runtime["state"] == "live" for runtime in runtimes)
        if not operator:
            status, detail = (
                "operator_only",
                "This connector uses shared service credentials and is available only to operators.",
            )
        elif not reviewed:
            status, detail = "needs_review", "Its connector's tools have not been reviewed for chat yet."
        elif not live:
            current = next((runtime["state"] for runtime in runtimes if runtime), "not started")
            status, detail = "connector_down", f"Its chat connector is {current.replace('_', ' ')}."
        elif app.id in (record.get("managed") or []):
            status, detail = "ready", "Available in chat."
        elif record.get("error"):
            status, detail = "failed", str(record["error"])
        elif record and not record.get("account", True):
            status, detail = "not_connected", "Open chat once; your assistants appear within five minutes."
        else:
            status, detail = "pending", "Adding to chat within five minutes."
        report.append({"id": app.id, "name": app.manifest.name, "status": status, "detail": detail})
    return report
