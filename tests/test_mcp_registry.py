"""MCP registry exposes application-data tools without infrastructure control."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from ctl.control_state import ControlState
from ctl.mcp_registry import snapshot
from ctl.registry import load


class McpRegistryTests(unittest.TestCase):
    def test_declares_mealie_and_actual_data_tools(self):
        registry = load()
        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            state.set_installation("mealie", "running")
            state.set_installation("actual-budget", "running")
            with patch("ctl.mcp_registry.ControlState.runtime", return_value=state):
                result = snapshot(registry, {"mealie": "running", "actual-budget": "running"})
        servers = {server["id"]: server for server in result["servers"]}
        self.assertIn("mealie.add_shopping_item", {tool["id"] for tool in servers["mealie-community"]["tools"]})
        self.assertIn("actual.create_transaction", {tool["id"] for tool in servers["actual-budget-community"]["tools"]})

    def test_vaultwarden_and_lifecycle_are_excluded(self):
        registry = load()
        result = snapshot(registry, {})
        self.assertNotIn("vaultwarden", {server["id"] for server in result["servers"]})
        self.assertFalse(any("start" in tool["id"] or "stop" in tool["id"]
                             for server in result["servers"] for tool in server["tools"]))

    def test_uninstalled_application_mcp_cards_are_hidden(self):
        registry = load()
        with patch("ctl.mcp_registry.ControlState.runtime", return_value=None):
            self.assertEqual(snapshot(registry, {})["servers"], [])

    def test_setup_mode_separates_managed_and_operator_action(self):
        registry = load()
        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            state.set_installation("firecrawl", "running")
            state.set_installation("mealie", "running")
            with patch("ctl.mcp_registry.ControlState.runtime", return_value=state), \
                 patch("ctl.mcp_registry.read_runtime_env", return_value={}):
                result = snapshot(registry, {"firecrawl": "running", "mealie": "running"})
        servers = {server["service_id"]: server for server in result["servers"]}
        self.assertEqual(servers["firecrawl"]["setup_mode"], "automatic")
        self.assertEqual(servers["mealie"]["setup_mode"], "manual")
        self.assertIn("credential", servers["mealie"]["setup_detail"])


if __name__ == "__main__":
    unittest.main()
