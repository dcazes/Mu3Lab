"""MCP registry exposes application-data tools without infrastructure control."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ctl.control_state import ControlState
from ctl.mcp_catalog import CATALOG
from ctl.mcp_catalog import load as load_mcp_catalog
from ctl.mcp_registry import snapshot
from ctl.registry import load

INFRASTRUCTURE = {"vaultwarden", "ingress", "authentik", "ollama", "litellm", "freellmapi", "lobehub"}


def _catalog_with(tmp: str, mutate) -> Path:
    raw = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    mutate(raw)
    path = Path(tmp) / "mcp-catalog.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


class McpRegistryTests(unittest.TestCase):
    def _snapshot(self, installed: dict[str, str], env: dict[str, str] | None = None) -> dict:
        registry = load()
        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            for service_id, service_state in installed.items():
                state.set_installation(service_id, service_state)
            with (
                patch("ctl.mcp_registry.ControlState.runtime", return_value=state),
                patch("ctl.mcp_registry.read_runtime_env", return_value=env or {}),
            ):
                return snapshot(registry, installed)

    def test_declares_mealie_and_actual_data_tools(self):
        result = self._snapshot({"mealie": "running", "actual-budget": "running"})
        servers = {server["id"]: server for server in result["servers"]}
        self.assertIn("mealie.add_shopping_item", {tool["id"] for tool in servers["mealie-community"]["tools"]})
        self.assertIn("actual.create_transaction", {tool["id"] for tool in servers["actual-budget-community"]["tools"]})

    def test_infrastructure_services_never_have_an_mcp_server(self):
        exposed = {server.service_id for server in load_mcp_catalog(load())}
        self.assertFalse(exposed & INFRASTRUCTURE)

    def test_catalog_that_exposes_vaultwarden_is_rejected(self):
        def add_vaultwarden(raw: dict) -> None:
            raw["servers"].append(
                raw["servers"][0] | {"id": "vaultwarden-mcp", "service_id": "vaultwarden", "preferred": False}
            )

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            load_mcp_catalog(load(), _catalog_with(tmp, add_vaultwarden))

    def test_catalog_that_drops_the_vaultwarden_exclusion_is_rejected(self):
        def drop_exclusion(raw: dict) -> None:
            raw["excluded_services"] = [item for item in raw["excluded_services"] if item != "vaultwarden"]

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            load_mcp_catalog(load(), _catalog_with(tmp, drop_exclusion))

    def test_uninstalled_application_mcp_cards_are_hidden(self):
        registry = load()
        with patch("ctl.mcp_registry.ControlState.runtime", return_value=None):
            self.assertEqual(snapshot(registry, {})["servers"], [])

    def test_missing_credentials_explain_whether_mu3lab_can_create_them(self):
        result = self._snapshot({"mealie": "running", "actual-budget": "running"})
        servers = {server["service_id"]: server for server in result["servers"]}

        mealie, actual = servers["mealie"], servers["actual-budget"]
        self.assertEqual(mealie["state"], "authentication_required")
        self.assertTrue(mealie["auth"]["auto_provision"])
        self.assertIn("will be created", mealie["error"])

        self.assertEqual(actual["state"], "authentication_required")
        self.assertFalse(actual["auth"]["auto_provision"])
        self.assertIn("password", actual["error"])


if __name__ == "__main__":
    unittest.main()
