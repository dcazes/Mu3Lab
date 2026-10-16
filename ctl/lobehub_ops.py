"""Synchronize connected people's assistants through the supported chat API."""

from __future__ import annotations

from ctl import chat_connections, chat_ops
from ctl.contextforge import ContextForgeError
from ctl.control_state import ControlState
from ctl.integrations.lobehub import AssistantSyncError, ChatError, LobeHub
from ctl.manifest.catalog import cached
from ctl.mcp_catalog import load as load_connectors
from ctl.people import PeopleError, chat_people
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths


def origin() -> str:
    try:
        return chat_ops.origin(chat_ops.by_workspace("lobe"))
    except chat_ops.ChatError as exc:
        raise ChatError(str(exc)) from None


def assistant_report(uid: str, *, operator: bool = False) -> list[dict[str, str]]:
    """Why each installed app does or does not have an assistant in this person's chat."""
    state = ControlState.runtime()
    if state is None:
        return []
    record = chat_connections.records().get(uid, {})
    connectors = load_connectors(load_registry())
    report = []
    for app in cached().apps:
        installation = state.installation(app.id)
        if not app.manifest.chat.assistant or not installation or installation["state"] == "not_installed":
            continue
        own = [connector for connector in connectors if connector.service_id == app.id]
        runtimes = [state.mcp_server(connector.id) for connector in own]
        live = any(runtime and runtime["enabled"] and runtime["state"] == "live" for runtime in runtimes)
        if not own:
            status, detail = "needs_review", "This app has no chat connector yet."
        elif not live:
            current = next((runtime["state"] for runtime in runtimes if runtime), "not started")
            status, detail = "connector_down", f"Its chat connector is {current.replace('_', ' ')}."
        elif not record.get("key"):
            status, detail = "not_connected", "Waiting for your one-time approval on the Chat page."
        elif app.id in record.get("managed", {}):
            status, detail = "ready", "Available in chat."
        elif record.get("error"):
            status, detail = "failed", record["error"]
        else:
            status, detail = "pending", "Adding to chat within five minutes."
        report.append({"id": app.id, "name": app.manifest.name, "status": status, "detail": detail})
    return report


def sync_agents(log) -> tuple[bool, str]:
    from ctl import access, resource_locks

    # Revocation rewrites the same per-person records; never interleave with it.
    with resource_locks.hold(access.CHAT_LOCK):
        return _sync_agents(log)


def _sync_agents(log) -> tuple[bool, str]:
    paths = RuntimePaths()
    records = chat_connections.records(paths)
    if not any(record.get("key") for record in records.values()):
        return True, "Assistants will be prepared when each person connects chat."
    try:
        people = chat_people()
        state = ControlState.runtime()
        if state is None:
            return True, "Chat synchronization waits for application state."
        installed = {
            app.id
            for app in cached().apps
            if (record := state.installation(app.id))
            and record.get("installed_at")
            and record["state"] != "not_installed"
        }
        address = origin()
    except (ChatError, PeopleError, OSError, ValueError) as exc:
        log(str(exc))
        return False, str(exc)
    failures = []
    for uid, record in records.items():
        if not record.get("key"):
            continue
        # One person's revoked key or failed request must not stop everyone else's assistants.
        try:
            person = people.get(uid)
            desired = chat_ops.desired_assistants(uid, person["email"]) if person and person["email"] else []
            with LobeHub(address, record["key"]) as client:
                managed = client.ensure_assistants(desired, record.get("managed", {}), installed=installed)
            chat_connections.save(uid, {**record, "managed": managed, "error": ""}, paths)
        except AssistantSyncError as exc:
            chat_connections.save(uid, {**record, "managed": exc.managed, "error": str(exc)}, paths)
            failures.append(str(exc))
        except (ChatError, ContextForgeError, OSError, ValueError) as exc:
            # Keep the reason with the person so the dashboard can show it, not just the worker log.
            chat_connections.save(uid, {**record, "error": str(exc)}, paths)
            failures.append(str(exc))
    if failures:
        detail = failures[0] if len(failures) == 1 else f"{len(failures)} people's assistants were not updated."
        log(detail)
        return False, detail
    return True, "Your assistants are ready."


def revoke(subject: str) -> None:
    """Detach every Mu3Lab assistant's tools from this person's LobeChat; conversations and agents stay."""
    from ctl import access, resource_locks

    # Chat synchronization rewrites the same records; never interleave with it.
    with resource_locks.hold(access.CHAT_LOCK):
        entry = chat_connections.records().get(subject) or {}
        managed = entry.get("managed") or {}
        if not entry.get("key") or not managed:
            return
        with LobeHub(origin(), entry["key"]) as client:
            managed = client.ensure_assistants([], managed, installed=set(managed))
        chat_connections.save(subject, {**entry, "managed": managed, "error": ""})
