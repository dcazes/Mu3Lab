"""Keep everyone's app assistants in Open WebUI, through its admin API.

Unlike LobeChat, nobody connects chat by hand: Mu3Lab holds an administrator
key (``OPEN_WEBUI_ADMIN_API_KEY``, registered by the app's owner script) and
keeps one assistant per app with a connector, shared by everyone in the
household, switched off while the app is stopped. Each assistant's tools reach
that app's ContextForge virtual server with the signed-in person's own
Authentik sign-in, so ContextForge knows and logs who made every call.

After every change Mu3Lab asks Open WebUI itself whether each assistant is
set up and, for the ones switched on, whether its tools answer; the
dashboard shows that answer, not what Mu3Lab meant to do.
"""

from __future__ import annotations

from collections.abc import Callable

from ctl import chat_ops, contextforge, contextforge_ops, person_connectors
from ctl.contextforge import ContextForgeError
from ctl.control_state import ControlState
from ctl.integrations.openwebui import ChatError, OpenWebUI
from ctl.manifest.catalog import App, cached
from ctl.mcp_catalog import load as load_connectors
from ctl.people import PeopleError, chat_people
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.store import records

Log = Callable[[str], None]
# Which apps have an assistant, and why the last sync failed.
SCOPE = "openwebui-chat"
RECORD = "household"
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
    """Nothing per person: a deactivated person can no longer sign in to Open WebUI or pass a sign-in on."""


def _sync(log: Log) -> tuple[bool, str]:
    """Bring Open WebUI in step with what is running now; it never needs ContextForge to be up to do that."""
    paths = RuntimePaths()
    gateway, up = chat_ops.gateway_name(), chat_ops.gateway_up()
    try:
        desired = chat_ops.desired_assistants(gateway=up)
        if any(item["active"] for item in desired):
            _ensure_accounts(log)
        with client() as api:
            from ctl import local_models

            chosen, local = local_models.settings(), local_models.chat_model()
            managed = api.ensure(
                desired,
                _guide({str(item["id"]).split("--")[0] for item in desired}),
                outside_choice=chosen["privacy"] == "local_first",
                local_choice=local.name if local and chosen["sources"][0] == "outside" else "",
                gateway_down="" if up else gateway,
            )
            try:
                api.ensure_status_filter(_filter_entries(desired, gateway))
                status_filter = ""
            except ChatError as exc:
                status_filter = f"Open WebUI's per-message app check could not be set up: {exc}"
                log(status_filter)
            checks = api.check(
                desired, _check_token(log) if any(item["active"] for item in desired) else "", gateway=gateway
            )
    except (ChatError, ContextForgeError, PeopleError, OSError, ValueError, KeyError) as exc:
        log(f"Open WebUI assistants were not updated: {exc}")
        records.put(SCOPE, RECORD, {**records.get(SCOPE, RECORD, paths), "error": str(exc)}, paths)
        return False, str(exc)
    records.put(
        SCOPE,
        RECORD,
        {"managed": managed, "checks": checks, "error": "", "gateway_down": not up, "status_filter": status_filter},
        paths,
    )
    names = {item["id"]: item["title"] for item in desired}
    failed = [
        f"{names[app_id]}: {result['detail']}" for app_id, result in checks.items() if result["status"] == "failed"
    ]
    if failed:
        log("Open WebUI's check found assistants that do not work: " + "; ".join(failed))
        return False, failed[0]
    if status_filter:
        return False, status_filter
    if not up:
        return True, f"{gateway} is stopped, so chat tells everyone their app assistants are out of reach."
    return True, "Your assistants are ready."


def _ensure_accounts(log: Log) -> None:
    """A ContextForge account for everyone; a failure here leaves Open WebUI's own update standing."""
    try:
        contextforge_ops.ensure_accounts(sorted({p["email"] for p in chat_people().values() if p["email"]}))
    except ContextForgeError as exc:
        log(f"ContextForge accounts were not checked this time: {exc}")


