"""Official API upserts preserve conversations and avoid duplicate assistants.

The old SQL/trigger tests were removed together with that implementation.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import jsonschema

from ctl import chat_connections, lobehub_ops
from ctl.integrations.lobehub import AssistantSyncError, ChatError, LobeHub
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

    def test_assistant_the_person_deleted_is_forgotten_not_retired(self):
        server = ApiServer()
        with LobeHub("https://chat.test", "person-key", transport=httpx.MockTransport(server.handle)) as client:
            managed = client.ensure_assistants(DESIRED, {})
            server.agents = []
            result = client.ensure_assistants([], managed, installed={"recipes"})
        self.assertEqual(result, {})
        self.assertNotIn(("PATCH", "/api/v1/agents/" + managed["recipes"]["agent_id"]), server.calls)

    def test_one_apps_failing_connector_does_not_stop_the_other_assistants(self):
        server = ApiServer()
        handle = server.handle
        broken = {"fail": False}

        def flaky(request):
            # The recipes connector's tool sync fails; pantry's must still be set up.
            if broken["fail"] and request.url.path.endswith("/sync"):
                row = next(s for s in server.servers if request.url.path.endswith(s["id"] + "/sync"))
                if row["identifier"] == "mu3lab-recipes":
                    return httpx.Response(502, json={"success": False})
            return handle(request)

        pantry = {**DESIRED[0], "id": "pantry", "title": "Pantry", "url": "http://gateway:8810/pantry/mcp"}
        with LobeHub("https://chat.test", "person-key", transport=httpx.MockTransport(flaky)) as client:
            managed = client.ensure_assistants(DESIRED, {})
            broken["fail"] = True
            with self.assertRaises(AssistantSyncError) as caught:
                client.ensure_assistants([*DESIRED, pantry], managed)
        self.assertIn("Recipes", str(caught.exception))
        self.assertIn("HTTP 502", str(caught.exception))
        self.assertEqual(set(caught.exception.managed), {"recipes", "pantry"})
        self.assertEqual(caught.exception.managed["recipes"], managed["recipes"])
        self.assertIn("Pantry", [agent["title"] for agent in server.agents])

    def test_partial_sync_saves_the_assistants_that_worked(self):
        class Client:
            def __init__(self, _origin, key):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def ensure_assistants(self, desired, managed, *, installed):
                raise AssistantSyncError("Recipes: failed", {"pantry": {"agent_id": "p", "instructions": "i"}})

        state = Mock()
        state.installation.return_value = None
        with (
            patch.object(lobehub_ops.chat_connections, "records", return_value={"owner": {"key": "sk-lh-working"}}),
            patch.object(lobehub_ops.chat_connections, "save") as save,
            patch.object(lobehub_ops, "desired_assistants", return_value=[]),
            patch.object(lobehub_ops.ControlState, "runtime", return_value=state),
            patch.object(lobehub_ops, "origin", return_value="https://chat.test"),
            patch.object(lobehub_ops, "LobeHub", Client),
        ):
            ok, detail = lobehub_ops.sync_agents(lambda _line: None)
        self.assertFalse(ok)
        self.assertIn("Recipes", detail)
        save.assert_called_once()
        self.assertEqual(save.call_args.args[1]["managed"], {"pantry": {"agent_id": "p", "instructions": "i"}})

    def test_one_persons_failure_does_not_stop_others(self):
        synced = []

        class Client:
            def __init__(self, _origin, key):
                self.key = key

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def ensure_assistants(self, desired, managed, *, installed):
                if self.key == "sk-lh-revoked":
                    raise ChatError("Chat did not accept this request (HTTP 401).")
                synced.append(self.key)
                return {"recipes": {"agent_id": "a", "instructions": "i"}}

        state = Mock()
        state.installation.return_value = None
        records = {"first": {"key": "sk-lh-revoked"}, "second": {"key": "sk-lh-working"}}
        with (
            patch.object(lobehub_ops.chat_connections, "records", return_value=records),
            patch.object(lobehub_ops.chat_connections, "save") as save,
            patch.object(lobehub_ops, "desired_assistants", return_value=[]),
            patch.object(lobehub_ops.ControlState, "runtime", return_value=state),
            patch.object(lobehub_ops, "origin", return_value="https://chat.test"),
            patch.object(lobehub_ops, "LobeHub", Client),
        ):
            ok, detail = lobehub_ops.sync_agents(lambda _line: None)
        self.assertFalse(ok)
        self.assertIn("HTTP 401", detail)
        self.assertEqual(synced, ["sk-lh-working"])
        saved = {call.args[0]: call.args[1] for call in save.call_args_list}
        # The failing person keeps the reason for the dashboard; the other is synced and cleared.
        self.assertIn("HTTP 401", saved["first"]["error"])
        self.assertNotIn("managed", saved["first"])
        self.assertEqual(saved["second"]["error"], "")
        self.assertIn("recipes", saved["second"]["managed"])

    def test_report_says_why_each_app_has_no_assistant(self):
        def app(app_id):
            manifest = Mock(id=app_id)
            manifest.name = app_id.title()
            return Mock(id=app_id, manifest=manifest)

        def connector(app_id, reviewed):
            return Mock(id=app_id + "-mcp", service_id=app_id, gateway=reviewed)

        apps = [app(name) for name in ("unreviewed", "down", "ready", "waiting")]
        servers = {
            "down-mcp": {"enabled": True, "state": "authentication_required"},
            "ready-mcp": {"enabled": True, "state": "live"},
            "waiting-mcp": {"enabled": True, "state": "live"},
        }
        state = Mock()
        state.installation.return_value = {"state": "running"}
        state.mcp_server.side_effect = servers.get
        connectors = [
            connector("unreviewed", False),
            connector("down", True),
            connector("ready", True),
            connector("waiting", True),
        ]
        records = {"me": {"key": "sk-lh-key", "managed": {"ready": {"agent_id": "a"}}}}
        with (
            patch.object(lobehub_ops.ControlState, "runtime", return_value=state),
            patch.object(lobehub_ops.chat_connections, "records", return_value=records),
            patch.object(lobehub_ops, "load_connectors", return_value=connectors),
            patch.object(lobehub_ops, "load_registry"),
            patch.object(lobehub_ops, "load", return_value=Mock(apps=apps)),
        ):
            report = {item["id"]: item["status"] for item in lobehub_ops.assistant_report("me")}
            stranger = {item["id"]: item["status"] for item in lobehub_ops.assistant_report("someone-else")}
        self.assertEqual(
            report, {"unreviewed": "needs_review", "down": "connector_down", "ready": "ready", "waiting": "pending"}
        )
        self.assertEqual(stranger["ready"], "not_connected")

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

    def test_device_flow_uses_the_public_cli_endpoints(self):
        # Real LobeHub redirects anonymous discovery requests to its sign-in page.
        states = iter(["authorization_pending", "slow_down", "approved"])

        def handle(request):
            self.assertNotIn("well-known", request.url.path)
            if request.url.path == "/oidc/device/auth":
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
