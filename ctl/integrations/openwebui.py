"""Open WebUI's admin API, used with Mu3Lab's own administrator key.

Mu3Lab keeps two things in step: the tool connections (one MCP connection per
app, pointing at that app's ContextForge virtual server and passing each
person's own Authentik sign-in) and the workspace models that act as each
app's assistant, plus the main assistant every new chat starts with. All are
shared by everyone, except an infrastructure app's read-only assistant, which only
Open WebUI's administrators see, and a private app's, which each person has their own of.
While an app's tools are out of reach (the app is stopped, or ContextForge is), its assistant
stays in the model menu, named for why (``Nextcloud (stopped)``), with its tool connection
switched off and instructions that say why first; old chats keep working with it. ``check`` then
asks Open WebUI itself whether each one is set up and its tools answer. It also hides the AI gateway's own names from the model
menu (``HIDDEN``). Connections and models Mu3Lab did not create are never
touched: our assistants carry the ``mu3lab-app-`` id prefix (or are ``mu3lab-assistant``), our tool
connections the short ``m3-`` one (``connection_id``).
API error bodies never reach logs.

Mu3Lab's status filter (``ctl/integrations/openwebui_filter.py``, ``ensure_status_filter``)
checks every message: when an assistant's tools did not connect, the model is told and the
person sees one line saying why.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from ctl import agent_context

# Distinct from LiteLLM model names such as mu3lab-chat, which Open WebUI also lists.
PREFIX = "mu3lab-app-"
# Our tool connections. Open WebUI names each tool ``<connection>_<tool>`` and cuts a name past 64
# characters to 55 plus a code the model must copy exactly, which models get wrong ("Tool not found").
CONNECTION_PREFIX = "m3-"
# What a person's tag keeps in a connection name: enough to tell a household's people apart.
TAG_LENGTH = 6


def connection_id(item_id: str) -> str:
    """The short ID of an assistant's tool connection: ``m3-grocy``, ``m3-immich-b54595`` for one person's."""
    app_id, _, tag = item_id.partition("--")
    return f"{CONNECTION_PREFIX}{app_id}" + (f"-{tag[-TAG_LENGTH:]}" if tag else "")


def tool_id(item_id: str) -> str:
    return f"server:mcp:{connection_id(item_id)}"


# The household's main assistant: no app tools, the selected model in every new chat
# (DEFAULT_MODELS in apps/open-webui/docker-compose.yml), and it points people to the app assistants.
MAIN = "mu3lab-assistant"
BASE_MODEL = "mu3lab-chat"
# LiteLLM's names, which Open WebUI would list as chat models: the main assistant is built on
# mu3lab-chat, and the rest serve Mu3Lab's own checks, document search, speech and the voice
# assistant. Hidden from the model menu only; they keep working.
# The second-ranked source, offered in the model menu beside the main assistant: outside providers
# while local models come first, the local model while outside providers do (ctl.local_models).
OUTSIDE = "mu3lab-outside"
LOCAL = "mu3lab-local"
HIDDEN = ("mu3lab-chat", "mu3lab-fast", "mu3lab-embed", "mu3lab-stt", "mu3lab-tts", "mu3lab-voice")
# How an assistant whose tools are out of reach is marked in the model menu.
MARKS = {"stopped": "stopped", "gateway_down": "unavailable", "connector_down": "unavailable"}


def menu_name(title: str, status: str) -> str:
    """``Nextcloud (stopped)``; a title with a note of its own keeps one pair: ``qBittorrent (admins, stopped)``."""
    mark = MARKS.get(status)
    if not mark:
        return title
    return f"{title[:-1]}, {mark})" if title.endswith(")") else f"{title} ({mark})"


# Mu3Lab's global filter: Open WebUI function IDs allow letters, digits and underscores only.
FILTER_ID = "mu3lab_status"
FILTER_SOURCE = Path(__file__).with_name("openwebui_filter.py")


class ChatError(RuntimeError):
    """A safe message for the dashboard or worker."""


# Readable by every Open WebUI user; ContextForge decides what each person may do.
EVERYONE = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]
# A tool connection with no grants is Open WebUI's own "administrators only" (has_connection_access);
# an administrators-only assistant is granted to each administrator by name instead, because
# administrators do not see everything here (BYPASS_ADMIN_ACCESS_CONTROL is off).
ADMINS_ONLY: list[dict[str, str]] = []


def grants_for(user_ids: list[str]) -> list[dict[str, str]]:
    return [{"principal_type": "user", "principal_id": uid, "permission": "read"} for uid in sorted(user_ids)]


def operators_only(item: dict[str, Any]) -> bool:
    return item.get("audience") == "operators"


class OpenWebUI:
    def __init__(self, origin: str, key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.client = httpx.Client(
            base_url=origin.rstrip("/"),
            timeout=30,
            transport=transport,
            headers={"Authorization": f"Bearer {key}"},
        )

    def __enter__(self) -> OpenWebUI:
        return self

    def __exit__(self, *_args: object) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
            data = response.json() if response.content else None
        except (httpx.HTTPError, ValueError):
            raise ChatError("Open WebUI could not be reached.") from None
        if not response.is_success:
            raise ChatError(f"Open WebUI did not accept this request (HTTP {response.status_code}).")
        return data

    def users(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/api/v1/users/all")
        return list((data or {}).get("users") or []) if isinstance(data, dict) else list(data or [])

    def admin_ids(self) -> list[str]:
        """Open WebUI's administrators: Authentik's operators, as of their last sign-in (OAUTH_ADMIN_ROLES)."""
        return [str(user["id"]) for user in self.users() if user.get("role") == "admin" and user.get("id")]

    def connections(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/api/v1/configs/tool_servers") or {}
        return list(data.get("TOOL_SERVER_CONNECTIONS") or [])

    def set_connections(self, connections: list[dict[str, Any]]) -> None:
        self.request("POST", "/api/v1/configs/tool_servers", json={"TOOL_SERVER_CONNECTIONS": connections})

    def models(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/api/v1/models/all")
        return list(data or []) if isinstance(data, list) else list((data or {}).get("data") or [])

    def save_model(self, model: dict[str, Any], *, exists: bool) -> None:
        if not exists:
            self.request("POST", "/api/v1/models/create", json=model)
            return
        self.request("POST", "/api/v1/models/model/update", json=model)
        # Since Open WebUI 0.12 an update keeps the old sharing; who may use a model changes only here.
        access = {"id": model["id"], "name": model.get("name"), "access_grants": model.get("access_grants") or []}
        self.request("POST", "/api/v1/models/model/access/update", json=access)

    def delete_model(self, model_id: str) -> None:
        self.request("POST", "/api/v1/models/model/delete", json={"id": model_id})

    def verify(self, connection: dict[str, Any], token: str) -> list[dict[str, Any]]:
        """Open WebUI's own connection test: it connects to the MCP server and lists its tools."""
        data = self.request(
            "POST", "/api/v1/configs/tool_servers/verify", json={**connection, "auth_type": "bearer", "key": token}
        )
        return list((data or {}).get("specs") or [])

    def ensure(
        self,
        assistants: list[dict[str, Any]],
        guide: str = "",
        *,
        outside_choice: bool = False,
        local_choice: str = "",
        gateway_down: str = "",
    ) -> list[str]:
        """Make Mu3Lab's connections and assistants exactly ``assistants``; returns their app IDs.

        ``gateway_down`` names ContextForge while it is stopped, so the main assistant can say so.
        """
        wanted_connections: list[dict[str, Any]] = []
        wanted_models: dict[str, dict[str, Any]] = {}
        admins = grants_for(self.admin_ids()) if any(operators_only(item) for item in assistants) else []
        # A private app's assistant belongs to one person, granted to their Open WebUI account by ID.
        accounts = (
            {str(user.get("email") or "").lower(): str(user["id"]) for user in self.users() if user.get("id")}
            if any(item.get("person") for item in assistants)
            else {}
        )
        for item in assistants:
            server_id = connection_id(item["id"])
            model_id = f"{PREFIX}{item['id']}"
            # Out of reach: the tool connection is switched off; the assistant stays and says why.
            active = bool(item.get("active", True))
            restricted = operators_only(item)
            if item.get("person"):
                account = accounts.get(str(item["person"]).lower())
                if not account:
                    continue  # they have not opened Open WebUI yet; the next sync adds it
                personal = grants_for([account])
            wanted_connections.append(
                {
                    "url": item["url"],
                    "path": "",
                    "type": "mcp",
                    # Each person's own Authentik sign-in, which ContextForge trusts.
                    "auth_type": "system_oauth",
                    "key": "",
                    "config": {
                        "enable": active,
                        "access_grants": personal if item.get("person") else ADMINS_ONLY if restricted else EVERYONE,
                    },
                    "info": {"id": server_id, "name": item["title"], "description": item["description"]},
                }
            )
            if item.get("tools_only"):
                continue  # the main assistant carries these (main_model)
            wanted_models[model_id] = {
                "id": model_id,
                "base_model_id": BASE_MODEL,
                "name": menu_name(item["title"], str(item.get("status") or "ready")),
                "meta": {
                    "description": item["description"],
                    "toolIds": [tool_id(item["id"])],
                    "capabilities": {"function_calling": True},
                },
                "params": {"system": item["instructions"], "function_calling": "native"},
                "access_grants": personal if item.get("person") else admins if restricted else EVERYONE,
                # Always in the menu: a hidden one is "Model not found" in its old chats.
                "is_active": True,
            }

        def ours(identifier: str) -> bool:
            return identifier.startswith(PREFIX)

        def our_connection(identifier: str) -> bool:
            # Connections made before the short names carried the assistants' long prefix.
            return identifier.startswith((CONNECTION_PREFIX, PREFIX))

        current = self.connections()
        others = [item for item in current if not our_connection(str((item.get("info") or {}).get("id") or ""))]
        self.set_connections([*others, *wanted_connections])
        existing = {str(model.get("id")) for model in self.models()}
        # The main assistant is everyone's, so it names only the household's assistants, each app once
        # (a ready one first), each with whether it can reach its app now.
        listed: dict[str, dict[str, Any]] = {}
        for item in assistants:
            if operators_only(item) or item.get("tools_only"):
                continue
            if item["title"] not in listed or item.get("active"):
                listed[item["title"]] = item
        # Each person's own calendar connection; Open WebUI skips those a person is not granted.
        tool_ids = [
            tool_id(item["id"])
            for item in assistants
            if item.get("tools_only") and connection_id(item["id"]) in {c["info"]["id"] for c in wanted_connections}
        ]
        self.save_model(
            main_model(list(listed.values()), guide, tool_ids=tool_ids, gateway_down=gateway_down),
            exists=MAIN in existing,
        )
        for model_id in HIDDEN:
            self.save_model(hidden_model(model_id), exists=model_id in existing)
        # The second-ranked source is a choice in the model menu, named for what it does.
        self.save_model(outside_model() if outside_choice else hidden_model(OUTSIDE), exists=OUTSIDE in existing)
        self.save_model(local_model(local_choice) if local_choice else hidden_model(LOCAL), exists=LOCAL in existing)
        for model_id, model in wanted_models.items():
            self.save_model(model, exists=model_id in existing)
        for model_id in existing:
            if ours(model_id) and model_id not in wanted_models:
                self.delete_model(model_id)
        return [item["id"] for item in assistants if f"{PREFIX}{item['id']}" in wanted_models]

    def check(
        self, assistants: list[dict[str, Any]], token: str, *, gateway: str = "ContextForge"
    ) -> dict[str, dict[str, Any]]:
        """Read back what Open WebUI holds for each assistant and, for those switched on, have it list their tools.

        ``token`` is how Open WebUI signs in to ContextForge (``gateway``) for the test, in place of a person's sign-in.
        Returns each app's ``status`` (ready, paused or failed), ``detail`` and ``tools`` (how many answered).
        """
        connections = {str((item.get("info") or {}).get("id") or ""): item for item in self.connections()}
        models = {str(model.get("id")): model for model in self.models()}
        results = {}
        for item in assistants:
            server_id = connection_id(item["id"])
            active = bool(item.get("active", True))
            connection, model = connections.get(server_id), models.get(f"{PREFIX}{item['id']}")
            result = {"status": "failed", "tools": 0}
            if item.get("tools_only"):
                # Tools the main assistant carries: no menu entry of their own to check.
                model = model or {"meta": {"toolIds": [f"server:mcp:{server_id}"]}, "is_active": True}
            if (connection is None or model is None) and item.get("person"):
                result |= {"status": "pending", "detail": "Added once you have opened Open WebUI."}
            elif connection is None or model is None:
                missing = "tool connection" if connection is None else "assistant"
                result["detail"] = f"Open WebUI does not have this app's {missing}."
            elif connection.get("url") != item["url"] or (model.get("meta") or {}).get("toolIds") != [
                f"server:mcp:{server_id}"
            ]:
                result["detail"] = "Open WebUI's assistant does not point at this app's tools."
            elif operators_only(item) and (
                (connection.get("config") or {}).get("access_grants") or _public(model.get("access_grants"))
            ):
                result["detail"] = "Open WebUI offers this administrators-only assistant to everyone."
            elif bool((connection.get("config") or {}).get("enable")) != active:
                result["detail"] = f"Open WebUI still has this assistant's tools switched {'off' if active else 'on'}."
            elif not model.get("is_active"):
                result["detail"] = "Open WebUI hides this assistant from the model menu."
            elif not active:
                cause, _fix = agent_context.out_of_reach(
                    str(item.get("app_name") or item["title"]),
                    str(item.get("status") or "stopped"),
                    gateway=gateway,
                    connector_state=str(item.get("connector_state") or ""),
                )
                result |= {"status": "paused", "detail": f"In chat, marked out of reach: {cause}."}
            else:
                try:
                    tools = self.verify(connection, token)
                except ChatError:
                    result["detail"] = "Open WebUI could not reach this app's tools through ContextForge."
                else:
                    if tools:
                        result |= {
                            "status": "ready",
                            "tools": len(tools),
                            "detail": f"Open WebUI reached {len(tools)} tools.",
                        }
                    else:
                        result["detail"] = (
                            "Open WebUI reached this app's tools, but none are switched on in ContextForge."
                        )
            results[item["id"]] = result
        return results

    def ensure_status_filter(self, apps: dict[str, dict[str, Any]]) -> None:
        """Install Mu3Lab's status filter, switched on for every model, and tell it about each tool connection.

        ``apps`` maps each connection's ID to ``agent_context.filter_entry``.
        """
        import json

        content = FILTER_SOURCE.read_text()
        form = {
            "id": FILTER_ID,
            "name": "Mu3Lab app status",
            "content": content,
            "meta": {"description": "Says when an app's tools did not connect for a message. Managed by Mu3Lab."},
        }
        installed = {str(item.get("id")) for item in self.request("GET", "/api/v1/functions/") or []}
        if FILTER_ID not in installed:
            self.request("POST", "/api/v1/functions/create", json=form)
        current = self.request("GET", f"/api/v1/functions/id/{FILTER_ID}") or {}
        if current.get("content") != content:
            self.request("POST", f"/api/v1/functions/id/{FILTER_ID}/update", json=form)
        if not current.get("is_active"):
            self.request("POST", f"/api/v1/functions/id/{FILTER_ID}/toggle")
        if not current.get("is_global"):
            self.request("POST", f"/api/v1/functions/id/{FILTER_ID}/toggle/global")
        self.request(
            "POST", f"/api/v1/functions/id/{FILTER_ID}/valves/update", json={"priority": 0, "apps": json.dumps(apps)}
        )


