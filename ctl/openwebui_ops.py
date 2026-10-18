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

from ctl import chat_ops, contextforge
from ctl.contextforge import ContextForgeError
from ctl.control_state import ControlState
from ctl.integrations.openwebui import ChatError, OpenWebUI
from ctl.manifest.catalog import App, cached
from ctl.mcp_catalog import load as load_connectors
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
    paths = RuntimePaths()
    try:
        desired = chat_ops.desired_assistants()
        with client() as api:
            managed = api.ensure(desired)
            checks = api.check(desired, _check_token() if any(item["active"] for item in desired) else "")
    except (ChatError, ContextForgeError, OSError, ValueError, KeyError) as exc:
        log(f"Open WebUI assistants were not updated: {exc}")
        records.put(SCOPE, RECORD, {**records.get(SCOPE, RECORD, paths), "error": str(exc)}, paths)
        return False, str(exc)
    records.put(SCOPE, RECORD, {"managed": managed, "checks": checks, "error": ""}, paths)
    names = {item["id"]: item["title"] for item in desired}
    failed = [
        f"{names[app_id]}: {result['detail']}" for app_id, result in checks.items() if result["status"] == "failed"
    ]
    if failed:
        log("Open WebUI's check found assistants that do not work: " + "; ".join(failed))
        return False, failed[0]
    return True, "Your assistants are ready."


def _check_token() -> str:
    with contextforge.client() as api:
        return api.access_token()


def assistant_report(uid: str, *, operator: bool = False) -> list[dict[str, str]]:
    """Why each installed app does or does not have an assistant in Open WebUI."""
    state = ControlState.runtime()
    if state is None:
        return []
    record = records.get(SCOPE, RECORD, RuntimePaths())
    connectors = load_connectors(load_registry())
    report = []
    for app in cached().apps:
        installation = state.installation(app.id)
        if not app.manifest.chat.assistant or not installation or installation["state"] == "not_installed":
            continue
        own = [connector for connector in connectors if connector.service_id == app.id]
        runtimes = [state.mcp_server(connector.id) for connector in own]
        live = any(runtime and runtime["enabled"] and runtime["state"] == "live" for runtime in runtimes)
        check = (record.get("checks") or {}).get(app.id) or {}
        if not own:
            status, detail = "needs_review", "This app has no chat connector yet."
        elif check.get("status") == "failed":
            status, detail = "failed", str(check["detail"])
        elif not live:
            current = next((runtime["state"] for runtime in runtimes if runtime), "not started")
            if current == "stopped" and check.get("status") == "paused":
                status, detail = "paused", "Switched off while the app is stopped; it comes back when the app starts."
            else:
                status, detail = "connector_down", f"Its chat connector is {current.replace('_', ' ')}."
        elif check.get("status") == "ready":
            status, detail = "ready", f"Available in chat. {check['detail']}"
        elif record.get("error"):
            status, detail = "failed", str(record["error"])
        else:
            status, detail = "pending", "Adding to chat and checking it within five minutes."
        report.append({"id": app.id, "name": app.manifest.name, "status": status, "detail": detail})
    return report
