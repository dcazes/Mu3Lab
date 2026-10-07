"""Provision connector credentials through app-owned manifest scripts."""

from __future__ import annotations

from ctl import mcp_config
from ctl.engine.compose import Compose
from ctl.manifest.catalog import load
from ctl.mcp_registry import missing_credentials
from ctl.runtime import RuntimePaths
from ctl.store import onboarding as onboarding_state


def ensure(server, log) -> tuple[bool, str]:
    if not missing_credentials(server):
        return True, "MCP credentials are already registered."
    app, connector = load().connector(server.id)
    provision = connector.provision
    if provision is None:
        return False, connector.provision_note or "This application requires manual MCP credentials."
    args: list[str] = []
    if provision.owner_username:
        owner = onboarding_state.read(app.id).get("owner") or {}
        if not owner.get("username"):
            return False, f"{app.manifest.name}'s owner is not known yet; reinstall the app to record it."
        args = [str(owner["username"])]
    try:
        script = (app.connector_folder(connector.id) / provision.script).read_text()
        result = Compose(RuntimePaths().projects / app.id).run_script(
            provision.service, provision.interpreter, script, log, args=args, timeout=90
        )
        if not result.ok:
            return False, result.error or "The application could not register its chat credential."
        mcp_config.write(server, result.outputs)
    except (OSError, ValueError, TypeError) as exc:
        return False, str(exc)
    return True, f"Created and securely registered a dedicated {server.name} credential."
