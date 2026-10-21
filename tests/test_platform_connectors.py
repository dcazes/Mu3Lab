"""Read-only, administrators-only chat connectors for platform services (Authentik, Kopia)."""

from __future__ import annotations

import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ctl.manifest.catalog import CatalogError, cached
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]


def adapter(mode: str):
    with patch.dict("os.environ", {"MCP_APP": mode}), patch("http.server.ThreadingHTTPServer"):
        spec = importlib.util.spec_from_file_location(f"mu3lab_{mode}_adapter", ROOT / "platform/mcp-adapter/server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class CatalogRuleTests(unittest.TestCase):
    def test_a_platform_assistant_must_be_for_administrators(self):
        from ctl.manifest import catalog

        app = cached().get("kopia-ui")
        chat = app.manifest.chat
        household = chat.model_copy(update={"assistant": chat.assistant.model_copy(update={"audience": "household"})})
        with self.assertRaisesRegex(CatalogError, "administrators only"):
            catalog._load_connectors(app.manifest.model_copy(update={"chat": household}), app.folder)

    def test_a_platform_connector_may_only_read(self):
        from ctl import mcp_review
        from ctl.manifest import catalog

        app, connector = cached().connector("authentik-reader")
        review = mcp_review.load(connector.id, f"apps/authentik/connectors/{connector.id}/review.yaml")
        tools = dict(review.tools)
        tools["list_people"] = mcp_review.ReviewedTool("list_people", "people", "write", True)
        with (
            patch("ctl.mcp_review.load", return_value=review.__class__(**{**review.__dict__, "tools": tools})),
            self.assertRaisesRegex(CatalogError, "may only read; it offers list_people"),
        ):
            catalog._check_read_only(connector, app.connector_folder(connector.id))


class AudienceTests(unittest.TestCase):
    def test_lobechat_leaves_administrators_only_assistants_out_for_members(self):
        from ctl import chat_ops

        state = unittest.mock.Mock()
        state.mcp_server.return_value = {"enabled": True, "state": "live"}
        state.app_installation.return_value = {"state": "running"}
        with (
            patch("ctl.chat_ops.ControlState.runtime", return_value=state),
            patch("ctl.contextforge_ops.virtual_server", return_value="srv"),
            patch("ctl.contextforge_ops.person_key", return_value="key"),
            patch("ctl.contextforge.endpoint", return_value="http://contextforge/servers/srv/mcp"),
        ):
            member = {item["id"] for item in chat_ops.desired_assistants("u1", "m@example.com")}
            operator = {item["id"] for item in chat_ops.desired_assistants("u2", "o@example.com", operator=True)}
            everyone = {item["id"]: item["audience"] for item in chat_ops.desired_assistants()}
        self.assertIn("kopia-ui", operator)
        self.assertNotIn("kopia-ui", member)
        self.assertNotIn("authentik", member)
        self.assertEqual((everyone["authentik"], everyone["mealie"]), ("operators", "household"))

    def test_foundation_services_count_as_installed_and_running(self):
        from ctl.control_state import ControlState

        state = ControlState.__new__(ControlState)
        with patch.object(ControlState, "installation", return_value=None):
            self.assertEqual(state.app_installation("authentik")["state"], "running")
            self.assertIsNone(state.app_installation("kopia-ui"))
            self.assertIsNone(state.app_installation("no-such-app"))


class AuthentikTests(unittest.TestCase):
    def test_the_service_account_can_only_view(self):
        from ctl.authentik_blueprints import render_chat_reader_blueprint

        class Loader(yaml.SafeLoader):
            pass

        Loader.add_constructor("!Find", lambda loader, node: loader.construct_sequence(node, deep=True))
        key = "k" * 64
        entries = yaml.load(render_chat_reader_blueprint(key), Loader=Loader)["entries"]
        role, user, token = entries
        self.assertTrue(all(name.split(".")[1].startswith("view_") for name in role["attrs"]["permissions"]))
        self.assertEqual(user["attrs"]["type"], "service_account")
        self.assertEqual((token["attrs"]["intent"], token["attrs"]["key"]), ("api", key))
        with self.assertRaises(ValueError):
            render_chat_reader_blueprint('short"; injected')

    def test_the_hook_applies_the_blueprint_and_returns_its_key(self):
        from ctl.engine.hooks import load_app_hooks

        app = cached().get("authentik")
        with patch("ctl.integrations.authentik.Authentik.runtime") as runtime:
            values = load_app_hooks(app).connector_credentials(app, "authentik-reader", RuntimePaths(Path("/nowhere")))
        name, content = runtime.return_value.apply_blueprint.call_args.args
        self.assertEqual(name, "Mu3Lab chat read-only access")
        self.assertIn(values["api_token"], content)

    def test_events_keep_only_who_what_when(self):
        module = adapter("authentik")
        event = {
            "created": "2026-10-18T10:00:00Z",
            "action": "login_failed",
            "client_ip": "100.64.0.2",
            "user": {},
            "context": {"username": "sam", "message": "Invalid credentials", "http_request": {"path": "/x"}},
        }
        sent = []
        reply = json.dumps({"results": [event]}).encode()
        with (
            patch.dict("os.environ", {"AUTHENTIK_API_TOKEN": "t"}),
            patch.object(module, "_request", side_effect=lambda route, *_: sent.append(route) or (reply, 200)),
        ):
            events = module._authentik("recent_events", {"action": "login_failed", "limit": 5})
            with self.assertRaises(ValueError):
                module._authentik("recent_events", {"action": "user_delete"})
        self.assertEqual(events[0]["username"], "sam")
        self.assertNotIn("http_request", json.dumps(events))
        self.assertIn("action=login_failed", sent[0])


class KopiaTests(unittest.TestCase):
    def test_the_hook_reuses_mu3labs_control_login(self):
        from ctl.engine.hooks import load_app_hooks

        app = cached().get("kopia-ui")
        with tempfile.TemporaryDirectory() as temp:
            paths = RuntimePaths(Path(temp))
            (paths.projects / "kopia-ui").mkdir(parents=True)
            (paths.projects / "kopia-ui" / ".env").write_text("KOPIA_SERVER_CONTROL_PASSWORD=control-secret\n")
            hooks = load_app_hooks(app)
            self.assertEqual(hooks.connector_credentials(app, "kopia-reader", paths), {"password": "control-secret"})
            (paths.projects / "kopia-ui" / ".env").write_text("")
            with self.assertRaises(RuntimeError):
                hooks.connector_credentials(app, "kopia-reader", paths)

    def test_backup_status_uses_only_the_read_route(self):
        module = adapter("kopia")
        source = {
            "source": {"path": "/data/mealie"},
            "status": "IDLE",
            "lastSnapshot": {"startTime": "a", "endTime": "b", "stats": {"totalSize": 10, "errorCount": 0}},
            "nextSnapshotTime": "c",
        }
        sent = []
        reply = json.dumps({"sources": [source]}).encode()
        with (
            patch.dict("os.environ", {"KOPIA_USERNAME": "mu3lab-control", "KOPIA_PASSWORD": "pw"}),
            patch.object(
                module, "_request", side_effect=lambda route, *rest: sent.append((route, rest)) or (reply, 200)
            ),
        ):
            backups = module._kopia("backup_status", {})
            auth = module._auth()
        self.assertEqual(sent, [("/api/v1/control/sources", ())])
        self.assertEqual(backups[0]["app"], "mealie")
        self.assertEqual(backups[0]["last_size_bytes"], 10)
        self.assertEqual(auth, "Basic " + base64.b64encode(b"mu3lab-control:pw").decode())

    def test_it_listens_on_loopback_and_dockers_host_address(self):
        module = adapter("kopia")
        with (
            patch.dict("os.environ", {"MCP_LISTEN": "127.0.0.1:8830,@docker0:8830"}),
            patch.object(module, "_interface_address", return_value="10.210.0.1") as interface,
        ):
            self.assertEqual(module._listeners(), [("127.0.0.1", 8830), ("10.210.0.1", 8830)])
        interface.assert_called_once_with("docker0")
        with patch.dict("os.environ", {"MCP_LISTEN": ""}):
            self.assertEqual(module._listeners(), [("0.0.0.0", 8080)])

    def test_contextforge_can_reach_this_computer(self):
        compose = yaml.safe_load((ROOT / "apps/contextforge/docker-compose.yml").read_text())
        self.assertIn("host.docker.internal:host-gateway", compose["services"]["contextforge"]["extra_hosts"])


if __name__ == "__main__":
    unittest.main()
