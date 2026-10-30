"""Chat's calendar tools: each person's own Mu3Lab calendar, the one the dashboard shows.

There is one calendar for each person: Nextcloud's once they connect it, Mu3Lab's own store until
then (``ctl/local_calendar.py``). Chat reads and changes that same calendar, so the phone, the
dashboard, Nextcloud and chat always agree; Open WebUI's own calendar is switched off.

- Each person has a calendar key that Mu3Lab makes for them. It reaches only their own calendar,
  through ``/api/v1/chat-tools/calendar`` on the control plane, which refuses anything that came
  through Caddy (``ctl/api/routes/chat_calendar.py``).
- One small adapter (``platform/calendar-tools``) turns MCP tool calls into those requests. It
  holds no secret of its own: every call carries the caller's key.
- Each person gets a ContextForge gateway and virtual server holding their key, kept to their own
  team (``ctl/person_connectors.py``), so nobody else can call it.
- Chat gives each person their calendar tools: Open WebUI on the main assistant, LobeChat as a
  Calendar assistant (``chat_ops.desired_assistants``).
"""

from __future__ import annotations

import hmac
import secrets
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ctl import actions, platform_releases
from ctl.contextforge import ContextForgeError, client, endpoint
from ctl.runtime import RuntimePaths
from ctl.store import records
from ctl.store.secrets import SecretStore

Log = Callable[[str], None]
ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "platform" / "calendar-tools"
PROJECT = "calendar-tools"
# Per person: their calendar key (secret store) and their ContextForge entries (records).
KEY_SCOPE = "calendar-tool-key"
SCOPE = "calendar-tools"
URL = "http://host.docker.internal:8831/mcp"
TITLE = "Calendar"
DESCRIPTION = "Your Mu3Lab calendar: see, add, move and remove events."
INSTRUCTIONS = (
    "Use the calendar tools for the person's own Mu3Lab calendar, the same one the Mu3Lab dashboard, Nextcloud "
    "and their phone show. List events before changing one, use the IDs the tools return, and give times in the "
    "person's time zone. Ask before moving or deleting an event. An event can conflict with a change made in "
    "Nextcloud meanwhile; then say so and suggest the dashboard's Calendar page to choose which version to keep."
)


# --- Keys ---------------------------------------------------------------------------


def key_for(uid: str, paths: RuntimePaths | None = None) -> str:
    """This person's calendar key, made the first time."""
    store = SecretStore(paths or RuntimePaths())
    saved = store.get(KEY_SCOPE, uid)
    if isinstance(saved, dict) and saved.get("token"):
        return str(saved["token"])
    token = "mu3cal_" + secrets.token_urlsafe(32)
    store.put(KEY_SCOPE, uid, {"token": token})
    return token


def person_for(token: str, paths: RuntimePaths | None = None) -> str:
    """Whose key this is, or "" for none; compared in constant time."""
    if not token.startswith("mu3cal_"):
        return ""
    store = SecretStore(paths or RuntimePaths())
    found = ""
    for uid in store.list_names(KEY_SCOPE):
        saved = store.get(KEY_SCOPE, uid)
        if isinstance(saved, dict) and hmac.compare_digest(str(saved.get("token", "")), token):
            found = uid
    return found


# --- The adapter ----------------------------------------------------------------------


def _project(paths: RuntimePaths) -> Path:
    return paths.projects / PROJECT


def _materialize(paths: RuntimePaths) -> Path:
    """The adapter's Compose project: built from the checkout, or the release's verified image."""
    target = _project(paths)
    target.mkdir(mode=0o750, parents=True, exist_ok=True)
    compose = yaml.safe_load((SOURCE / "docker-compose.yml").read_text(encoding="utf-8"))
    for service in compose["services"].values():
        build = service.get("build")
        if isinstance(build, dict):
            build["context"] = str((SOURCE / build.get("context", ".")).resolve())
    (target / "docker-compose.yml").write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
    platform_releases.pin_compose(ROOT, SOURCE / "docker-compose.yml", target / "docker-compose.yml")
    return target


def ensure_running(log: Log, paths: RuntimePaths | None = None) -> bool:
    paths = paths or RuntimePaths()
    folder = _materialize(paths)
    rc, output = actions.docker_container_statuses(actions._compose_project_name(folder))
    if rc == 0 and any("\tUp" in line for line in output.splitlines()):
        return True
    rc, output = actions.compose_up(folder, log, wait_timeout=60, build=True)
    if rc:
        log(f"Chat's calendar tools did not start: {output[-300:]}")
    return rc == 0


