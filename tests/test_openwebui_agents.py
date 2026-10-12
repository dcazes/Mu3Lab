"""Open WebUI gets each operator's app assistants, private to them, through Mu3Lab's administrator key."""

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
    "url": "http://mcp-gateway:8080/apps/mealie/mcp",
    "token": "person-token",
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
    def test_each_person_gets_a_private_connection_and_assistant_and_others_are_left_alone(self):
        fake = FakeOpenWebUI()
        with OpenWebUI("http://owui", "sk-key", transport=httpx.MockTransport(fake.handler)) as api:
            managed = api.ensure({"u1": [ASSISTANT]})
        self.assertEqual(managed, {"u1": ["mealie"]})
        ids = [item["info"]["id"] for item in fake.connections]
        # The owner's own connection stays; the departed person's is gone.
        self.assertEqual(ids, ["owners-own", "mu3lab-app-mealie-u1"])
        ours = fake.connections[1]
        self.assertEqual((ours["type"], ours["auth_type"], ours["key"]), ("mcp", "bearer", "person-token"))
        grant = [{"principal_type": "user", "principal_id": "u1", "permission": "read"}]
        self.assertEqual(ours["config"]["access_grants"], grant)
        model = fake.models["mu3lab-app-mealie-u1"]
        self.assertEqual(model["meta"]["toolIds"], ["server:mcp:mu3lab-app-mealie-u1"])
        self.assertEqual(model["params"]["system"], "Help with recipes.")
        self.assertEqual(model["access_grants"], grant)
        self.assertNotIn("mu3lab-app-grocy-gone-user", fake.models)
        self.assertIn("someone-elses", fake.models)
        self.assertTrue(all(auth == "Bearer sk-key" for _method, _path, auth in fake.calls))


class SyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.paths = RuntimePaths(Path(temp.name))
        self.fake = FakeOpenWebUI()
        people = {
            "owner-uid": {"email": "owner@example.com", "operator": True},
            "kid-uid": {"email": "kid@example.com", "operator": False},
        }
        for target, value in (
            ("ctl.openwebui_ops.chat_people", {"return_value": people}),
            ("ctl.openwebui_ops.mcp_gateway.authority", {"return_value": MagicMock()}),
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

    def test_operators_with_an_account_get_assistants_and_members_get_none(self):
        ok, _detail = openwebui_ops.sync_agents(lambda _line: None)
        self.assertTrue(ok)
        self.assertEqual(records.get(openwebui_ops.SCOPE, "owner-uid", self.paths)["managed"], ["mealie"])
        # The member never signed in to Open WebUI and is no operator: nothing to add.
        self.assertEqual(records.get(openwebui_ops.SCOPE, "kid-uid", self.paths)["managed"], [])
        self.assertEqual([item["info"]["id"] for item in self.fake.connections][-1], "mu3lab-app-mealie-u1")


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
