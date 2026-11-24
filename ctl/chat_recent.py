"""The Ask widget's chat switch: the chat apps a person can pick, and their own recent chats in one.

Each person sees only their own chats, found by their sign-in email. They get each chat's title and
when it was last used, never what was said:

* Open WebUI: its administrator API's list of a person's chats (``/api/v1/chats/list/user/<id>``),
  with the key Mu3Lab already holds (``ctl.openwebui_ops``).
* LibreChat: its database, which only Mu3Lab reaches, since LibreChat has no API for another
  person's chats; the person's account is found through its admin API (``ctl.librechat_ops``).
* LobeChat: its own API with the key the person made when they connected chat (``chat:read``).

Lists are kept briefly, so several screens or a quick switch back do not ask the app again.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any
from urllib.parse import quote

from ctl import chat_ops
from ctl.manifest.catalog import App
from ctl.runtime import RuntimePaths

CHATS = 5
KEEP_SECONDS = 60
MONGO_ID = re.compile(r"^[a-f0-9]{24}$")


class RecentError(RuntimeError):
    """Why the chats cannot be listed now; the message is for the person."""


_kept: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_lock = threading.Lock()


def choices() -> list[App]:
    """The installed chat apps, the household's own first."""
    household = chat_ops.selected()
    apps = [app for app in chat_ops.chat_apps() if chat_ops.installed(app.id)]
    return sorted(apps, key=lambda app: (app.id != household.id, app.manifest.name))


def prompt_url(app: App, uid: str) -> str:
    """Opens the app with a question typed in ("{question}" marks the spot; see chat_ops.prompt_path)."""
    path = chat_ops.prompt_path(app, uid)
    try:
        return chat_ops.origin(app) + path if path else ""
    except chat_ops.ChatError:
        return ""


def _chat(title: Any, url: str, when: Any) -> dict[str, Any]:
    try:
        moment = float(when or 0)
    except (TypeError, ValueError):
        moment = 0.0
    return {"title": " ".join(str(title or "New chat").split())[:120], "url": url, "updated_at": moment}


def _open_webui(app: App, email: str, origin: str) -> list[dict[str, Any]]:
    from ctl import openwebui_ops
    from ctl.integrations.openwebui import ChatError

    try:
        with openwebui_ops.client() as api:
            person = next((user for user in api.users() if str(user.get("email") or "").lower() == email), None)
            if person is None:
                return []
            rows = api.request(
                "GET",
                f"/api/v1/chats/list/user/{quote(str(person['id']), safe='')}",
                params={"order_by": "updated_at", "direction": "desc"},
            )
    except ChatError as exc:
        raise RecentError(str(exc)) from None
    return [
        _chat(row.get("title"), f"{origin}/c/{quote(str(row.get('id')), safe='')}", row.get("updated_at"))
        for row in (rows or [])[:CHATS]
        if isinstance(row, dict) and row.get("id")
    ]


# LibreChat's own database, asked for one person's newest chats: titles and times only.
LIBRE_QUERY = (
    "const rows = db.conversations.find({user: %s, isArchived: {$ne: true}},"
    " {_id: 0, conversationId: 1, title: 1, updatedAt: 1}).sort({updatedAt: -1}).limit(%d).toArray();"
    " print(JSON.stringify(rows.map(r => ({id: r.conversationId, title: r.title,"
    " at: r.updatedAt ? r.updatedAt.getTime() / 1000 : 0}))));"
)


def _librechat(app: App, email: str, origin: str) -> list[dict[str, Any]]:
    from ctl import actions, librechat_ops
    from ctl.integrations.librechat import ChatError

    try:
        with librechat_ops.client() as api:
            person = next((user for user in api.users() if str(user.get("email") or "").lower() == email), None)
    except ChatError as exc:
        raise RecentError(str(exc)) from None
    user_id = str((person or {}).get("id") or "")
    if not MONGO_ID.match(user_id):
        return []
    project = RuntimePaths().projects / app.id
    code, output = actions.compose_exec(
        project,
        "mongodb",
        ["mongosh", "LibreChat", "--quiet", "--eval", LIBRE_QUERY % (json.dumps(user_id), CHATS)],
        lambda _line: None,
        timeout=30,
    )
    lines = [line for line in (output or "").splitlines() if line.startswith("[")]
    if code or not lines:
        raise RecentError(f"{app.manifest.name}'s chats could not be read just now.")
    try:
        rows = json.loads(lines[-1])
    except ValueError:
        raise RecentError(f"{app.manifest.name}'s chats could not be read just now.") from None
    return [
        _chat(row.get("title"), f"{origin}/c/{quote(str(row.get('id')), safe='')}", row.get("at"))
        for row in rows
        if isinstance(row, dict) and row.get("id")
    ]


def _lobechat(app: App, uid: str, origin: str) -> list[dict[str, Any]]:
    from ctl import chat_connections
    from ctl.integrations.lobehub import ChatError, LobeHub

    key = str((chat_connections.records().get(uid) or {}).get("key") or "")
    if not key:
        raise RecentError(f"Connect chat on the Chat page once to see your {app.manifest.name} chats here.")
    try:
        with LobeHub(f"http://127.0.0.1:{app.manifest.service.local_port}", key) as api:
            data = api.request("GET", "/api/v1/topics", params={"page": 1, "pageSize": 30}) or {}
    except ChatError as exc:
        raise RecentError(str(exc)) from None
    topics = [topic for topic in data.get("topics") or [] if isinstance(topic, dict) and topic.get("id")]

    def moment(topic: dict[str, Any]) -> float:
        text = str(topic.get("updatedAt") or topic.get("createdAt") or "")
        try:
            from datetime import datetime

            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    topics.sort(key=moment, reverse=True)
    chats = []
    for topic in topics[:CHATS]:
        agent = str(topic.get("agentId") or "")
        where = f"/agent/{quote(agent, safe='')}" if agent else "/"
        chats.append(
            _chat(topic.get("title"), f"{origin}{where}?topic={quote(str(topic['id']), safe='')}", moment(topic))
        )
    return chats


def recent(app: App, *, uid: str, email: str) -> list[dict[str, Any]]:
    """This person's newest chats in ``app``, newest first."""
    if not chat_ops.installed(app.id):
        raise RecentError(f"{app.manifest.name} isn't installed.")
    email = email.strip().lower()
    if not email or not uid:
        raise RecentError("Mu3Lab could not tell who you are; sign in again.")
    key = (app.id, uid)
    with _lock:
        hit = _kept.get(key)
        if hit and time.time() - hit[0] < KEEP_SECONDS:
            return hit[1]
    try:
        origin = chat_ops.origin(app)
    except chat_ops.ChatError as exc:
        raise RecentError(str(exc)) from None
    workspace = app.manifest.chat.workspace
    if workspace == "open_webui":
        chats = _open_webui(app, email, origin)
    elif workspace == "libre":
        chats = _librechat(app, email, origin)
    elif workspace == "lobe":
        chats = _lobechat(app, uid, origin)
    else:
        chats = []
    with _lock:
        _kept[key] = (time.time(), chats)
    return chats
