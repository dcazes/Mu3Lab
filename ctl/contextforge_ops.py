"""Keep ContextForge in step with Mu3Lab's connectors and people.

A connector that goes live is registered (or switched back on) and gets its
virtual server the first time; one that stops is switched off, so its tools
disappear from chat until it is back. Virtual servers and their tools are
then the operators' to change in ContextForge's screens.
"""

from __future__ import annotations

import secrets
import shutil
from collections.abc import Callable

from ctl.contextforge import KEY_PREFIX, ContextForgeError, client, safe_name, tool_ids
from ctl.mcp_registry import credential_path
from ctl.mcp_review import load as load_review
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.store import records
from ctl.store.secrets import SecretStore

Log = Callable[[str], None]
# Per connector: its gateway and virtual server IDs in ContextForge.
SCOPE = "tool-gateway"
# Per person and connector: the LobeChat key (secret store).
KEY_SCOPE = "contextforge-key"


def starting_tools(server) -> set[str] | None:
    """The tools a new virtual server starts with: the review's, or all when there is none."""
    if not server.review:
        return None
    review = load_review(server.id, server.review)
    on = {category.id for category in review.categories if category.default_on}
    return {tool.name for tool in review.tools.values() if tool.category in on}


def server_name(app_name: str, server) -> str:
    return safe_name(f"{app_name} - {server.name}")


def connect(server, app_name: str, log: Log) -> tuple[bool, str]:
    """Register a live connector and make sure it has its virtual server."""
    from ctl.service_state import tailnet_dns_name

    token = read_runtime_env(credential_path(server.id)).get("MCP_AUTH_TOKEN", "")
    try:
        wanted = starting_tools(server)
    except (OSError, ValueError) as exc:
        return False, f"{server.name}'s tool review could not be read: {exc}"
    try:
        with client() as api:
            dns_name = tailnet_dns_name()
            if dns_name:
                api.ensure_chat_provider(dns_name)
            gateway = api.ensure_gateway(
                server.id, server.endpoint, token, f"{app_name}: {server.name}", dict(getattr(server, "headers", ()))
            )
            tools = api.tools(str(gateway["id"]))
            if not tools:
                return False, f"ContextForge found no tools on {server.name}."
            virtual = api.ensure_server(
                server_name(app_name, server), f"The tools {app_name}'s assistant can use.", tool_ids(tools, wanted)
            )
            if dns_name:
                api.accept_chat_sign_ins(virtual, dns_name)
    except ContextForgeError as exc:
        log(f"{server.name} was not registered in ContextForge: {exc}")
        return False, str(exc)
    records.put(SCOPE, server.id, {"gateway_id": str(gateway["id"]), "server_id": str(virtual["id"])})
    return True, f"{server.name} is connected through ContextForge."


def disconnect(server, log: Log) -> tuple[bool, str]:
    """Switch a stopped connector off; its virtual server and the operators' choices stay."""
    record = records.get(SCOPE, server.id)
    if not record.get("gateway_id"):
        return True, f"{server.name} was not registered in ContextForge."
    try:
        with client() as api:
            api.set_gateway_active(record["gateway_id"], False)
    except ContextForgeError as exc:
        log(f"{server.name} was not switched off in ContextForge: {exc}")
        return False, str(exc)
    return True, f"{server.name} is switched off in ContextForge."


def virtual_server(connector_id: str) -> str:
    return str(records.get(SCOPE, connector_id).get("server_id") or "")


def ensure_accounts(emails: list[str]) -> None:
    """A ContextForge account for each person, so the sign-in Open WebUI passes on finds them.

    ContextForge does not create accounts for sign-ins reaching its MCP endpoint.
    """
    with client() as api:
        for email in emails:
            api.ensure_user(email, email.split("@")[0], secrets.token_urlsafe(32))


def person_key(uid: str, email: str, connector_id: str, server_id: str, paths: RuntimePaths | None = None) -> str:
    """This person's LobeChat key for one virtual server, created the first time.

    A saved key is reused only while ContextForge still lists it as active, so a key
    an operator revoked in ContextForge's screens is replaced on the next sync.
    """
    store = SecretStore(paths or RuntimePaths())
    name = f"{uid}:{connector_id}"
    saved = store.get(KEY_SCOPE, name)
    label = f"{KEY_PREFIX}{connector_id}"
    with client(paths) as api:
        if isinstance(saved, dict) and saved.get("server_id") == server_id and saved.get("token"):
            try:
                live = {str(key.get("id")) for key in api.keys(email) if key.get("is_active", True)}
            except ContextForgeError:
                # ContextForge is down: keep the saved key rather than fail the whole sync.
                return str(saved["token"])
            if saved.get("key_id") and str(saved["key_id"]) in live:
                return str(saved["token"])
        api.ensure_user(email, email.split("@")[0], secrets.token_urlsafe(32))
        # A key whose text Mu3Lab no longer holds (or for another server) cannot be shown again; replace it.
        for key in api.keys(email):
            if key.get("name") == label:
                api.revoke_key(str(key["id"]))
        key_id, token = api.create_key(email, label, server_id)
    store.put(KEY_SCOPE, name, {"server_id": server_id, "key_id": key_id, "email": email, "token": token})
    return token


def revoke_person(uid: str, email: str, paths: RuntimePaths | None = None) -> None:
    """Withdraw every LobeChat key Mu3Lab made for this person.

    Raises when keys were saved for them but no email is known to find those keys by,
    so the revocation stays pending instead of leaving the keys working.
    """
    store = SecretStore(paths or RuntimePaths())
    names = [f"{uid}:{connector}" for connector in records.names(SCOPE, paths or RuntimePaths())]
    saved = [item for item in (store.get(KEY_SCOPE, name) for name in names) if isinstance(item, dict)]
    emails = {email} if email else set()
    emails |= {str(item["email"]) for item in saved if item.get("email")}
    if saved and not emails:
        raise ContextForgeError("This person's email is unknown, so their ContextForge keys could not be found.")
    if emails:
        with client(paths) as api:
            for address in sorted(emails):
                for key in api.keys(address):
                    api.revoke_key(str(key["id"]))
    for name in names:
        store.delete(KEY_SCOPE, name)


def remove_admin(email: str, paths: RuntimePaths | None = None) -> None:
    """Take ContextForge administrator rights at once; ContextForge would only do it at their next sign-in."""
    with client(paths) as api:
        api.remove_admin(email)


def retire_old_gateway(log: Log, paths: RuntimePaths | None = None) -> None:
    """Remove the tool gateway ContextForge replaced, on computers installed before it."""
    from ctl import actions

    project = (paths or RuntimePaths()).projects / "mcp-gateway"
    if not (project / "docker-compose.yml").is_file():
        return
    rc, output = actions.compose_down(project, log)
    if rc == 0:
        shutil.rmtree(project, ignore_errors=True)
        log("The old tool gateway was removed; ContextForge serves app tools now.")
    else:
        log(f"The old tool gateway could not be removed yet: {output[-300:]}")
