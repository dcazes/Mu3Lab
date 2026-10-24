"""Private apps' chat tools, one set per person.

An app that keeps each person's data private (``per_person: true`` on its connector) cannot give
the household one shared assistant: the connector's key would show its owner's data to everyone.
So everyone gets their own:

- a key minted in the app for their own account (the connector's provision script, given their
  username or email);
- their own copy of the connector (none for an app that serves MCP itself);
- their own ContextForge gateway and virtual server, kept to a ContextForge team only they
  belong to, so other household members can neither see nor call it.

The installing owner keeps the connector Mu3Lab already runs for the app (its key is theirs); its
gateway and virtual server are kept to their team too (``contextforge_ops.connect``). Chat apps
give each person the assistant that points at their own server (``chat_ops.desired_assistants``).

Someone who has not opened the app yet has no account in it, so their copy waits until they
have. The app stopping stops the copies; a person leaving the household, or the connector being
switched off, removes them. ContextForge's administrators (Mu3Lab's operators) can still reach
every team's tools in ContextForge's own screens.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ctl import actions, platform_releases
from ctl.contextforge import ContextForgeError, client, safe_name, tool_ids
from ctl.engine.compose import Compose
from ctl.jobs import redact
from ctl.manifest.catalog import cached
from ctl.mcp_registry import credential_path
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl.store import onboarding as onboarding_state
from ctl.store import records
from ctl.store.secrets import SecretStore

Log = Callable[[str], None]
# Per connector and person ("<connector>:<uid>"): their copy's state and ContextForge IDs.
SCOPE = "person-tools"
# Per person: the ContextForge team only they belong to.
TEAMS = "person-teams"
# Someone without an app account yet is asked again this often, not on every pass.
WAIT_SECONDS = 600
REGISTER_TRIES = 12


class PersonToolsError(RuntimeError):
    """A safe message about one person's copy."""


def tag(uid: str) -> str:
    """A short, stable name part for one person that does not reveal who they are."""
    return "p" + hashlib.sha256(uid.encode()).hexdigest()[:10]


def instance_id(server_id: str, uid: str) -> str:
    return f"{server_id}--{tag(uid)}"


def project(server_id: str, uid: str, paths: RuntimePaths | None = None) -> Path:
    return (paths or RuntimePaths()).projects / f"mcp-{instance_id(server_id, uid)}"


def _key(server_id: str, uid: str) -> str:
    return f"{server_id}:{uid}"


def household() -> list[dict[str, str]]:
    """Everyone who may use chat now: active administrators and household members."""
    from ctl.people import list_people

    return [
        {
            "uid": str(person["uid"]),
            "username": person["username"],
            "email": person["email"].lower(),
            "name": person["name"],
        }
        for person in list_people()
        if person.get("active") and person.get("role") in {"admin", "member"} and person.get("email")
    ]


def owner_email(service_id: str) -> str:
    """The email of the person whose key the app's own connector holds (the installing owner)."""
    return str((onboarding_state.read(service_id).get("owner") or {}).get("email") or "").lower()


def team(api, person: dict[str, str]) -> str:
    """This person's own ContextForge team, made the first time."""
    saved = records.get(TEAMS, person["uid"])
    if saved.get("team_id") and saved.get("email") == person["email"]:
        return str(saved["team_id"])
    # A team takes only people ContextForge already has an account for.
    api.ensure_user(person["email"], person["username"], secrets.token_urlsafe(32))
    team_id = api.ensure_team(f"Mu3Lab {person['username']}", person["email"])
    records.put(TEAMS, person["uid"], {"team_id": team_id, "email": person["email"]})
    return team_id


def owner_team(api, service_id: str) -> str:
    """The owner's team for an app's own connector, or "" when the owner is not in the household."""
    email = owner_email(service_id)
    person = next((item for item in household() if item["email"] == email), None) if email else None
    return team(api, person) if person else ""


def servers(uid: str) -> dict[str, dict[str, Any]]:
    """This person's live copies by connector: their virtual server and team."""
    found = {}
    for name in records.names(SCOPE):
        connector, _, owner = name.partition(":")
        record = records.get(SCOPE, name)
        if owner == uid and record.get("state") == "live" and record.get("server_id"):
            found[connector] = record
    return found


