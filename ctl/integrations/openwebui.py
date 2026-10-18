"""Open WebUI's admin API, used with Mu3Lab's own administrator key.

Mu3Lab keeps two things in step: the tool connections (one MCP connection per
app, pointing at that app's ContextForge virtual server and passing each
person's own Authentik sign-in) and the workspace models that act as each
app's assistant, plus the main assistant every new chat starts with. All are
shared by everyone. An app that is stopped keeps its assistant switched off
(out of the model menu, settings kept) rather than deleted; ``check`` then
asks Open WebUI itself whether each one is set up and its tools answer. It also hides the AI gateway's own names from the model
menu (``HIDDEN``). Connections and models Mu3Lab did not create are never
touched: ours carry the ``mu3lab-app-`` id prefix, or are ``mu3lab-assistant``.
API error bodies never reach logs.
"""

from __future__ import annotations

from typing import Any

import httpx

# Distinct from LiteLLM model names such as mu3lab-chat, which Open WebUI also lists.
PREFIX = "mu3lab-app-"
# The household's main assistant: no app tools, the selected model in every new chat
# (DEFAULT_MODELS in apps/open-webui/docker-compose.yml), and it points people to the app assistants.
MAIN = "mu3lab-assistant"
BASE_MODEL = "mu3lab-chat"
# LiteLLM's names, which Open WebUI would list as chat models: the main assistant is built on
# mu3lab-chat, and the rest serve Mu3Lab's own checks, document search and speech. Hidden from
# the model menu only; they keep working.
HIDDEN = ("mu3lab-chat", "mu3lab-fast", "mu3lab-local", "mu3lab-embed", "mu3lab-stt", "mu3lab-tts")


class ChatError(RuntimeError):
    """A safe message for the dashboard or worker."""