def stop(log: Log, paths: RuntimePaths | None = None) -> None:
    folder = _project(paths or RuntimePaths())
    if (folder / "docker-compose.yml").is_file():
        actions.compose_down(folder, log)
        shutil.rmtree(folder, ignore_errors=True)


# --- Each person's ContextForge entries -------------------------------------------------


def sync(log: Log) -> bool:
    """Give everyone in the household their calendar tools; remove them for anyone who left.

    Returns whether anything changed. Does nothing until ContextForge runs.
    """
    from ctl import chat_ops, contextforge, person_connectors
    from ctl.contextforge_ops import ensure_accounts
    from ctl.people import PeopleError
    from ctl.service_state import tailnet_dns_name

    gateway_app = contextforge.app().id
    if not chat_ops.installed(gateway_app) or not _running(gateway_app):
        return False
    try:
        people = {person["uid"]: person for person in person_connectors.household()}
    except PeopleError as exc:
        log(f"Calendar tools were not updated: {exc}")
        return False
    if not ensure_running(log):
        return False
    changed = False
    dns_name = tailnet_dns_name()
    try:
        ensure_accounts(sorted(person["email"] for person in people.values()))
        with client() as api:
            for uid, person in people.items():
                record = records.get(SCOPE, uid)
                if record.get("server_id") and record.get("email") == person["email"]:
                    continue
                team_id = person_connectors.team(api, person)
                tag = person_connectors.tag(uid)
                gateway = api.ensure_gateway(
                    f"mu3lab-calendar--{tag}", URL, key_for(uid), f"Calendar for {person['name']}", team_id=team_id
                )
                tools = api.tools(str(gateway["id"]))
                if not tools:
                    raise ContextForgeError("ContextForge found no calendar tools.")
                virtual = api.ensure_server(
                    f"Calendar - {person['username']}",
                    f"{person['name']}'s Mu3Lab calendar.",
                    [str(tool["id"]) for tool in tools if tool.get("id")],
                    team_id=team_id,
                )
                if dns_name:
                    api.accept_chat_sign_ins(virtual, dns_name)
                records.put(
                    SCOPE,
                    uid,
                    {
                        "email": person["email"],
                        "team_id": team_id,
                        "gateway_id": str(gateway["id"]),
                        "server_id": str(virtual["id"]),
                    },
                )
                changed = True
            for uid in records.names(SCOPE):
                if uid not in people:
                    _remove(api, uid)
                    changed = True
    except ContextForgeError as exc:
        log(f"Calendar tools were not updated: {exc}")
    return changed


def _running(app_id: str) -> bool:
    from ctl.control_state import ControlState

    state = ControlState.runtime()
    installation = state.installation(app_id) if state else None
    return bool(installation and installation["state"] == "running")


def _remove(api, uid: str) -> None:
    record = records.get(SCOPE, uid)
    for kind, identifier in (("server", record.get("server_id")), ("gateway", record.get("gateway_id"))):
        if identifier:
            getattr(api, f"delete_{kind}")(str(identifier))
    records.delete(SCOPE, uid)


def forget(uid: str, log: Log) -> None:
    """Someone left the household: their key stops working at once, then their tools are removed."""
    SecretStore(RuntimePaths()).delete(KEY_SCOPE, uid)
    if not records.get(SCOPE, uid):
        return
    try:
        with client() as api:
            _remove(api, uid)
    except ContextForgeError as exc:
        log(f"Their calendar tools are switched off but not yet removed from ContextForge: {exc}")


def assistants(uid: str = "") -> list[dict[str, Any]]:
    """Calendar tools for chat: everyone's for Open WebUI, or one person's for LobeChat (``uid``)."""
    from ctl import person_connectors

    found = []
    for person_uid in records.names(SCOPE):
        if uid and person_uid != uid:
            continue
        record = records.get(SCOPE, person_uid)
        if not record.get("server_id"):
            continue
        found.append(
            {
                "id": "calendar" if uid else f"calendar--{person_connectors.tag(person_uid)}",
                "title": TITLE,
                "description": DESCRIPTION,
                "instructions": INSTRUCTIONS,
                "url": endpoint(str(record["server_id"])),
                "active": True,
                "audience": "household",
                "person": str(record["email"]),
                "team_id": str(record["team_id"]),
                "server_id": str(record["server_id"]),
                # Open WebUI gives these to the main assistant rather than a Calendar assistant of their own.
                "tools_only": True,
            }
        )
    return found
