"""Apps that serve MCP themselves: no connector container, only the credential ContextForge sends."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import mcp_ops
from ctl.mcp_catalog import load as load_connectors
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.store.secrets import SecretStore

BUILTIN = {
    "outline-builtin": ("outline", "http://outline:3000/mcp"),
    "dawarich-builtin": ("dawarich", "http://dawarich-app:3000/api/v1/mcp"),
}


class CatalogTests(unittest.TestCase):
    def test_builtin_connectors_point_at_the_app_itself(self):
        connectors = {connector.id: connector for connector in load_connectors(load_registry())}
        for connector_id, (app_id, endpoint) in BUILTIN.items():
            connector = connectors[connector_id]
            self.assertTrue(connector.builtin)
            self.assertEqual((connector.service_id, connector.endpoint), (app_id, endpoint))
            # ContextForge reads the token it sends from MCP_AUTH_TOKEN.
            self.assertEqual([field["env"] for field in connector.credentials], ["MCP_AUTH_TOKEN"])
            compose = (Path(__file__).resolve().parents[1] / "apps" / app_id / "docker-compose.yml").read_text()
            self.assertIn("mu3lab_mcp_upstream", compose)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.paths = RuntimePaths(Path(temp.name))
        for target in ("ctl.mcp_ops.RuntimePaths", "ctl.mcp_registry.RuntimePaths"):
            patcher = patch(target, return_value=self.paths)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = next(c for c in load_connectors(load_registry()) if c.id == "outline-builtin")

    def test_materialize_writes_only_the_credential_file(self):
        with patch("ctl.mcp_ops.SecretStore", return_value=SecretStore(self.paths)):
            project = mcp_ops._materialize(self.server, Path(__file__).resolve().parents[1])
        self.assertEqual(sorted(item.name for item in project.iterdir()), [".env"])
        self.assertTrue(mcp_ops._ready(self.server, project))


class HeaderTests(unittest.TestCase):
    def test_fixed_headers_go_to_contextforge_with_the_key(self):
        from ctl.contextforge import ContextForge

        api = ContextForge.__new__(ContextForge)
        sent = {}
        api.gateways = lambda: []
        api.request = lambda method, path, json=None, **_: sent.update(json) or {"id": "g1"}
        api.ensure_gateway(
            "dawarich-builtin", "http://dawarich-app:3000/api/v1/mcp", "k", "Dawarich", {"X-Forwarded-Proto": "https"}
        )
        self.assertEqual(sent["auth_type"], "authheaders")
        self.assertEqual(
            sent["auth_headers"],
            [{"key": "Authorization", "value": "Bearer k"}, {"key": "X-Forwarded-Proto", "value": "https"}],
        )
        api.ensure_gateway("outline-builtin", "http://outline:3000/mcp", "k", "Outline")
        self.assertEqual((sent["auth_type"], sent["auth_token"]), ("bearer", "k"))


class OwnerArgumentTests(unittest.TestCase):
    def test_a_script_can_ask_for_the_owners_email(self):
        from ctl import mcp_credentials

        server = next(c for c in load_connectors(load_registry()) if c.id == "beaver-adapter")
        owner = {"owner": {"username": "dak", "email": "dak@example.com"}}
        with (
            patch("ctl.mcp_credentials.missing_credentials", return_value=["api_token"]),
            patch("ctl.mcp_credentials.onboarding_state.read", return_value=owner),
            patch("ctl.mcp_credentials.Compose") as compose,
            patch("ctl.mcp_credentials.mcp_config.write"),
        ):
            compose.return_value.run_script.return_value.ok = True
            ok, _detail = mcp_credentials.ensure(server, lambda _line: None)
        self.assertTrue(ok)
        self.assertEqual(compose.return_value.run_script.call_args.kwargs["args"], ["dak@example.com"])