# Readable by every Open WebUI user; ContextForge decides what each person may do.
EVERYONE = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]


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

    def connections(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/api/v1/configs/tool_servers") or {}
        return list(data.get("TOOL_SERVER_CONNECTIONS") or [])

    def set_connections(self, connections: list[dict[str, Any]]) -> None:
        self.request("POST", "/api/v1/configs/tool_servers", json={"TOOL_SERVER_CONNECTIONS": connections})

    def models(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/api/v1/models/all")
        return list(data or []) if isinstance(data, list) else list((data or {}).get("data") or [])

    def save_model(self, model: dict[str, Any], *, exists: bool) -> None:
        self.request("POST", "/api/v1/models/model/update" if exists else "/api/v1/models/create", json=model)

    def delete_model(self, model_id: str) -> None:
        self.request("POST", "/api/v1/models/model/delete", json={"id": model_id})

    def verify(self, connection: dict[str, Any], token: str) -> list[dict[str, Any]]:
        """Open WebUI's own connection test: it connects to the MCP server and lists its tools."""
        data = self.request(
            "POST", "/api/v1/configs/tool_servers/verify", json={**connection, "auth_type": "bearer", "key": token}
        )
        return list((data or {}).get("specs") or [])

    def ensure(self, assistants: list[dict[str, Any]]) -> list[str]:
        """Make Mu3Lab's connections and assistants exactly ``assistants``; returns their app IDs."""
        wanted_connections: list[dict[str, Any]] = []
        wanted_models: dict[str, dict[str, Any]] = {}
        for item in assistants:
            server_id = f"{PREFIX}{item['id']}"
            # Switched off while its app is stopped: kept, but out of the menu and unreachable.
            active = bool(item.get("active", True))
            wanted_connections.append(
                {
                    "url": item["url"],
                    "path": "",
                    "type": "mcp",
                    # Each person's own Authentik sign-in, which ContextForge trusts.
                    "auth_type": "system_oauth",
                    "key": "",
                    "config": {"enable": active, "access_grants": EVERYONE},
                    "info": {"id": server_id, "name": item["title"], "description": item["description"]},
                }
            )
            wanted_models[server_id] = {
                "id": server_id,
                "base_model_id": BASE_MODEL,
                "name": item["title"],
                "meta": {
                    "description": item["description"],
                    "toolIds": [f"server:mcp:{server_id}"],
                    "capabilities": {"function_calling": True},
                },
                "params": {"system": item["instructions"], "function_calling": "native"},
                "access_grants": EVERYONE,
                "is_active": active,
            }

        def ours(identifier: str) -> bool:
            return identifier.startswith(PREFIX)

        current = self.connections()
        others = [item for item in current if not ours(str((item.get("info") or {}).get("id") or ""))]
        self.set_connections([*others, *wanted_connections])
        existing = {str(model.get("id")) for model in self.models()}
        self.save_model(main_model([item for item in assistants if item.get("active", True)]), exists=MAIN in existing)
        for model_id in HIDDEN:
            self.save_model(hidden_model(model_id), exists=model_id in existing)
        for model_id, model in wanted_models.items():
            self.save_model(model, exists=model_id in existing)
        for model_id in existing:
            if ours(model_id) and model_id not in wanted_models:
                self.delete_model(model_id)
        return [item["id"] for item in assistants]

    def check(self, assistants: list[dict[str, Any]], token: str) -> dict[str, dict[str, Any]]:
        """Read back what Open WebUI holds for each assistant and, for those switched on, have it list their tools.

        ``token`` is how Open WebUI signs in to ContextForge for the test, in place of a person's sign-in.
        Returns each app's ``status`` (ready, paused or failed), ``detail`` and ``tools`` (how many answered).
        """
        connections = {str((item.get("info") or {}).get("id") or ""): item for item in self.connections()}
        models = {str(model.get("id")): model for model in self.models()}
        results = {}
        for item in assistants:
            server_id = f"{PREFIX}{item['id']}"
            active = bool(item.get("active", True))
            connection, model = connections.get(server_id), models.get(server_id)
            result = {"status": "failed", "tools": 0}
            if connection is None or model is None:
                missing = "tool connection" if connection is None else "assistant"
                result["detail"] = f"Open WebUI does not have this app's {missing}."
            elif connection.get("url") != item["url"] or (model.get("meta") or {}).get("toolIds") != [
                f"server:mcp:{server_id}"
            ]:
                result["detail"] = "Open WebUI's assistant does not point at this app's tools."
            elif (
                bool((connection.get("config") or {}).get("enable")) != active or bool(model.get("is_active")) != active
            ):
                result["detail"] = f"Open WebUI still has this assistant switched {'off' if active else 'on'}."
            elif not active:
                result |= {"status": "paused", "detail": "Switched off in Open WebUI while the app is stopped."}
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


def main_model(assistants: list[dict[str, Any]]) -> dict[str, Any]:
    """The main assistant, told which app assistants exist so it can send people to them."""
    if assistants:
        listing = "\n".join(f"- {item['title']}: {item['description']}" for item in assistants)
        apps = (
            "These app assistants can read and change data in their app:\n"
            f"{listing}\n\n"
            "When a request needs one of these apps, name the assistant to use and tell the person to type @ "
            "followed by its name in the message box, or to choose it from the model menu at the top of the chat."
        )
    else:
        apps = (
            "No app assistants are available yet. When someone asks about their apps, explain that an "
            "administrator can install apps from the Mu3Lab dashboard (Apps, then Discover); apps with chat "
            "support then get their own assistant here."
        )
    instructions = (
        "You are Mu3Lab, this household's general assistant. You can help with general questions, writing and "
        "planning. You have no app tools: you cannot see or change anything inside the household's apps, so never "
        f"claim to have done so.\n\n{apps}"
    )
    return {
        "id": MAIN,
        "base_model_id": BASE_MODEL,
        "name": "Mu3Lab",
        "meta": {
            "description": "General help, and directions to the right app assistant.",
            "capabilities": {"function_calling": True},
        },
        "params": {"system": instructions},
        "access_grants": EVERYONE,
        "is_active": True,
    }