def _public(grants: Any) -> bool:
    """Whether a model's grants let every Open WebUI user read it."""
    return any(
        isinstance(grant, dict) and grant.get("principal_type") == "user" and grant.get("principal_id") == "*"
        for grant in grants or []
    )


def outside_model() -> dict[str, Any]:
    """Outside providers as a chat choice beside the local model (local first)."""
    return {
        "id": OUTSIDE,
        "base_model_id": None,
        "name": "Outside providers",
        "meta": {
            "description": "For harder questions: sends your messages to the outside AI providers set up in Mu3Lab.",
        },
        "params": {},
        "access_grants": EVERYONE,
        "is_active": True,
    }


def local_model(name: str) -> dict[str, Any]:
    """The local model as a private chat choice beside outside providers (outside first)."""
    return {
        "id": LOCAL,
        "base_model_id": None,
        "name": f"{name} (private, on this server)",
        "meta": {
            "description": "Answers on this server: nothing you send leaves it. Slower and less capable than outside "
            "providers.",
        },
        "params": {},
        "access_grants": EVERYONE,
        "is_active": True,
    }


def hidden_model(model_id: str) -> dict[str, Any]:
    """Settings for one of the gateway's own models that keep it out of the model menu."""
    return {
        "id": model_id,
        "base_model_id": None,
        "name": model_id,
        "meta": {"hidden": True},
        "params": {},
        "access_grants": EVERYONE,
        "is_active": True,
    }


