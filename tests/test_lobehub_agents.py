"""Official API upserts preserve conversations and avoid duplicate assistants.

The old SQL/trigger tests were removed together with that implementation.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import httpx
import jsonschema

from ctl import chat_connections
from ctl.integrations.lobehub import ChatError, LobeHub
from ctl.runtime import RuntimePaths

SPEC = json.loads((Path(__file__).parent / "fixtures/lobehub-openapi-2.2.18.json").read_text())
DESIRED = [
    {
        "id": "recipes",
        "title": "Recipes",
        "description": "Meals",
        "instructions": "Help cook",
        "url": "http://gateway:8810/recipes/mcp",
        "token": "gateway-secret",
    }
]


class ApiServer:
    def __init__(self):
        self.agents = []
        self.servers = []
        self.topics = []
        self.calls = []

    def handle(self, request):
        path = request.url.path
        self.calls.append((request.method, path))
        self.assert_key(request)
        body = json.loads(request.content) if request.content else None
        key = path
        if path.startswith("/api/v1/agents/"):
            key = "/api/v1/agents/{id}"
        if path.startswith("/api/v1/mcp-servers/"):
            key = "/api/v1/mcp-servers/{id}" + ("/sync" if path.endswith("/sync") else "")
        operation = SPEC["paths"][key][request.method.lower()]
        if body is not None:
            schema = operation["requestBody"]["content"]["application/json"]["schema"]
            jsonschema.Draft202012Validator(schema).validate(body)
        if path == "/api/v1/agents" and request.method == "GET":
            data = {"agents": self.agents, "total": len(self.agents)}
        elif path == "/api/v1/mcp-servers" and request.method == "GET":
            data = self.servers
        elif path == "/api/v1/topics":
            data = {"topics": self.topics, "total": len(self.topics)}
        elif request.method == "POST" and path.endswith("/sync"):
            data = {"tools": []}
        elif request.method == "POST":
            data = {"id": "created-" + str(len(self.calls)), **body}
            (self.agents if path.endswith("agents") else self.servers).append(data)
        elif request.method == "PATCH":
            data = next(row for row in [*self.agents, *self.servers] if path.endswith(row["id"]))
            data.update(body)
        elif request.method == "DELETE":
            self.agents = [a for a in self.agents if not path.endswith(a["id"])]
            data = {}
        else:
            raise AssertionError((request.method, path))
        return httpx.Response(200, json={"success": True, "data": data})

    def assert_key(self, request):
        if request.headers.get("authorization") != "Bearer person-key":
            raise AssertionError("Requests must use the person's API key")


class AgentSyncTests(unittest.TestCase):
    def test_rerun_upserts_without_duplicates(self):
        server = ApiServer()
        with LobeHub("https://chat.test", "person-key", transport=httpx.MockTransport(server.handle)) as client:
            managed = client.ensure_assistants(DESIRED, {})
            client.ensure_assistants(DESIRED, managed)
        self.assertEqual(len(server.agents), 1)
        self.assertEqual(len(server.servers), 1)
        self.assertEqual(server.agents[0]["plugins"], [{"identifier": "mu3lab-recipes", "mode": "pinned"}])

    def test_conversations_survive_uninstall_and_tools_are_detached(self):
        server = ApiServer()
        with LobeHub("https://chat.test", "person-key", transport=httpx.MockTransport(server.handle)) as client:
            managed = client.ensure_assistants(DESIRED, {})
            server.topics = [{"id": "conversation"}]
            client.ensure_assistants([], managed)
        self.assertEqual(len(server.agents), 1)
        self.assertEqual(server.agents[0]["plugins"], [])
        self.assertFalse(server.servers[0]["isEnabled"])

    def test_empty_retired_assistant_is_removed(self):
        server = ApiServer()
        with LobeHub("https://chat.test", "person-key", transport=httpx.MockTransport(server.handle)) as client:
            managed = client.ensure_assistants(DESIRED, {})
            result = client.ensure_assistants([], managed)
        self.assertEqual(server.agents, [])
        self.assertEqual(result, {})

    def test_stopped_or_disconnected_installed_app_keeps_its_assistant(self):
        server = ApiServer()
        with LobeHub("https://chat.test", "person-key", transport=httpx.MockTransport(server.handle)) as client:
            managed = client.ensure_assistants(DESIRED, {})
            client.ensure_assistants([], managed, installed={"recipes"})
        self.assertEqual(len(server.agents), 1)
        self.assertEqual(server.agents[0]["plugins"], [])
        self.assertFalse(server.servers[0]["isEnabled"])
        self.assertNotIn(("GET", "/api/v1/topics"), server.calls)

    def test_persons_instruction_edits_survive_sync(self):
        server = ApiServer()
        with LobeHub("https://chat.test", "person-key", transport=httpx.MockTransport(server.handle)) as client:
            managed = client.ensure_assistants(DESIRED, {})
            server.agents[0]["systemRole"] = "My own instructions"
            client.ensure_assistants(DESIRED, managed)
        self.assertEqual(server.agents[0]["systemRole"], "My own instructions")

    def test_api_failure_does_not_expose_key_or_response_body(self):
        with (
            LobeHub(
                "https://chat.test",
                "secret-key",
                transport=httpx.MockTransport(lambda _r: httpx.Response(401, json={"error": "secret-key"})),
            ) as client,
            self.assertRaises(ChatError) as caught,
        ):
            client.agents()
        self.assertNotIn("secret-key", str(caught.exception))

    def test_device_approval_mints_once_and_discards_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            chat_connections.save(
                "person", {"device_code": "device", "expires_at": 99999999999, "next_poll": 0, "interval": 5}, paths
            )
            client = Mock()
            client.poll_device_login.return_value = {"state": "approved", "access_token": "oidc-secret"}
            client.create_key.return_value = "sk-lh-secret"
            self.assertEqual(chat_connections.poll("person", client, paths)["state"], "connected")
            chat_connections.poll("person", client, paths)
            client.create_key.assert_called_once()
            self.assertNotIn("oidc-secret", str(chat_connections.records(paths)))
            self.assertNotIn(b"sk-lh-secret", (paths.state / "mu3lab.db").read_bytes())

    def test_another_person_cannot_poll_the_device_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            chat_connections.save("owner", {"device_code": "private"}, paths)
            with self.assertRaises(ValueError):
                chat_connections.poll("other", Mock(), paths)

    def test_repeated_connect_cannot_discard_an_approved_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            chat_connections.save("person", {"key": "sk-lh-existing"}, paths)
            with self.assertRaises(ValueError):
                chat_connections.begin("person", {"device_code": "new"}, paths)
            self.assertEqual(chat_connections.records(paths)["person"]["key"], "sk-lh-existing")

    def test_device_flow_uses_discovery_and_supported_endpoints(self):
        states = iter(["authorization_pending", "slow_down", "approved"])

        def handle(request):
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(
                    200,
                    json={
                        "device_authorization_endpoint": "https://chat.test/oidc/device",
                        "token_endpoint": "https://chat.test/oidc/token",
                    },
                )
            if request.url.path == "/oidc/device":
                self.assertIn(b"client_id=lobehub-cli", request.content)
                return httpx.Response(
                    200,
                    json={
                        "device_code": "private-device",
                        "user_code": "ABCD",
                        "verification_uri_complete": "https://chat.test/oidc/approve?code=ABCD",
                        "expires_in": 300,
                        "interval": 5,
                    },
                )
            self.assertEqual(request.url.path, "/oidc/token")
            self.assertIn(b"device_code=private-device", request.content)
            state = next(states)
            return httpx.Response(
                200 if state == "approved" else 400,
                json={"access_token": "oidc-secret"} if state == "approved" else {"error": state},
            )

        with LobeHub("https://chat.test", transport=httpx.MockTransport(handle)) as client:
            self.assertEqual(client.start_device_login()["user_code"], "ABCD")
            self.assertEqual(client.poll_device_login("private-device")["state"], "authorization_pending")
            self.assertEqual(client.poll_device_login("private-device")["state"], "slow_down")
            self.assertEqual(client.poll_device_login("private-device")["state"], "approved")
