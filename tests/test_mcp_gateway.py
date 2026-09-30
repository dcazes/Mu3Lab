"""The tool gateway keeps each assistant's tool list small, honest and switch-bound."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from ctl import mcp_gateway
from ctl.control_state import ControlState
from ctl.mcp_activity import McpActivity
from ctl.mcp_catalog import load as load_catalog
from ctl.mcp_review import load as load_review
from ctl.registry import load as load_registry

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mu3lab_gateway", ROOT / "apps" / "mcp" / "gateway" / "gateway.py")
assert _spec and _spec.loader
gateway = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gateway)


def _review(tmp: str, mutate=None) -> Path:
    raw = {
        "schema_version": 1,
        "server": "demo",
        "categories": [
            {"id": "search", "title": "Search", "summary": "Find things."},
            {"id": "trash", "title": "Trash", "summary": "Delete things.", "default_on": False},
        ],
        "tools": {
            "search": {"category": "search", "access": "read", "core": True},
            "details": {"category": "search", "access": "read"},
            "rename": {"category": "search", "access": "write"},
            "delete": {"category": "trash", "access": "write"},
        },
        "blocked": {"set_key": "Changes credentials."},
    }
    if mutate:
        mutate(raw)
    path = Path(tmp) / "demo.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


class ReviewTests(unittest.TestCase):
    def test_every_catalog_review_loads_and_accounts_for_blocked_tools(self):
        for server in load_catalog(load_registry()):
            if server.review:
                review = load_review(server.id, server.review)
                self.assertLessEqual(sum(tool.core for tool in review.tools.values()), 6, server.id)
                self.assertFalse(set(review.blocked) & set(review.tools), server.id)

    def test_credential_tools_are_never_offered(self):
        review = load_review("immich-photo-manager", "mcp-reviews/immich-photo-manager.yaml")
        self.assertIn("update_credentials", review.blocked)
        self.assertIn("empty_trash", review.blocked)

    def test_invalid_reviews_are_rejected(self):
        broken = {
            "unknown category": lambda raw: raw["tools"]["search"].update(category="nope"),
            "bad access": lambda raw: raw["tools"]["search"].update(access="admin"),
            "offered and blocked": lambda raw: raw["blocked"].update(search="no"),
            "blocked without reason": lambda raw: raw["blocked"].update(other=""),
            "too many everyday tools": lambda raw: raw["tools"].update(
                {f"t{i}": {"category": "search", "access": "read", "core": True} for i in range(6)}
            ),
            "empty category": lambda raw: raw["categories"].append({"id": "x", "title": "X", "summary": "x"}),
        }
        for label, mutate in broken.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                path = _review(tmp, mutate)
                with self.assertRaises(ValueError):
                    load_review("demo", path.name, Path(tmp))


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.review = load_review("demo", _review(self.tmp.name).name, Path(self.tmp.name))
        self.activity = McpActivity(Path(self.tmp.name) / "activity.sqlite3")
        self.server = MagicMock(id="demo", service_id="demo")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reading_tools_start_on_and_changing_tools_start_off(self):
        states = mcp_gateway.tool_states(self.server, self.review, self.activity)
        self.assertEqual(states["categories"], {"search": True, "trash": False})
        self.assertEqual(states["tools"], {"search": True, "details": True, "rename": False, "delete": False})

    def test_owner_choices_override_defaults(self):
        self.activity.set_category("demo", "trash", True)
        self.activity.set_permission("demo", "delete", "needs_approval")
        self.activity.set_permission("demo", "details", "disabled")
        states = mcp_gateway.tool_states(self.server, self.review, self.activity)
        self.assertTrue(states["categories"]["trash"])
        self.assertTrue(states["tools"]["delete"])
        self.assertFalse(states["tools"]["details"])

    def test_category_map_names_switched_off_categories_and_where_to_switch_them(self):
        states = mcp_gateway.tool_states(self.server, self.review, self.activity)
        text = mcp_gateway.instructions("Demo", self.review, states)
        self.assertIn("- Trash (off): Delete things.", text)
        self.assertIn("- Search (on; tools that change data are off): Find things.", text)
        self.assertIn("Chat integrations, Demo", text)
        self.assertIn("find_tools", text)

    def test_category_whose_only_tools_change_data_reads_as_off(self):
        # Trash holds only a changing tool, which starts off.
        self.activity.set_category("demo", "trash", True)
        states = mcp_gateway.tool_states(self.server, self.review, self.activity)
        text = mcp_gateway.instructions("Demo", self.review, states)
        self.assertIn("- Trash (off: its tools change data and are switched off)", text)


class FakeConnector:
    def __init__(self, names):
        self.calls = []
        self.tools = {
            name: {"name": name, "description": f"{name} tool", "inputSchema": {"type": "object"}} for name in names
        }

    def list_tools(self):
        return self.tools

    def request(self, method, params):
        self.calls.append((method, params))
        return {"content": [{"type": "text", "text": "done"}]}


def _app(**tool_overrides):
    tools = {
        "search": {"name": "search", "category": "search", "access": "read", "core": True, "enabled": True},
        "details": {"name": "details", "category": "search", "access": "read", "core": False, "enabled": True},
        "rename": {"name": "rename", "category": "search", "access": "write", "core": False, "enabled": False},
        "delete": {"name": "delete", "category": "trash", "access": "write", "core": False, "enabled": True},
    }
    for name, changes in tool_overrides.items():
        tools[name].update(changes)
    return {
        "name": "Demo",
        "server_id": "demo",
        "upstream": {"url": "http://demo/mcp"},
        "categories": [
            {"id": "search", "title": "Search", "summary": "Find things.", "enabled": True},
            {"id": "trash", "title": "Trash", "summary": "Delete things.", "enabled": False},
        ],
        "tools": tools,
        "blocked": {"set_key": "Changes credentials."},
    }


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.connector = FakeConnector(["search", "details", "rename", "delete", "set_key", "surprise"])
        patcher = patch.object(gateway.CONNECTORS, "get", return_value=self.connector)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_assistant_sees_everyday_tools_and_discovery_only(self):
        names = [tool["name"] for tool in gateway.list_tools("demo", _app())]
        self.assertEqual(names, ["search", "find_tools", "use_tool"])

    def test_change_tool_appears_only_while_a_changing_tool_is_on(self):
        app = _app(rename={"enabled": True})
        self.assertIn("change_with_tool", [tool["name"] for tool in gateway.list_tools("demo", app)])

    def test_overview_lists_switched_off_categories_with_the_switch_to_flip(self):
        overview = gateway.find_tools("demo", _app(), {})["categories"]
        trash = next(item for item in overview if item["category"] == "trash")
        self.assertEqual(trash["state"], "off")
        self.assertIn("Chat integrations, Demo", trash["note"])

    def test_category_listing_gives_inputs_only_for_tools_that_are_on(self):
        tools = {item["tool"]: item for item in gateway.find_tools("demo", _app(), {"category": "search"})["tools"]}
        self.assertIn("inputs", tools["details"])
        self.assertEqual(tools["rename"]["state"], "off")
        self.assertNotIn("inputs", tools["rename"])
        self.assertNotIn("surprise", tools)

    def test_search_finds_tools_by_words(self):
        found = gateway.find_tools("demo", _app(), {"query": "delete"})["tools"]
        self.assertEqual(found[0]["tool"], "delete")

    def test_switched_off_category_blocks_its_tools(self):
        with self.assertRaisesRegex(gateway.GatewayError, "Trash category, which is switched off"):
            gateway.handle_call("demo", _app(), "change_with_tool", {"tool": "delete", "arguments": {}})
        self.assertEqual(self.connector.calls, [])

    def test_changing_tool_cannot_run_through_the_reading_path(self):
        app = _app(rename={"enabled": True})
        with self.assertRaisesRegex(gateway.GatewayError, "changes data"):
            gateway.handle_call("demo", app, "use_tool", {"tool": "rename", "arguments": {}})

    def test_blocked_and_unreviewed_tools_are_unreachable(self):
        with self.assertRaisesRegex(gateway.GatewayError, "not available in Mu3Lab"):
            gateway.handle_call("demo", _app(), "use_tool", {"tool": "set_key"})
        with self.assertRaisesRegex(gateway.GatewayError, "no tool called 'surprise'"):
            gateway.handle_call("demo", _app(), "use_tool", {"tool": "surprise"})
        self.assertEqual(self.connector.calls, [])

    def test_enabled_reading_tool_is_forwarded(self):
        result, tool, via = gateway.handle_call("demo", _app(), "use_tool", {"tool": "details", "arguments": {"id": 1}})
        self.assertEqual((tool, via), ("details", "use_tool"))
        self.assertEqual(self.connector.calls, [("tools/call", {"name": "details", "arguments": {"id": 1}})])
        self.assertFalse(result["isError"])

    def test_long_results_are_shortened(self):
        bounded = gateway._bounded({"content": [{"type": "text", "text": "x" * (gateway.MAX_TEXT + 10)}]})
        self.assertIn("shortened by Mu3Lab", bounded["content"][0]["text"])


class ExclusiveConnectorTests(unittest.TestCase):
    def test_second_immich_connector_cannot_start_while_one_is_active(self):
        from ctl import mcp_ops

        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            state.set_installation("immich", "running")
            state.set_mcp_server("immich-photo-manager", "immich", enabled=True, state="live")
            store = MagicMock()
            job = {"id": "j1", "service_id": "mcp:immich-control", "action": "install", "actor": "owner"}
            with patch("ctl.mcp_ops.ControlState.runtime", return_value=state):
                mcp_ops.execute_claimed(store, job, "worker", ROOT)
            transition = store.transition.call_args
            self.assertEqual(transition.args[1], "failed")
            self.assertEqual(transition.kwargs["error_code"], "mcp_other_connector_active")

    def test_switch_turns_the_active_connector_off_first(self):
        from ctl import mcp_ops

        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            state.set_installation("immich", "running")
            state.set_mcp_server("immich-photo-manager", "immich", enabled=True, state="live")
            job = {"id": "j1", "service_id": "mcp:immich-control", "action": "switch", "actor": "owner"}
            with (
                patch("ctl.mcp_ops.ControlState.runtime", return_value=state),
                patch("ctl.mcp_ops._stop_connector", return_value=(True, "")) as stop,
                patch("ctl.mcp_ops.missing_credentials", return_value=["api_key"]),
                patch("ctl.mcp_ops.ensure_credentials", return_value=(False, "no admin")),
            ):
                mcp_ops.execute_claimed(MagicMock(), job, "worker", ROOT)
            self.assertEqual(stop.call_args.args[0].id, "immich-photo-manager")


class PolicyTests(unittest.TestCase):
    def test_policy_holds_a_token_hash_never_the_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            state.set_mcp_server("immich-photo-manager", "immich", enabled=True, state="live")
            with (
                patch("ctl.mcp_gateway.project", return_value=Path(tmp) / "gw"),
                patch("ctl.control_state.ControlState.runtime", return_value=state),
                patch("ctl.mcp_gateway.McpActivity", return_value=McpActivity(Path(tmp) / "a.sqlite3")),
                patch("ctl.mcp_gateway.credential_path", return_value=Path(tmp) / "none.env"),
            ):
                policy = mcp_gateway.write_policy()
                token = mcp_gateway.app_token("immich")
            written = (Path(tmp) / "gw" / "policy" / "policy.json").read_text(encoding="utf-8")
        self.assertEqual(list(policy["apps"]), ["immich"])
        self.assertNotIn(token, written)
        self.assertEqual(json.loads(written)["apps"]["immich"]["server_id"], "immich-photo-manager")


class ConsoleTests(unittest.TestCase):
    def _resolve(self, tool_name: str):
        from ctl import mcp_console

        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            state.set_installation("immich", "running")
            snapshot = [
                {"id": name, "title": name, "risk": "read"}
                for name in ("update_credentials", "delete_assets", "search_smart")
            ]
            state.set_mcp_server("immich-photo-manager", "immich", enabled=True, state="live", tools=snapshot)
            with (
                patch("ctl.mcp_console.ControlState.runtime", return_value=state),
                patch("ctl.mcp_gateway.McpActivity", return_value=McpActivity(Path(tmp) / "a.sqlite3")),
            ):
                return mcp_console._resolve("immich-photo-manager", tool_name)

    def test_console_refuses_blocked_and_switched_off_tools(self):
        with self.assertRaisesRegex(ValueError, "API key"):
            self._resolve("update_credentials")
        with self.assertRaisesRegex(ValueError, "switched off"):
            self._resolve("delete_assets")

    def test_console_uses_the_reviewed_access(self):
        _server, tool, permission = self._resolve("search_smart")
        self.assertEqual((tool["risk"], permission), ("read", "auto"))


if __name__ == "__main__":
    unittest.main()
