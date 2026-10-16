"""Open WebUI gets everyone's app assistants, through Mu3Lab's administrator key, reaching ContextForge."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from ctl import chat_ops, openwebui_ops
from ctl.control_state import ControlState
from ctl.integrations.openwebui import OpenWebUI
from ctl.runtime import RuntimePaths
from ctl.store import records

ASSISTANT = {
    "id": "mealie",
    "title": "Mealie",
    "description": "Plan meals.",
    "instructions": "Help with recipes.",
    "url": "http://contextforge:4444/servers/abc123/mcp",
}


class FakeOpenWebUI:
    """Open WebUI's admin API as far as Mu3Lab uses it."""

    def __init__(self):
        self.connections = [
            {"type": "mcp", "url": "https://example.test/mcp", "info": {"id": "owners-own"}},
            {"type": "mcp", "url": "http://old", "info": {"id": "mu3lab-app-grocy-gone-user"}},
        ]
        self.models = {"mu3lab-app-grocy-gone-user": {}, "someone-elses": {}}
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.headers.get("authorization")))
        body = json.loads(request.content) if request.content else None
        if request.url.path == "/api/v1/users/all":
            return httpx.Response(200, json={"users": [{"id": "u1", "email": "Owner@Example.com"}]})
        if request.url.path == "/api/v1/configs/tool_servers":
            if request.method == "POST":
                self.connections = body["TOOL_SERVER_CONNECTIONS"]
            return httpx.Response(200, json={"TOOL_SERVER_CONNECTIONS": self.connections})
        if request.url.path == "/api/v1/models/all":
            return httpx.Response(200, json=[{"id": key} for key in self.models])
        if request.url.path in {"/api/v1/models/create", "/api/v1/models/model/update"}:
            self.models[body["id"]] = body
            return httpx.Response(200, json=body)
        if request.url.path == "/api/v1/models/model/delete":
            self.models.pop(body["id"], None)
            return httpx.Response(200, json=True)
        return httpx.Response(404, json={})


class ClientTests(unittest.TestCase):
    def test_one_shared_connection_and_assistant_per_app_and_others_are_left_alone(self):
        fake = FakeOpenWebUI()
        with OpenWebUI("http://owui", "sk-key", transport=httpx.MockTransport(fake.handler)) as api:
            managed = api.ensure([ASSISTANT])
        self.assertEqual(managed, ["mealie"])
        ids = [item["info"]["id"] for item in fake.connections]
        # The owner's own connection stays; the old per-person one is gone.
        self.assertEqual(ids, ["owners-own", "mu3lab-app-mealie"])
        ours = fake.connections[1]
        # Each person's own Authentik sign-in reaches ContextForge; Mu3Lab stores no key.
        self.assertEqual((ours["type"], ours["auth_type"], ours["key"]), ("mcp", "system_oauth", ""))
        self.assertEqual(ours["url"], "http://contextforge:4444/servers/abc123/mcp")
        everyone = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]
        self.assertEqual(ours["config"]["access_grants"], everyone)
        model = fake.models["mu3lab-app-mealie"]
        self.assertEqual(model["meta"]["toolIds"], ["server:mcp:mu3lab-app-mealie"])
        self.assertEqual(model["params"]["system"], "Help with recipes.")
        self.assertEqual(model["access_grants"], everyone)
        self.assertNotIn("mu3lab-app-grocy-gone-user", fake.models)
        self.assertIn("someone-elses", fake.models)
        self.assertTrue(all(auth == "Bearer sk-key" for _method, _path, auth in fake.calls))

    def test_the_main_assistant_has_no_tools_and_points_to_the_app_assistants(self):
        fake = FakeOpenWebUI()
        with OpenWebUI("http://owui", "sk-key", transport=httpx.MockTransport(fake.handler)) as api:
            api.ensure([ASSISTANT])
            main = fake.models["mu3lab-assistant"]
            self.assertNotIn("toolIds", main["meta"])
            self.assertIn("- Mealie: Plan meals.", main["params"]["system"])
            self.assertEqual(main["base_model_id"], "mu3lab-chat")
            # With nothing installed it still exists, and says how to get app assistants.
            api.ensure([])
        main = fake.models["mu3lab-assistant"]
        self.assertIn("No app assistants are available yet", main["params"]["system"])
        self.assertNotIn("mu3lab-app-mealie", fake.models)

    def test_the_main_assistant_is_every_new_chats_model(self):
        compose = (Path(__file__).resolve().parents[1] / "apps/open-webui/docker-compose.yml").read_text()
        self.assertIn("DEFAULT_MODELS: mu3lab-assistant", compose)


class SyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.paths = RuntimePaths(Path(temp.name))
        self.fake = FakeOpenWebUI()
        for target, value in (
            ("ctl.openwebui_ops.chat_ops.desired_assistants", {"return_value": [ASSISTANT]}),
            (
                "ctl.openwebui_ops.client",
                {
                    "side_effect": lambda: OpenWebUI(
                        "http://owui", "sk-key", transport=httpx.MockTransport(self.fake.handler)
                    )
                },
            ),
            ("ctl.openwebui_ops.RuntimePaths", {"return_value": self.paths}),
            ("ctl.resource_locks.hold", {"return_value": MagicMock()}),
        ):
            patcher = patch(target, **value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_everyone_shares_the_assistants(self):
        ok, _detail = openwebui_ops.sync_agents(lambda _line: None)
        self.assertTrue(ok)
        self.assertEqual(records.get(openwebui_ops.SCOPE, openwebui_ops.RECORD, self.paths)["managed"], ["mealie"])
        self.assertEqual([item["info"]["id"] for item in self.fake.connections][-1], "mu3lab-app-mealie")


class ChatAppChoiceTests(unittest.TestCase):
    def test_open_webui_is_the_default_and_lobechat_must_be_installed_to_choose(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "state.db")
            with patch("ctl.chat_ops.ControlState.runtime", return_value=state):
                self.assertEqual(chat_ops.selected().id, "open-webui")
                with self.assertRaises(chat_ops.ChatError):
                    chat_ops.choose("lobehub", "owner")
                state.set_installation("lobehub", "running")
                self.assertEqual(chat_ops.choose("lobehub", "owner").id, "lobehub")
                self.assertEqual(chat_ops.selected().id, "lobehub")
                self.assertTrue(chat_ops.needs_connect(chat_ops.selected()))


if __name__ == "__main__":
    unittest.main()
