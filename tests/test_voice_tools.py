"""The voice assistant's app tools: each person's own, with read-only tools named (ctl/voice_tools.py)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import voice_tools
from ctl.runtime import RuntimePaths

PEOPLE = [
    {"uid": "1", "email": "Owner@Example.com", "name": "Owner", "role": "admin", "active": True},
    {"uid": "2", "email": "kid@example.com", "name": "Kid", "role": "member", "active": True},
    {"uid": "3", "email": "gone@example.com", "name": "Gone", "role": "member", "active": False},
]


def desired(uid: str = "", email: str = "", *, operator: bool = False) -> list[dict]:
    """Chat's assistants for one person, as chat_ops.desired_assistants gives them."""
    items = [
        {"id": "beaver-habits", "title": "Beaver Habits", "description": "Habits.", "instructions": "Habit rules.",
         "url": "http://contextforge:4444/servers/b/mcp", "active": True, "audience": "household"},
        {"id": "calendar", "title": "Calendar", "description": "Your calendar.", "instructions": "Calendar rules.",
         "url": "http://contextforge:4444/servers/c/mcp", "active": True, "audience": "household"},
        {"id": "kopia-ui", "title": "Backups", "description": "Backups.", "instructions": "Backup rules.",
         "url": "http://contextforge:4444/servers/k/mcp", "active": True, "audience": "operators"},
        {"id": "immich", "title": "Photos", "description": "Photos.", "instructions": "Photo rules.",
         "url": "http://contextforge:4444/servers/i/mcp", "active": False, "audience": "household"},
    ]  # fmt: skip
    if uid and not operator:
        items = [item for item in items if item["audience"] == "household"]
    return [item | ({"token": f"key-{uid}-{item['id']}"} if uid and item["active"] else {}) for item in items]


class VoiceToolsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = RuntimePaths(Path(tmp.name))
        self.paths.state.mkdir(parents=True)
        for patcher in (
            patch("ctl.people.list_people", return_value=PEOPLE),
            patch("ctl.chat_ops.desired_assistants", side_effect=desired),
            patch("ctl.openwebui_ops._guide", return_value="- Beaver Habits: habits."),
            patch(
                "ctl.voice_tools._reads", return_value={"calendar": ["list_events"], "beaver-habits": ["list_habits"]}
            ),
            patch("ctl.voice_tools.RuntimePaths", return_value=self.paths),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_each_person_gets_their_own_live_assistants_and_keys(self):
        data = voice_tools.build(lambda _line: None)
        people = data["people"]
        self.assertEqual(set(people), {"owner@example.com", "kid@example.com"})
        owner = {agent["id"]: agent for agent in people["owner@example.com"]["agents"]}
        kid = {agent["id"]: agent for agent in people["kid@example.com"]["agents"]}
        # Administrators-only assistants reach administrators; stopped apps reach nobody.
        self.assertEqual(set(owner), {"beaver-habits", "calendar", "kopia-ui"})
        self.assertEqual(set(kid), {"beaver-habits", "calendar"})
        self.assertEqual(kid["calendar"]["token"], "key-2-calendar")
        # Read-only tools as ContextForge ends their names; everything else needs a spoken yes.
        self.assertEqual(kid["calendar"]["reads"], ["list-events"])
        self.assertEqual(owner["kopia-ui"]["reads"], [])
        self.assertTrue(kid["calendar"]["instructions"].startswith(voice_tools.VOICE_RULES))
        self.assertEqual(data["guide"], "- Beaver Habits: habits.")

    def test_an_app_switched_off_for_voice_is_left_out(self):
        voice_tools.set_switch("calendar", False, self.paths)
        agents = voice_tools.build(lambda _line: None)["people"]["kid@example.com"]["agents"]
        self.assertEqual([agent["id"] for agent in agents], ["beaver-habits"])
        voice_tools.set_switch("calendar", True, self.paths)
        self.assertEqual(voice_tools.switches(self.paths), {})

    def test_one_persons_failed_key_leaves_everyone_else_their_tools(self):
        from ctl.contextforge import ContextForgeError

        def failing(uid: str = "", email: str = "", *, operator: bool = False):
            if uid == "1":
                raise ContextForgeError("key refused")
            return desired(uid, email, operator=operator)

        lines: list[str] = []
        with patch("ctl.chat_ops.desired_assistants", side_effect=failing):
            people = voice_tools.build(lines.append)["people"]
        self.assertEqual(people["owner@example.com"]["agents"], [])
        self.assertEqual(len(people["kid@example.com"]["agents"]), 2)
        self.assertIn("key refused", lines[0])

    def test_the_file_is_written_for_the_agent_only_when_the_voice_app_is_installed(self):
        project = self.paths.projects / "livekit"
        app = SimpleNamespace(id="livekit")
        with patch("ctl.voice_tools.app", return_value=app), patch("ctl.chat_ops.installed", return_value=False):
            self.assertFalse(voice_tools.sync(lambda _line: None))
        project.mkdir(parents=True)
        with patch("ctl.voice_tools.app", return_value=app), patch("ctl.chat_ops.installed", return_value=True):
            self.assertTrue(voice_tools.sync(lambda _line: None))
            # Unchanged settings leave the file alone.
            self.assertFalse(voice_tools.sync(lambda _line: None))
        path = project / voice_tools.FOLDER / voice_tools.FILE
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertIn("kid@example.com", json.loads(path.read_text())["people"])


if __name__ == "__main__":
    unittest.main()