def _listing(item: dict[str, Any], gateway_down: str) -> str:
    """One app assistant for the main assistant, with whether it can reach its app now."""
    status = str(item.get("status") or "ready")
    line = f"- {menu_name(item['title'], status)}: {item['description']}"
    if status == "ready" or (status == "gateway_down" and gateway_down):
        return line  # ContextForge being down is said once, below the list
    cause, fix = agent_context.out_of_reach(
        str(item.get("app_name") or item["title"]),
        status,
        gateway=gateway_down or "ContextForge",
        connector_state=str(item.get("connector_state") or ""),
    )
    return f"{line} Out of reach right now: {cause}. {fix}"


def main_model(
    assistants: list[dict[str, Any]],
    guide: str = "",
    *,
    tool_ids: list[str] | None = None,
    gateway_down: str = "",
) -> dict[str, Any]:
    """The main assistant, told which app assistants exist, whether each can reach its app now, and what every
    installed app is for.

    ``guide`` (``agent_context.household_guide``) lets it answer questions about the apps themselves,
    such as what one is for or how to get it on a phone, without sending people elsewhere.
    ``gateway_down`` names ContextForge while it is stopped: then no assistant reaches its app.
    """
    if assistants:
        listing = "\n".join(_listing(item, gateway_down) for item in assistants)
        offline = (
            f"\n\nRight now {gateway_down}, the part of Mu3Lab that connects chat to the household's apps, is "
            f"stopped, so none of these assistants can reach its app{', and your calendar tools are offline' if tool_ids else ''}. "
            "Say so plainly whenever someone asks for anything inside an app. "
            f"{agent_context.start_in_dashboard(gateway_down)} {agent_context.who_fixes(None)}"
            if gateway_down
            else ""
        )
        apps = (
            "These app assistants can read and change data in their app:\n"
            f"{listing}{offline}\n\n"
            "When a request needs one of these apps, name the assistant to use and tell the person to type @ "
            "followed by its name in the message box, or to choose it from the model menu at the top of the chat. "
            "When its app is out of reach, say so first, with how to fix it."
        )
    else:
        apps = (
            "No app assistants are available yet. When someone asks about their apps, explain that an "
            "administrator can install apps from the Mu3Lab dashboard (Apps, then Discover); apps with chat "
            "support then get their own assistant here."
        )
    household = (
        "\n\nThe household's installed apps (all private to this server; reach them on devices connected to the "
        f"household's Tailscale network, signing in with the Mu3Lab account):\n{guide}\n\n"
        "Answer questions about what these apps are for, which to use, or how to open them or get them on a "
        "phone yourself. Anything that needs the person's data in an app goes to that app's assistant. Do not "
        "guess at who else can see something, such as administrators: what an administrator can see differs "
        "by app, and each app's assistant knows."
        if guide
        else ""
    )
    calendar = (
        "\n\nCalendar: you have tools for the person's own Mu3Lab calendar, the same one the Mu3Lab dashboard, "
        "Nextcloud and their phone show. List events before changing one, use the IDs the tools return, give times "
        "in the person's time zone, and ask before moving or deleting an event. If you have no calendar tools in "
        "this chat, they did not connect: say so plainly, and never answer from memory."
        if tool_ids
        else ""
    )
    instructions = (
        f"{agent_context.NOW}\n\n"
        "You are Mu3Lab, this household's general assistant. You can help with general questions, writing and "
        "planning. Apart from the calendar, you have no app tools: you cannot see or change anything inside the "
        f"household's apps, so never claim to have done so.\n\n{apps}{household}{calendar}\n\n"
        "People manage Mu3Lab itself (apps, people, AI settings, backups) in the Mu3Lab dashboard."
    )
    return {
        "id": MAIN,
        "base_model_id": BASE_MODEL,
        "name": "Mu3Lab",
        "meta": {
            "description": "General help, your calendar, and directions to the right app assistant.",
            "capabilities": {"function_calling": True},
            **({"toolIds": tool_ids} if tool_ids else {}),
        },
        "params": {"system": instructions, **({"function_calling": "native"} if tool_ids else {})},
        "access_grants": EVERYONE,
        "is_active": True,
    }