# --- Making one person's copy ----------------------------------------------------------


def _mint(server, person: dict[str, str], log: Log) -> dict[str, str] | None:
    """The person's own key from the app, as the connector's credential settings; ``None`` if typed by hand."""
    app, connector = cached().connector(server.id)
    provision = connector.provision
    if provision is None:
        return None
    script = (app.connector_folder(connector.id) / provision.script).read_text()
    arg = person["username"] if provision.owner_username else person["email"]
    result = Compose(RuntimePaths().projects / app.id).run_script(
        provision.service, provision.interpreter, script, log, args=[arg], timeout=90
    )
    if not result.ok:
        raise PersonToolsError(result.error or f"{app.manifest.name} did not make {person['name']}'s chat key.")
    fields = {str(field["key"]): str(field["env"]) for field in server.credentials}
    values = {fields[key]: value for key, value in result.outputs.items() if key in fields}
    missing = [
        str(field["key"]) for field in server.credentials if field.get("required") and field["env"] not in values
    ]
    if missing:
        raise PersonToolsError(f"{app.manifest.name} did not return {', '.join(missing)}.")
    return values


def _renamed(compose: Path, suffix: str) -> dict[str, str]:
    """Give a copied Compose project its own name, service names and no host ports; returns the renames."""
    data = yaml.safe_load(compose.read_text(encoding="utf-8"))
    data["name"] = f"{data['name']}-{suffix}"
    renames = {name: f"{name}-{suffix}" for name in data.get("services", {})}
    services = {}
    for name, service in data.get("services", {}).items():
        # Copies are reached on the connector network only; their loopback test port is the owner's.
        service.pop("ports", None)
        service.pop("container_name", None)
        depends = service.get("depends_on")
        if isinstance(depends, list):
            service["depends_on"] = [renames.get(item, item) for item in depends]
        elif isinstance(depends, dict):
            service["depends_on"] = {renames.get(key, key): value for key, value in depends.items()}
        services[renames[name]] = service
    data["services"] = services
    compose.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return renames


def _materialize(server, person: dict[str, str], credential: dict[str, str], root: Path) -> tuple[Path, str]:
    """Write the person's copy and its settings; returns its folder and the address ContextForge calls."""
    iid = instance_id(server.id, person["uid"])
    target = project(server.id, person["uid"])
    target.mkdir(mode=0o750, parents=True, exist_ok=True)
    store = SecretStore()
    # The owner's settings minus anything that belongs to the owner: their key and the connector's own secrets.
    personal = {str(field["env"]) for field in server.credentials} | {str(item["env"]) for item in server.secrets}
    values = {key: value for key, value in read_runtime_env(credential_path(server.id)).items() if key not in personal}
    saved = store.get("connector", iid) or {}
    for item in server.secrets:
        env = str(item["env"])
        values[env] = saved.get(env) or secrets.token_urlsafe(int(item["length"]))
    values.update(credential)
    store.put("connector", iid, values)
    url = server.endpoint
    if not server.builtin:
        source = server.compose_path(root)
        if source is None or not (source / "docker-compose.yml").is_file():
            raise PersonToolsError(f"{server.name}'s connector files are missing.")
        for item in source.iterdir():
            if item.name.startswith(".env") or item.is_symlink():
                continue
            if item.is_file():
                shutil.copy2(item, target / item.name)
            elif item.is_dir():
                shutil.copytree(item, target / item.name, dirs_exist_ok=True)
        for relative in server.include:
            shutil.copy2(root / relative, target / Path(relative).name)
        platform_releases.pin_compose(root, source / "docker-compose.yml", target / "docker-compose.yml")
        renames = _renamed(target / "docker-compose.yml", tag(person["uid"]))
        address = urllib.parse.urlsplit(server.endpoint)
        host = renames.get(address.hostname or "")
        if not host:
            raise PersonToolsError(f"{server.name}'s address does not name one of its services.")
        url = urllib.parse.urlunsplit(address._replace(netloc=f"{host}:{address.port}" if address.port else host))
    env_path = target / ".env"
    env_path.write_text(runtime_env_text(values), encoding="utf-8")
    os.chmod(env_path, 0o600)
    return target, url


