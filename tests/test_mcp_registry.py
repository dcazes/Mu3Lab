"""MCP registry exposes application-data tools without infrastructure control."""

from __future__ import annotations

import unittest

from ctl.mcp_registry import snapshot
from ctl.registry import load


class McpRegistryTests(unittest.TestCase):
    def test_declares_mealie_and_actual_data_tools(self):
        registry = load()
        result = snapshot(registry, {service.id: "blocked" for service in registry.services})
        servers = {server["id"]: server for server in result["servers"]}
        self.assertIn("mealie.add_shopping_item", {tool["id"] for tool in servers["mealie-community"]["tools"]})
        self.assertIn("actual.create_transaction", {tool["id"] for tool in servers["actual-budget-community"]["tools"]})

    def test_vaultwarden_and_lifecycle_are_excluded(self):
        registry = load()
        result = snapshot(registry, {})
        self.assertNotIn("vaultwarden", {server["id"] for server in result["servers"]})
        self.assertFalse(any("start" in tool["id"] or "stop" in tool["id"]
                             for server in result["servers"] for tool in server["tools"]))


if __name__ == "__main__":
    unittest.main()
