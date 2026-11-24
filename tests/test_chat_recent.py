"""The Ask widget's recent chats: each person's own, newest first, titles and times only."""

from __future__ import annotations

import json
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from ctl import chat_ops, chat_recent


def app(workspace: str):
    return next(item for item in chat_ops.chat_apps() if item.manifest.chat.workspace == workspace)


class RecentChatTests(unittest.TestCase):
    def setUp(self):
        chat_recent._kept.clear()
        for target, value in ((chat_ops, "installed"), (chat_ops, "origin")):
            patcher = patch.object(
                target, value, return_value=True if value == "installed" else "https://box.example:8472"
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_librechat_asks_its_database_for_this_person_only(self):
        api = MagicMock()
        api.users.return_value = [{"id": "6b038e4927ac0a8a1247cb63", "email": "Sam@Example.com"}]
        rows = [{"id": "c-1", "title": "Dinner plans", "at": 1795540744.8}]
        with (
            patch("ctl.librechat_ops.client", return_value=nullcontext(api)),
            patch("ctl.actions.compose_exec", return_value=(0, "noise\n" + json.dumps(rows))) as run,
        ):
            chats = chat_recent.recent(app("libre"), uid="u1", email="sam@example.com")
        self.assertEqual(
            chats, [{"title": "Dinner plans", "url": "https://box.example:8472/c/c-1", "updated_at": 1795540744.8}]
        )
        script = run.call_args.args[2][-1]
        self.assertIn('{user: "6b038e4927ac0a8a1247cb63"', script)

    def test_librechat_never_builds_a_query_from_an_odd_id(self):
        api = MagicMock()
        api.users.return_value = [{"id": '"}); db.dropDatabase(); ({"', "email": "sam@example.com"}]
        with (
            patch("ctl.librechat_ops.client", return_value=nullcontext(api)),
            patch("ctl.actions.compose_exec", side_effect=AssertionError("must not run")),
        ):
            self.assertEqual(chat_recent.recent(app("libre"), uid="u1", email="sam@example.com"), [])

    def test_lobechat_lists_newest_first_with_the_persons_own_key(self):
        topics = {
            "topics": [
                {"id": "t-old", "title": "Old", "agentId": "a1", "updatedAt": "2026-11-01T10:00:00Z"},
                {"id": "t-new", "title": "New", "agentId": None, "updatedAt": "2026-11-24T10:00:00Z"},
            ]
        }
        client = MagicMock()
        client.__enter__.return_value.request.return_value = topics
        with (
            patch("ctl.chat_connections.records", return_value={"u1": {"key": "person-key"}}),
            patch("ctl.integrations.lobehub.LobeHub", return_value=client) as lobe,
        ):
            chats = chat_recent.recent(app("lobe"), uid="u1", email="sam@example.com")
        self.assertEqual(lobe.call_args.args[1], "person-key")
        self.assertEqual([chat["title"] for chat in chats], ["New", "Old"])
        self.assertEqual(chats[1]["url"], "https://box.example:8472/agent/a1?topic=t-old")
        with patch("ctl.chat_connections.records", return_value={}), self.assertRaises(chat_recent.RecentError):
            chat_recent.recent(app("lobe"), uid="u2", email="alex@example.com")


if __name__ == "__main__":
    unittest.main()