def _running(folder: Path) -> bool:
    rc, output = actions.docker_container_statuses(actions._compose_project_name(folder))
    return rc == 0 and any("\tUp" in line for line in output.splitlines())


def _register(api, server, app_name: str, person: dict[str, str], url: str, token: str, team_id: str) -> dict[str, str]:
    """The person's gateway and virtual server, kept to their team; retried while their copy starts."""
    from ctl.contextforge_ops import starting_tools
    from ctl.service_state import tailnet_dns_name

    iid = instance_id(server.id, person["uid"])
    description = f"{app_name}: {server.name} for {person['name']}"
    for attempt in range(REGISTER_TRIES):
        try:
            gateway = api.ensure_gateway(iid, url, token, description, dict(server.headers), team_id=team_id)
            tools = api.tools(str(gateway["id"]))
            if tools:
                break
        except ContextForgeError:
            if attempt == REGISTER_TRIES - 1:
                raise
        time.sleep(5)
    else:
        raise PersonToolsError(f"ContextForge found no tools on {person['name']}'s {server.name}.")
    virtual = api.ensure_server(
        safe_name(f"{app_name} - {server.name} - {person['username']}"),
        f"{person['name']}'s tools in {app_name}.",
        tool_ids(tools, starting_tools(server)),
        team_id=team_id,
    )
    dns_name = tailnet_dns_name()
    if dns_name:
        api.accept_chat_sign_ins(virtual, dns_name)
    return {"gateway_id": str(gateway["id"]), "server_id": str(virtual["id"])}


def _ensure(api, server, app_name: str, person: dict[str, str], root: Path, log: Log) -> bool:
    """Bring one person's copy up; returns whether anything changed."""
    key = _key(server.id, person["uid"])
    record = records.get(SCOPE, key)
    folder = project(server.id, person["uid"])
    if record.get("state") == "live" and (server.builtin or _running(folder)):
        return False
    if record.get("state") == "waiting" and time.time() - float(record.get("checked_at") or 0) < WAIT_SECONDS:
        return False
    base = {"uid": person["uid"], "email": person["email"], "username": person["username"], "connector": server.id}
    credential = {
        env: value
        for env, value in (SecretStore().get("connector", instance_id(server.id, person["uid"])) or {}).items()
        if env in {str(field["env"]) for field in server.credentials}
    }
    if len(credential) < sum(1 for field in server.credentials if field.get("required")):
        try:
            minted = _mint(server, person, log)
        except PersonToolsError as exc:
            # Usually: they have not opened the app yet, so it has no account for them.
            records.put(SCOPE, key, base | {"state": "waiting", "error": str(exc), "checked_at": time.time()})
            return False
        if minted is None:
            records.put(
                SCOPE, key, base | {"state": "unavailable", "error": "This app's chat key is typed in by hand."}
            )
            return False
        credential = minted
    folder, url = _materialize(server, person, credential, root)
    if not server.builtin:
        rc, output = actions.compose_up(folder, log, wait_timeout=120)
        if rc:
            raise PersonToolsError(redact(output[-300:]) or f"{person['name']}'s {server.name} did not start.")
    team_id = team(api, person)
    token = read_runtime_env(folder / ".env").get("MCP_AUTH_TOKEN", "")
    registered = _register(api, server, _app_name(server), person, url, token, team_id)
    records.put(SCOPE, key, base | registered | {"team_id": team_id, "state": "live", "error": ""})
    log(f"{person['name']} has their own {app_name} tools in chat.")
    return True


def _app_name(server) -> str:
    return cached().get(server.service_id).manifest.name


