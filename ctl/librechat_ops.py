"""Keep everyone's app assistants in LibreChat, through its admin configuration API.

Like Open WebUI, nobody connects chat by hand. Mu3Lab's own LibreChat administrator
(``ctl.integrations.librechat``) keeps:

* the Base profile: the household's menu (the main Mu3Lab assistant, one assistant per
  app with a connector, and the second model choice) and those apps' tools;
* each person's own profile, once they have a LibreChat account: their whole menu, now
  with administrators-only assistants for Mu3Lab's administrators, their own assistants
  for private apps, and their calendar tools on the main assistant.

Every app tool passes the signed-in person's own Authentik sign-in on to ContextForge
(``{{LIBRECHAT_OPENID_ACCESS_TOKEN}}``), so ContextForge knows and logs who made every call.
While an app's tools are out of reach its assistant stays in the menu, named for why
(``Nextcloud (stopped)``), with no tools and instructions that say why first.

After every change Mu3Lab reads back what LibreChat serves each person, and asks
ContextForge whether each assistant's tools are switched on; the dashboard shows that.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from ctl import chat_ops, contextforge, contextforge_ops
from ctl.contextforge import ContextForgeError
from ctl.control_state import ControlState
from ctl.integrations.librechat import ADMIN_EMAIL, BASE, PASSWORD_ENV, ChatError, LibreChat
from ctl.integrations.openwebui import connection_id, main_model, menu_name
from ctl.manifest.catalog import App, cached
from ctl.mcp_catalog import load as load_connectors
from ctl.people import PeopleError, chat_people
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.store import records

Log = Callable[[str], None]
SCOPE = "librechat-chat"
RECORD = "household"
# LibreChat's own endpoint for Mu3Lab's models (apps/librechat/librechat.yaml).
ENDPOINT = "Mu3Lab"
BASE_MODEL = "mu3lab-chat"
MAIN = "mu3lab"
SPEC_PREFIX = "mu3lab-"
# LibreChat fills this in, in the person's time zone, with each message; without it a model guesses
# the date, and books "tomorrow" a year early.
NOW = (
    'Now: {{current_datetime}}. Work out "today", "tomorrow" and every other date from this, in this time zone; '
    "never assume another year."
)
PICK = (
    "When a request needs one of these apps, name the assistant to use and tell the person to choose it from the "
    "menu at the top of the chat. When its app is out of reach, say so first, with how to fix it."
)
# The main assistant calls each person's app agents itself (LibreChat subagents).
DELEGATE = (
    "When a request needs one of these apps, hand it to that app's assistant with your subagent tool, and tell "
    "the person which app you are using. The assistant cannot see this conversation: give it the whole task with "
    "every detail it needs from the conversation (names, dates, amounts, what was decided). For a request that "
    "spans apps, call each assistant in turn and pass on what the earlier ones found, then answer with the "
    "combined result. If an assistant asks for a confirmation or a missing detail, ask the person, then call it "
    "again with their answer. When an app is out of reach, say so first, with how to fix it. People can also "
    "choose an app's assistant from the menu at the top of the chat to talk to it directly."
)
# LibreChat's name for "every tool on this MCP server" in an agent's tool list.
ALL_TOOLS = "sys__all__sys_mcp_"
MAIN_STARTERS = [
    "What can you help me with?",
    "What's on my calendar this week?",
    "Plan dinners from what we have in stock",
    "Which apps should I use for photos and documents?",
]


def _app() -> App:
    return chat_ops.by_workspace("libre")


def client(paths: RuntimePaths | None = None) -> LibreChat:
    """LibreChat on this server's loopback, as Mu3Lab's administrator."""
    app = _app()
    password = read_runtime_env((paths or RuntimePaths()).projects / app.id / ".env").get(PASSWORD_ENV, "")
    if not password:
        raise ChatError("LibreChat's administrator password is missing; retry its setup.")
    return LibreChat(f"http://127.0.0.1:{app.manifest.service.local_port}", password)


def sync_agents(log: Log) -> tuple[bool, str]:
    from ctl import access, resource_locks

    with resource_locks.hold(access.CHAT_LOCK):
        return _sync(log)


def revoke(subject: str) -> None:
    """Take this person's own assistants, app agents and tools out of LibreChat; their conversations stay.

    Their sign-in already ends with their Authentik account, so their tools stop working at once.
    """
    from ctl import access, resource_locks

    with resource_locks.hold(access.CHAT_LOCK):
        entry = records.get(SCOPE, subject)
        if entry.get("account") or entry.get("agents"):
            with client() as api:
                if entry.get("account"):
                    api.delete_profile("user", str(entry["account"]))
                for agent in (entry.get("agents") or {}).values():
                    api.delete_agent(str(agent["id"]))
        records.delete(SCOPE, subject)


def _tool_title(name: str) -> str:
    """A name LibreChat takes for app tools: letters, digits, spaces, hyphens and apostrophes only."""
    cleaned = " ".join(re.sub(r"[^\w '’-]|_", " ", name).split()).lstrip("'’-")
    return cleaned or "App tools"


def _server(item: dict[str, Any]) -> dict[str, Any]:
    """One assistant's app tools: its ContextForge virtual server, reached with the person's own sign-in."""
    return {
        "type": "streamable-http",
        "url": item["url"],
        "title": _tool_title(str(item.get("app_name") or item["title"])),
        "description": item["description"],
        "headers": {"Authorization": "Bearer {{LIBRECHAT_OPENID_ACCESS_TOKEN}}"},
        # Its sign-in is the header above: no OAuth of its own to look for.
        "requiresOAuth": False,
        # Offered through its assistant, not as a loose tool in every chat.
        "chatMenu": False,
        "startup": False,
    }


def _spec(item: dict[str, Any], agent_id: str = "") -> dict[str, Any]:
    """One app assistant in the menu: the person's own app agent, or (the household's Base menu) a preset."""
    active = bool(item.get("active", True))
    spec: dict[str, Any] = {
        "name": SPEC_PREFIX + str(item["id"]),
        "label": menu_name(item["title"], str(item.get("status") or "ready")),
        "description": item["description"],
        "group": "App assistants",
        **({"conversation_starters": list(item["starters"])} if item.get("starters") else {}),
    }
    if agent_id:
        # The agent carries the instructions and tools, the same one the main assistant calls.
        return spec | {"preset": {"endpoint": "agents", "agent_id": agent_id}}
    return spec | {
        # Out of reach: no tools; the assistant stays and its instructions say why.
        **({"mcpServers": [connection_id(str(item["id"]))]} if active else {}),
        "preset": {
            "endpoint": ENDPOINT,
            "model": BASE_MODEL,
            "modelLabel": item["title"],
            "promptPrefix": item["instructions"],
        },
    }


def _agent_body(item: dict[str, Any]) -> dict[str, Any]:
    """One person's app agent: the app assistant's instructions and, while its app is in reach, its tools."""
    active = bool(item.get("active", True))
    return {
        "name": item["title"],
        "description": item["description"],
        "instructions": item["instructions"],
        "provider": ENDPOINT,
        "model": BASE_MODEL,
        "tools": [ALL_TOOLS + connection_id(str(item["id"]))] if active else [],
        "conversation_starters": list(item.get("starters") or []),
        "category": "general",
    }


def _digest(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _ensure_agents(
    api: LibreChat,
    account: str,
    items: list[dict[str, Any]],
    known: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
) -> None:
    """Each of this person's app assistants as an agent Mu3Lab's administrator owns and shares with them alone.

    ``known`` is what the last sync made: ``{item id: {id, _id, digest}}``. An agent is changed only when its
    assistant changed (LibreChat keeps every version), made again if someone deleted it, and deleted once its
    app has no assistant for this person. ``agents`` fills in as it goes, so a sync that fails partway still
    records every agent it made.
    """
    for item in items:
        if item.get("tools_only"):
            continue
        key, body = str(item["id"]), _agent_body(item)
        digest, record = _digest(body), dict(known.get(str(item["id"])) or {})
        if record and api.agent(str(record["id"])) is None:
            record = {}
        if not record:
            made = api.create_agent(body)
            api.share_agent(str(made["_id"]), [account])
            record = {"id": str(made["id"]), "_id": str(made["_id"])}
        elif record.get("digest") != digest:
            api.update_agent(str(record["id"]), body)
        agents[key] = record | {"digest": digest, "tools": body["tools"]}
    for key, record in known.items():
        if key not in agents:
            api.delete_agent(str(record["id"]))


def _choices() -> list[dict[str, Any]]:
    """The second-ranked source, offered beside the main assistant (as in Open WebUI)."""
    from ctl import local_models
    from ctl.integrations.openwebui import LOCAL, OUTSIDE

    chosen, local = local_models.settings(), local_models.chat_model()
    specs = []
    if chosen["privacy"] == "local_first":
        specs.append(
            {
                "name": OUTSIDE,
                "label": "Outside providers",
                "description": "For harder questions: sends your messages to the outside AI providers set up in "
                "Mu3Lab.",
                "group": "Models",
                "preset": {"endpoint": ENDPOINT, "model": OUTSIDE},
            }
        )
    if local and chosen["sources"][0] == "outside":
        specs.append(
            {
                "name": LOCAL,
                "label": f"{local.name} (private, on this server)",
                "description": "Answers on this server: nothing you send leaves it. Slower and less capable than "
                "outside providers.",
                "group": "Models",
                "preset": {"endpoint": ENDPOINT, "model": LOCAL},
            }
        )
    return specs


def _menu(
    items: list[dict[str, Any]], guide: str, gateway_down: str, agents: dict[str, dict[str, Any]] | None = None
) -> tuple[list[dict], dict[str, dict]]:
    """One person's (or the household's) menu and app tools from ``chat_ops.desired_assistants``.

    ``agents`` (one person's, from ``_ensure_agents``): the app assistants become those agents, and the main
    assistant calls the ones that can reach their app as subagents.
    """
    agents = agents or {}
    servers = {connection_id(str(item["id"])): _server(item) for item in items if item.get("active", True)}
    calendar = [item for item in items if item.get("tools_only") and item.get("active", True)]
    apps = [item for item in items if not item.get("tools_only")]
    # The main assistant names only the household's assistants, each app once (a ready one first).
    listed: dict[str, dict[str, Any]] = {}
    for item in apps:
        if item.get("audience") == "operators":
            continue
        if item["title"] not in listed or item.get("active"):
            listed[item["title"]] = item
    # Only agents that can reach their app; one out of reach is still named in the instructions, with why.
    callable_ids = [
        str(agents[str(item["id"])]["id"]) for item in apps if item.get("active", True) and str(item["id"]) in agents
    ]
    main = main_model(
        list(listed.values()),
        guide,
        tool_ids=[connection_id(str(item["id"])) for item in calendar],
        gateway_down=gateway_down,
        now=NOW,
        pick=DELEGATE if callable_ids else PICK,
        delegates=bool(callable_ids),
    )
    specs = [
        {
            "name": MAIN,
            "label": "Mu3Lab",
            "default": True,
            "description": "General help, your calendar, and every app through its assistant."
            if callable_ids
            else main["meta"]["description"],
            "conversation_starters": MAIN_STARTERS,
            **({"mcpServers": [connection_id(str(item["id"])) for item in calendar]} if calendar else {}),
            **({"subagents": {"enabled": True, "allowSelf": False, "agent_ids": callable_ids}} if callable_ids else {}),
            "preset": {
                "endpoint": ENDPOINT,
                "model": BASE_MODEL,
                "modelLabel": "Mu3Lab",
                "promptPrefix": main["params"]["system"],
            },
        },
        *_choices(),
        *(_spec(item, str((agents.get(str(item["id"])) or {}).get("id") or "")) for item in apps),
    ]
    return specs, servers


def _desired(uid: str = "", *, operator: bool = False, gateway: bool) -> list[dict[str, Any]]:
    """The household's assistants (``uid`` empty) or one person's, worded for LibreChat."""
    items = chat_ops.desired_assistants(uid, operator=operator, gateway=gateway, now=NOW, person_view=True)
    if uid:
        return items
    # The Base profile: shared assistants only; administrators and private apps come per person.
    return [
        item
        for item in items
        if not item.get("person") and not item.get("tools_only") and item.get("audience") != "operators"
    ]


def _sync(log: Log) -> tuple[bool, str]:
    """Bring LibreChat in step with what is running now; it never needs ContextForge to be up to do that."""
    from ctl.openwebui_ops import _guide

    paths = RuntimePaths()
    gateway, up = chat_ops.gateway_name(), chat_ops.gateway_up()
    gateway_down = "" if up else gateway
    people: dict[str, dict] = {}
    try:
        people = chat_people()
        household = _desired(gateway=up)
        everyone = chat_ops.desired_assistants(gateway=up)
        guide = _guide({str(item["id"]).split("--")[0] for item in everyone})
        if any(item["active"] for item in everyone):
            _ensure_accounts(log, people)
        base_specs, base_servers = _menu(household, guide, gateway_down)
        with client() as api:
            api.save_profile(*BASE, {"modelSpecs": {"list": base_specs}, "mcpServers": base_servers})
            # Nobody browses or publishes agents: Mu3Lab's assistants are the menu (administrators included).
            for role in ("ADMIN", "USER"):
                api.set_role_permissions(role, {"MARKETPLACE": {"USE": False}})
            accounts = {str(user.get("email") or "").lower(): user for user in api.users() if user.get("id")}
            wanted = {_server_id(str(item["url"])) for item in everyone if item.get("url")}
            tools = _prepare_servers(log, wanted) if up else {}
            menus = {
                uid: _desired(uid, operator=bool(person.get("operator")), gateway=up) for uid, person in people.items()
            }
            # LibreChat gives an agent an app's tools only when its maker's settings name that app's tool
            # server, so Mu3Lab's administrator carries the name of every server anyone has.
            every_server: dict[str, dict] = {}
            for items in menus.values():
                every_server.update(_menu(items, guide, gateway_down)[1])
            if admin := accounts.get(ADMIN_EMAIL):
                api.save_profile("user", str(admin["id"]), {"mcpServers": every_server})
            failures = []
            for uid, person in people.items():
                email = str(person.get("email") or "").lower()
                account = accounts.get(email)
                items = menus[uid]
                previous = records.get(SCOPE, uid, paths)
                if not account:
                    records.put(SCOPE, uid, {"account": "", "checks": {}, "error": ""}, paths)
                    continue
                agents: dict[str, dict[str, Any]] = {}
                try:
                    _ensure_agents(api, str(account["id"]), items, previous.get("agents") or {}, agents)
                    specs, servers = _menu(items, guide, gateway_down, agents)
                    own = {name: server for name, server in servers.items() if base_servers.get(name) != server}
                    api.save_profile("user", str(account["id"]), {"modelSpecs": {"list": specs}, "mcpServers": own})
                    checks = _check(items, api.effective(str(account["id"])), tools, gateway, agents)
                    records.put(
                        SCOPE,
                        uid,
                        {"account": str(account["id"]), "checks": checks, "error": "", "agents": agents},
                        paths,
                    )
                except ChatError as exc:
                    kept = {**(previous.get("agents") or {}), **agents}
                    records.put(
                        SCOPE,
                        uid,
                        {"account": str(account["id"]), "checks": {}, "error": str(exc), "agents": kept},
                        paths,
                    )
                    failures.append(str(exc))
                    continue
                failures.extend(
                    f"{result['title']}: {result['detail']}"
                    for result in checks.values()
                    if result["status"] == "failed"
                )
            # People who left the household keep no assistants of their own here.
            known = {str(person.get("email") or "").lower() for person in people.values()}
            for email, account in accounts.items():
                if email not in known and email != ADMIN_EMAIL:
                    api.delete_profile("user", str(account["id"]))
    except (ChatError, ContextForgeError, PeopleError, OSError, ValueError, KeyError) as exc:
        log(f"LibreChat assistants were not updated: {exc}")
        records.put(SCOPE, RECORD, {"error": str(exc), "gateway_down": not up}, paths)
        return False, str(exc)
    records.put(SCOPE, RECORD, {"error": "", "gateway_down": not up}, paths)
    if failures:
        log("LibreChat's check found assistants that do not work: " + "; ".join(failures))
        return False, failures[0]
    if not up:
        return True, f"{gateway} is stopped, so chat tells everyone their app assistants are out of reach."
    return True, "Your assistants are ready."


def _ensure_accounts(log: Log, people: dict[str, dict]) -> None:
    """A ContextForge account for everyone, so the sign-in LibreChat passes on finds them."""
    try:
        contextforge_ops.ensure_accounts(sorted({p["email"] for p in people.values() if p.get("email")}))
    except ContextForgeError as exc:
        log(f"ContextForge accounts were not checked this time: {exc}")


def _server_id(url: str) -> str:
    return url.split("/servers/", 1)[-1].split("/", 1)[0]


def _prepare_servers(log: Log, server_ids: set[str]) -> dict[str, int]:
    """Make sure each assistant's virtual server takes LibreChat's sign-ins, and count its switched-on tools.

    Servers made before LibreChat was installed trusted Open WebUI alone. Returns tool counts by
    server ID; empty when ContextForge did not answer.
    """
    from ctl.service_state import tailnet_dns_name

    dns_name = tailnet_dns_name()
    try:
        with contextforge.client() as api:
            counts = {}
            for server in api.servers():
                server_id = str(server.get("id"))
                if server_id not in server_ids:
                    continue
                if dns_name:
                    api.accept_chat_sign_ins(server, dns_name)
                counts[server_id] = len(contextforge._field(server, "associated_tools") or [])
            return counts
    except ContextForgeError as exc:
        log(f"LibreChat's tool check could not reach ContextForge: {exc}")
        return {}


def _check(
    items: list[dict[str, Any]],
    served: dict[str, Any],
    tools: dict[str, int],
    gateway: str,
    agents: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Read back what LibreChat serves one person for each assistant, and whether its tools are switched on.

    An app assistant that is an agent (``agents``) must open that agent and give it the app's tools.
    """
    agents = agents or {}
    from ctl import agent_context

    specs = {str(spec.get("name")): spec for spec in ((served.get("modelSpecs") or {}).get("list") or [])}
    servers = served.get("mcpServers") or served.get("mcpConfig") or {}
    main = specs.get(MAIN) or {}
    results: dict[str, dict[str, Any]] = {}
    for item in items:
        name = connection_id(str(item["id"]))
        active = bool(item.get("active", True))
        spec = main if item.get("tools_only") else specs.get(SPEC_PREFIX + str(item["id"]))
        result: dict[str, Any] = {"status": "failed", "tools": 0, "title": item["title"]}
        agent = agents.get(str(item["id"]))
        if agent and not item.get("tools_only"):
            opens = ((spec or {}).get("preset") or {}).get("agent_id") == agent["id"]
            attached = opens and ALL_TOOLS + name in (agent.get("tools") or [])
        else:
            attached = name in ((spec or {}).get("mcpServers") or [])
        if not spec:
            result["detail"] = "LibreChat does not have this assistant."
        elif not active:
            if attached:
                result["detail"] = "LibreChat still gives this assistant its tools."
            else:
                cause, _fix = agent_context.out_of_reach(
                    str(item.get("app_name") or item["title"]),
                    str(item.get("status") or "stopped"),
                    gateway=gateway,
                    connector_state=str(item.get("connector_state") or ""),
                )
                result |= {"status": "paused", "detail": f"In chat, marked out of reach: {cause}."}
        elif not attached or (servers.get(name) or {}).get("url") != item["url"]:
            result["detail"] = "LibreChat's assistant does not point at this app's tools."
        elif _server_id(str(item["url"])) not in tools:
            result |= {"status": "pending", "detail": f"Set up in LibreChat; {gateway} did not answer the tool check."}
        elif count := tools[_server_id(str(item["url"]))]:
            result |= {"status": "ready", "tools": count, "detail": f"{count} tools are switched on for it."}
        else:
            result["detail"] = f"Set up in LibreChat, but none of this app's tools are switched on in {gateway}."
        results[str(item["id"])] = result
    return results


def assistant_report(uid: str, *, operator: bool = False) -> list[dict[str, str]]:
    """Why each installed app does or does not have an assistant in this person's LibreChat."""
    from ctl import person_connectors

    state = ControlState.runtime()
    if state is None:
        return []
    household = records.get(SCOPE, RECORD, RuntimePaths())
    entry = records.get(SCOPE, uid, RuntimePaths())
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
        check = (entry.get("checks") or {}).get(app.id) or {}
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
        elif household.get("gateway_down"):
            gateway = chat_ops.gateway_name()
            status, detail = (
                "connector_down",
                f"{gateway} is stopped, so chat can't reach this app. Start {gateway} under Apps.",
            )
        elif check.get("status") == "ready":
            status, detail = "ready", f"Available in chat. {check['detail']}"
        elif check.get("status") == "pending":
            status, detail = "pending", str(check["detail"])
        elif entry and not entry.get("account"):
            status, detail = "pending", "Added once you have opened LibreChat."
        elif entry.get("error") or household.get("error"):
            status, detail = "failed", str(entry.get("error") or household["error"])
        elif any(connector.per_person for connector in own):
            record = next(
                (
                    records.get(person_connectors.SCOPE, f"{connector.id}:{uid}")
                    for connector in own
                    if connector.per_person
                ),
                {},
            )
            status, detail = (
                "pending",
                str(record.get("error") or "Your own copy of this app's tools is being prepared."),
            )
        else:
            status, detail = "pending", "Adding to chat and checking it within five minutes."
        report.append({"id": app.id, "name": app.manifest.name, "status": status, "detail": detail})
    return report
