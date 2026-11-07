"""
title: Mu3Lab app status
author: Mu3Lab
description: Tells the assistant, and the person, when an app's tools did not connect for a message.
version: 1
"""

# Mu3Lab installs this file in Open WebUI as a global filter (ctl.integrations.openwebui,
# ensure_status_filter) and rewrites its ``apps`` valve on every chat sync. It runs inside
# Open WebUI, so it imports only what Open WebUI ships.
#
# Its ``request`` step runs for every message after Open WebUI has tried to connect the
# assistant's tools and before the model is called. For each Mu3Lab tool connection the
# assistant should have but that did not connect (its app is stopped, ContextForge is down,
# or the connection failed just now), it adds a note to the system message and shows the
# person one status line. It never raises: a fault here must never stop a chat.

import json
from typing import Any

from pydantic import BaseModel, Field

PREFIX = "server:mcp:"
# Marks the note, so a second pass over the same message (the tool loop) adds nothing twice.
MARK = "[Mu3Lab, checked for this message]"
# Messages whose status line was already shown; a bounded memory.
SEEN_LIMIT = 500


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Order among filters.")
        apps: str = Field(
            default="{}",
            description="Managed by Mu3Lab: each app tool connection's name and what to say when it is out of reach.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()
        self.seen: list[str] = []

    async def request(
        self,
        body: dict,
        __user__: dict | None = None,
        __model__: dict | None = None,
        __metadata__: dict | None = None,
        __event_emitter__: Any = None,
    ) -> dict:
        try:
            missing = self.missing(body, __user__ or {}, __model__ or {}, __metadata__)
            if missing and self.add_note(body, missing, __user__ or {}):
                await self.announce(missing, __metadata__ or body.get("metadata") or {}, __event_emitter__)
        except Exception:
            pass  # never stop a chat over a status note
        return body

    def entries(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self.valves.apps or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def missing(self, body: dict, user: dict, model: dict, metadata: dict | None) -> list[dict[str, str]]:
        """Mu3Lab tool connections this assistant should have for this person that did not connect."""
        metadata = metadata if isinstance(metadata, dict) else body.get("metadata") or {}
        meta = (model.get("info") or {}).get("meta") or {}
        wanted = [*(meta.get("toolIds") or []), *(metadata.get("tool_ids") or [])]
        connected = set(metadata.get("mcp_clients") or {})
        email = str(user.get("email") or "").lower()
        entries = self.entries()
        found = []
        for tool_id in dict.fromkeys(str(item) for item in wanted):
            if not tool_id.startswith(PREFIX):
                continue
            server_id = tool_id[len(PREFIX) :]
            entry = entries.get(server_id)
            if not isinstance(entry, dict) or server_id in connected:
                continue
            # Someone else's own connection (their calendar, their private app) is not this person's to miss.
            if entry.get("person") and str(entry["person"]).lower() != email:
                continue
            found.append(entry)
        return found

    def add_note(self, body: dict, missing: list[dict[str, str]], user: dict) -> bool:
        """Tell the model; False when this message already has the note."""
        messages = body.get("messages")
        if not isinstance(messages, list):
            return False
        if user.get("role") == "admin":
            who = "The person you are talking to is an administrator, so they can do this themselves."
        else:
            who = "The person you are talking to is not an administrator, so they need to ask one."
        note = "\n".join(
            [
                MARK,
                *(str(entry.get("note") or "") for entry in missing),
                who,
                "Tell the person this first, in one or two sentences of your own, then help with anything that "
                "does not need it. Never guess at what is in it, and never use another tool in its place.",
            ]
        )
        system = next((item for item in messages if isinstance(item, dict) and item.get("role") == "system"), None)
        if system is None:
            messages.insert(0, {"role": "system", "content": note})
            return True
        content = system.get("content")
        if isinstance(content, str):
            if MARK in content:
                return False
            system["content"] = f"{content}\n\n{note}" if content else note
            return True
        if isinstance(content, list):
            if any(isinstance(part, dict) and MARK in str(part.get("text") or "") for part in content):
                return False
            content.append({"type": "text", "text": note})
            return True
        return False

    async def announce(self, missing: list[dict[str, str]], metadata: dict, emit: Any) -> None:
        """Show the person one status line per message."""
        if emit is None:
            return
        message = str(metadata.get("message_id") or "")
        if message and message in self.seen:
            return
        if message:
            self.seen = [*self.seen[-(SEEN_LIMIT - 1) :], message]
        lines = [str(entry.get("line") or "") for entry in missing if entry.get("line")]
        if lines:
            await emit({"type": "status", "data": {"description": " ".join(lines), "done": True}})