def _check_token(log: Log) -> str:
    """How Open WebUI signs in to ContextForge to test each assistant's tools ("" fails those tests)."""
    try:
        with contextforge.client() as api:
            return api.access_token()
    except ContextForgeError as exc:
        log(f"Open WebUI's tool check could not sign in to ContextForge: {exc}")
        return ""


def _filter_entries(desired: list[dict], gateway: str) -> dict[str, dict]:
    """For the status filter: each tool connection's name, whose it is, and what to say when it does not connect."""
    from ctl import agent_context
    from ctl.integrations.openwebui import connection_id

    return {
        connection_id(str(item["id"])): agent_context.filter_entry(
            str(item.get("app_name") or item["title"]),
            str(item.get("status") or "ready"),
            gateway=gateway,
            connector_state=str(item.get("connector_state") or ""),
            person=str(item.get("person") or ""),
        )
        for item in desired
    }


def assistant_report(uid: str, *, operator: bool = False) -> list[dict[str, str]]:
    """Why each installed app does or does not have an assistant in Open WebUI."""
    state = ControlState.runtime()
    if state is None:
        return []
    record = records.get(SCOPE, RECORD, RuntimePaths())
    connectors = load_connectors(load_registry())
    report = []
    for app in cached().apps:
        installation = state.app_installation(app.id)
        assistant = app.manifest.chat.assistant
        if not assistant or not installation or installation["state"] == "not_installed":
            continue
        if assistant.audience == "operators" and not operator:
            continue
        own = [connector for connector in connectors if connector.service_id == app.id]
        runtimes = [state.mcp_server(connector.id) for connector in own]
        live = any(runtime and runtime["enabled"] and runtime["state"] == "live" for runtime in runtimes)
        checks = record.get("checks") or {}
        check = checks.get(app.id) or checks.get(f"{app.id}--{person_connectors.tag(uid)}") or {}
        if not own:
            status, detail = "needs_review", "This app has no chat connector yet."
        elif check.get("status") == "failed":
            status, detail = "failed", str(check["detail"])
        elif not live:
            current = next((runtime["state"] for runtime in runtimes if runtime), "not started")
            if current == "stopped" and check.get("status") == "paused":
                status, detail = "paused", "Shown in chat as stopped; its tools come back when the app starts."
            else:
                status, detail = "connector_down", f"Its chat connector is {current.replace('_', ' ')}."
        elif record.get("gateway_down"):
            gateway = chat_ops.gateway_name()
            status, detail = (
                "connector_down",
                f"{gateway} is stopped, so chat can't reach this app. Start {gateway} under Apps.",
            )
        elif check.get("status") == "ready":
            status, detail = "ready", f"Available in chat. {check['detail']}"
        elif check.get("status") == "pending":
            status, detail = "pending", str(check["detail"])
        elif any(connector.per_person for connector in own) and not check:
            status, detail = "pending", _personal_detail(uid, own)
        elif record.get("error"):
            status, detail = "failed", str(record["error"])
        else:
            status, detail = "pending", "Adding to chat and checking it within five minutes."
        report.append({"id": app.id, "name": app.manifest.name, "status": status, "detail": detail})
    return report


def _personal_detail(uid: str, connectors: list) -> str:
    """Why someone does not have their own assistant for a private app yet."""
    for connector in connectors:
        record = records.get(person_connectors.SCOPE, f"{connector.id}:{uid}")
        if record.get("error"):
            return str(record["error"])
    return "Your own copy of this app's tools is being prepared."


def _guide(with_assistant: set[str]) -> str:
    """The main assistant's guide to every installed app."""
    from ctl import agent_context
    from ctl.service_state import tailnet_dns_name

    state = ControlState.runtime()
    if state is None:
        return ""
    installed = [
        app
        for app in cached().apps
        if (record := state.app_installation(app.id))
        and record.get("installed_at")
        and record["state"] != "not_installed"
    ]
    return agent_context.household_guide(installed, with_assistant, tailnet_dns_name() or "")
