"""Provision connector credentials through app-owned manifest scripts."""

from __future__ import annotations

import os

from ctl import mcp_config
from ctl.engine.compose import Compose
from ctl.manifest.catalog import cached
from ctl.mcp_registry import credential_path, missing_credentials
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl.store import onboarding as onboarding_state
from ctl.store.secrets import SecretStore


def _owner_args(app, provision) -> list[str] | None:
    """The owner's username or email when the script asks for it; ``None`` when it is not known yet."""
    if not (provision.owner_username or provision.owner_email):
        return []
    field = "username" if provision.owner_username else "email"
    owner = onboarding_state.read(app.id).get("owner") or {}
    return [str(owner[field])] if owner.get(field) else None


def ensure(server, log) -> tuple[bool, str]:
    if not missing_credentials(server):
        return True, "MCP credentials are already registered."
    app, connector = cached().connector(server.id)
    provision = connector.provision
    if provision is None:
        return False, connector.provision_note or "This application requires manual MCP credentials."
    args = _owner_args(app, provision)
    if args is None:
        return False, f"{app.manifest.name}'s owner is not known yet; reinstall the app to record it."
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


def revoke(server, log) -> tuple[bool, str]:
    """Delete a credential minted for Mu3Lab alone, then forget Mu3Lab's copy so it is minted again."""
    app, connector = cached().connector(server.id)
    provision = connector.provision
    if provision is None or not provision.revoke_script:
        return True, ""
    if missing_credentials(server):
        return True, ""
    args = _owner_args(app, provision)
    if args is None:
        return False, f"{app.manifest.name}'s owner is not known yet."
    try:
        script = (app.connector_folder(connector.id) / provision.revoke_script).read_text()
        result = Compose(RuntimePaths().projects / app.id).run_script(
            provision.service, provision.interpreter, script, log, args=args, timeout=90
        )
    except (OSError, ValueError, TypeError) as exc:
        return False, str(exc)
    if not result.ok:
        return False, result.error or "The application could not delete its chat credential."
    store = SecretStore()
    target = credential_path(server.id)
    values = store.get("connector", server.id) or read_runtime_env(target)
    for field in server.credentials:
        values.pop(str(field["env"]), None)
    store.put("connector", server.id, values)
    if target.is_file():
        target.write_text(runtime_env_text(values), encoding="utf-8")
        os.chmod(target, 0o600)
    return True, f"{server.name}'s chat key was deleted in {app.manifest.name}."
