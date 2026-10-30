"""The voice assistant's app tools: each person's own, the same ones chat gives them.

The voice agent (``apps/livekit/voice-agent``) runs a main assistant that hands the conversation
to an app's assistant, which has only that app's tools and instructions, and hands it back when
the person is done. Who is talking comes from their Authentik sign-in on the voice page.

The agent cannot reach the control plane, so Mu3Lab writes one file into the voice app's folder,
mounted read-only into the agent: for each person, their app assistants as chat has them
(``chat_ops.desired_assistants``), each with its instructions, its ContextForge address, that
person's own key, and which of its tools only read. Every other tool changes something, and the
agent runs it only right after the person says yes. An administrator switches an app's voice
assistant off in Settings, Voice assistant (``switches``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ctl.runtime import RuntimePaths
from ctl.store import records

Log = Callable[[str], None]
CAPABILITY = "voice_assistant"
# In the voice app's folder; the agent mounts it at /app/voice-tools.
FOLDER = "voice-tools"
FILE = "people.json"
# Apps an administrator switched off for voice: {app_id: False}. Missing means on.
SWITCHES = ("voice", "apps")
# The calendar's tools (``ctl/calendar_tools.py``) have no review; these are the ones that only read.
CALENDAR_READS = ("list_events",)
VOICE_RULES = (
    "You are speaking with the person, not writing: answer in one to three short, plain sentences that sound "
    "natural read aloud, with no lists, markdown, links or emoji. Say times and numbers the way people say them. "
    "Before any tool that adds, changes or deletes something, say exactly what you will do and ask the person to "
    "say yes; the tool refuses to run until they have. Where the instructions below mention typing, menus or "
    "choosing another assistant in chat, instead call back_to_main, and the main assistant hands the person to "
    "the right one. Call back_to_main too when the person is done here or asks about something else."
)


def app():
    from ctl.platform_apps import by_capability

    return by_capability(CAPABILITY)


def switches(paths: RuntimePaths | None = None) -> dict[str, bool]:
    return {str(key): bool(value) for key, value in records.get(*SWITCHES, paths or RuntimePaths()).items()}


def set_switch(app_id: str, on: bool, paths: RuntimePaths | None = None) -> None:
    paths = paths or RuntimePaths()
    saved = switches(paths)
    if on:
        saved.pop(app_id, None)
    else:
        saved[app_id] = False
    records.put(*SWITCHES, saved, paths)


def available() -> list[dict[str, Any]]:
    """Every app assistant voice could hand people to, each with its switch."""
    from ctl import chat_ops

    off = {app_id for app_id, on in switches().items() if not on}
    found: dict[str, dict[str, Any]] = {}
    for item in chat_ops.desired_assistants():
        app_id = str(item["id"]).split("--")[0]
        found.setdefault(
            app_id,
            {
                "id": app_id,
                "title": str(item["title"]),
                "description": str(item["description"]),
                "operators_only": item.get("audience") == "operators",
                "on": app_id not in off,
                # Its connector is live; a stopped app's assistant waits, switched off, until it starts.
                "ready": bool(item.get("active")),
            },
        )
    return sorted(found.values(), key=lambda item: item["title"].casefold())


def _reads() -> dict[str, list[str]]:
    """Per app, the tools its review marks as read-only, as ContextForge names their ends."""
    from ctl.mcp_catalog import load as load_connectors
    from ctl.mcp_review import load as load_review
    from ctl.registry import load as load_registry

    found: dict[str, list[str]] = {"calendar": list(CALENDAR_READS)}
    for connector in load_connectors(load_registry()):
        if not connector.review:
            continue
        try:
            review = load_review(connector.id, connector.review)
        except (OSError, ValueError):
            continue
        found.setdefault(connector.service_id, []).extend(
            name for name, tool in review.tools.items() if tool.access == "read"
        )
    return found


def _slug(name: str) -> str:
    """How ContextForge ends a tool's name: ``list_events`` becomes ``...-list-events``."""
    return name.replace("_", "-").lower()


def build(log: Log) -> dict[str, Any]:
    """Everyone's voice assistants, keyed by email; raises PeopleError when people cannot be read."""
    from ctl import chat_ops
    from ctl.contextforge import ContextForgeError
    from ctl.openwebui_ops import _guide
    from ctl.people import list_people

    off = {app_id for app_id, on in switches().items() if not on}
    reads = _reads()
    people: dict[str, Any] = {}
    every_app: set[str] = set()
    for person in list_people():
        email = str(person.get("email") or "").lower()
        if not person.get("active") or person.get("role") not in {"admin", "member"} or not email:
            continue
        agents = []
        try:
            desired = chat_ops.desired_assistants(str(person["uid"]), email, operator=person["role"] == "admin")
        except (ContextForgeError, OSError, ValueError) as exc:
            # One person's failed key must not take everyone else's tools away.
            log(f"{person.get('name') or email}'s voice tools were not updated: {exc}")
            desired = []
        for item in desired:
            app_id = str(item["id"]).split("--")[0]
            if not item.get("active") or not item.get("token") or app_id in off:
                continue
            every_app.add(app_id)
            agents.append(
                {
                    "id": app_id,
                    "title": str(item["title"]),
                    "description": str(item["description"]),
                    "instructions": f"{VOICE_RULES}\n\n{item['instructions']}",
                    "url": str(item["url"]),
                    "token": str(item["token"]),
                    "reads": [_slug(name) for name in reads.get(app_id, [])],
                }
            )
        people[email] = {"name": str(person.get("name") or email.split("@")[0]), "agents": agents}
    return {"guide": _guide(every_app), "people": people}


def folder(paths: RuntimePaths | None = None) -> Path:
    return (paths or RuntimePaths()).projects / app().id / FOLDER


def sync(log: Log) -> bool:
    """Write everyone's voice assistants, and the household's settings, for the agent, which reads
    both files at each call's start.

    Returns whether the people file changed. Does nothing until the voice app is installed.
    """
    from ctl import chat_ops, voice_settings
    from ctl.contextforge import ContextForgeError
    from ctl.people import PeopleError

    target = folder()
    if not chat_ops.installed(app().id) or not target.parent.is_dir():
        return False
    # The household's choices first: they need no people, and a new SearXNG install turns search on.
    voice_settings.write(log, RuntimePaths())
    try:
        data = build(log)
    except (PeopleError, ContextForgeError, OSError, ValueError) as exc:
        log(f"The voice assistant's app tools were not updated: {exc}")
        return False
    text = json.dumps(data, indent=1, sort_keys=True)
    path = target / FILE
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        # Unchanged, but the agent must still be able to read it (an older restore made it private).
        os.chmod(target, 0o755)
        os.chmod(path, 0o644)
        return False
    # The agent runs as its own user. The folder above is the owner's alone (0750), so only
    # the agent's read-only mount and the owner can reach the keys inside.
    target.mkdir(mode=0o755, exist_ok=True)
    os.chmod(target, 0o755)
    partial = target / f".{FILE}.partial"
    partial.write_text(text, encoding="utf-8")
    os.chmod(partial, 0o644)
    partial.replace(path)
    log("The voice assistant's app tools were updated.")
    return True