def sync(server, root: Path, log: Log) -> bool:
    """Give everyone but the owner their own copy and remove copies nobody should have; returns whether any changed."""
    if not server.per_person:
        return False
    from ctl.people import PeopleError

    try:
        people = household()
    except PeopleError as exc:
        log(f"{server.name}: the household could not be read, so nobody's copy changed: {exc}")
        return False
    owner = owner_email(server.service_id)
    wanted = {person["uid"]: person for person in people if person["email"] != owner}
    changed = False
    with client() as api:
        for person in wanted.values():
            try:
                changed = _ensure(api, server, _app_name(server), person, root, log) or changed
            except (PersonToolsError, ContextForgeError, OSError, ValueError) as exc:
                records.put(
                    SCOPE,
                    _key(server.id, person["uid"]),
                    {"uid": person["uid"], "email": person["email"], "connector": server.id}
                    | {"state": "failed", "error": redact(str(exc))},
                )
                log(f"{person['name']}'s {server.name} is not ready: {redact(str(exc))}")
        for name in records.names(SCOPE):
            connector, _, uid = name.partition(":")
            if connector == server.id and uid not in wanted:
                _remove(api, server, uid, log)
                changed = True
    return changed


# --- Stopping and removing -------------------------------------------------------------


def stop(server, log: Log) -> None:
    """The app stopped: stop everyone's copies and switch their gateways off until it is back."""
    if not server.per_person:
        return
    names = [name for name in records.names(SCOPE) if name.partition(":")[0] == server.id]
    if not names:
        return
    with client() as api:
        for name in names:
            record = records.get(SCOPE, name)
            folder = project(server.id, record.get("uid", name.partition(":")[2]))
            if not server.builtin and (folder / "docker-compose.yml").is_file():
                actions.compose_action(folder, "stop", log)
            if record.get("gateway_id"):
                try:
                    api.set_gateway_active(str(record["gateway_id"]), False)
                except ContextForgeError as exc:
                    log(f"{server.name} was not switched off for everyone in ContextForge: {exc}")
            if record.get("state") == "live":
                records.put(SCOPE, name, record | {"state": "stopped"})


def _remove(api, server, uid: str, log: Log) -> None:
    """Take down one person's copy: container, app key, ContextForge entries and Mu3Lab's records."""
    name = _key(server.id, uid)
    record = records.get(SCOPE, name)
    folder = project(server.id, uid)
    if (folder / "docker-compose.yml").is_file():
        actions.compose_down(folder, log)
    _revoke_key(server, record, log)
    for kind, identifier in (("server", record.get("server_id")), ("gateway", record.get("gateway_id"))):
        if identifier:
            getattr(api, f"delete_{kind}")(str(identifier))
    shutil.rmtree(folder, ignore_errors=True)
    SecretStore().delete("connector", instance_id(server.id, uid))
    records.delete(SCOPE, name)


def _revoke_key(server, record: dict[str, Any], log: Log) -> None:
    """Delete the key Mu3Lab minted in the app, where the app has a script for it; best effort."""
    app, connector = cached().connector(server.id)
    provision = connector.provision
    if provision is None or not provision.revoke_script or not record.get("email"):
        return
    arg = str(record.get("username") or "") if provision.owner_username else str(record["email"])
    if not arg:
        return
    script = (app.connector_folder(connector.id) / provision.revoke_script).read_text()
    result = Compose(RuntimePaths().projects / app.id).run_script(
        provision.service, provision.interpreter, script, log, args=[arg], timeout=90
    )
    if not result.ok:
        log(f"{app.manifest.name} kept a chat key Mu3Lab made: {result.error}")


def remove_all(server, log: Log) -> None:
    """The connector was switched off: remove everyone's copies."""
    names = [name for name in records.names(SCOPE) if name.partition(":")[0] == server.id]
    if not names:
        return
    with client() as api:
        for name in names:
            _remove(api, server, name.partition(":")[2], log)


def forget(uid: str, log: Log) -> None:
    """Someone left the household: remove every copy of theirs, then their team."""
    from ctl.mcp_catalog import load as load_connectors
    from ctl.registry import load as load_registry

    connectors = {server.id: server for server in load_connectors(load_registry())}
    names = [name for name in records.names(SCOPE) if name.partition(":")[2] == uid]
    saved = records.get(TEAMS, uid)
    if not names and not saved:
        return
    with client() as api:
        for name in names:
            server = connectors.get(name.partition(":")[0])
            if server is not None:
                _remove(api, server, uid, log)
            else:
                records.delete(SCOPE, name)
        if saved.get("team_id"):
            api.delete_team(str(saved["team_id"]))
        records.delete(TEAMS, uid)
