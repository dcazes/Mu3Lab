"""Runtime safety contracts for MVP configuration, identity, and MCP wiring."""

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import mcp_ops, service_config
from ctl.compute import compose_overrides
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env


ROOT = Path(__file__).resolve().parents[1]


class ServiceConfigurationTests(unittest.TestCase):
    def test_secret_is_write_only_and_blank_update_preserves_it(self):
        service = load().get("paperless-ngx")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime-root")
            with patch("ctl.service_config.RuntimePaths", return_value=paths), \
                 patch("ctl.service_config.ControlState.runtime", return_value=None):
                result = service_config.write(service, {
                    "admin_username": "operator",
                    "admin_email": "operator@example.test",
                    "admin_password": "private-value",
                })
                service_config.write(service, {"admin_password": ""})
                reread = service_config.read(service)
            password = next(field for field in result if field["key"] == "admin_password")
            self.assertIsNone(password["value"])
            self.assertTrue(password["secret_present"])
            self.assertNotIn("private-value", repr(reread))
            env_path = paths.projects / "paperless-ngx" / ".env"
            self.assertEqual(read_runtime_env(env_path)["PAPERLESS_ADMIN_PASSWORD"], "private-value")
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)


class McpVerificationTests(unittest.TestCase):
    def test_streamable_http_discovery_returns_runtime_tools(self):
        server = SimpleNamespace(
            transport="streamable-http",
            local_health="http://127.0.0.1:8815/health/ready",
            endpoint="http://immich-mcp:5000/mcp",
        )
        responses = [
            ({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}, "session"),
            ({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                {"name": "search_assets", "description": "Search the photo library"},
                {"name": "create_album", "description": "Create an album"},
            ]}}, "session"),
        ]
        with patch("ctl.mcp_ops._rpc_request", side_effect=responses) as request, \
             patch("ctl.mcp_ops._rpc_notification") as notification:
            tools = mcp_ops._discover_tools(server, {})
        self.assertEqual(request.call_args_list[1].args[1], "tools/list")
        notification.assert_called_once()
        self.assertEqual([tool["id"] for tool in tools], ["search_assets", "create_album"])
        self.assertEqual([tool["risk"] for tool in tools], ["read", "write"])

    def test_sse_rpc_decoder_uses_data_payload(self):
        result = mcp_ops._decode_rpc_response(
            b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n',
            "text/event-stream",
        )
        self.assertEqual(result["id"], 1)


class LobeChatIdentityTests(unittest.TestCase):
    def test_chat_uses_authentik_oidc_and_can_be_embedded(self):
        compose = (ROOT / "apps/lobehub/docker-compose.yml").read_text(encoding="utf-8")
        caddy = (ROOT / "core/ingress/Caddyfile.authenticated").read_text(encoding="utf-8")
        self.assertIn("AUTH_SSO_PROVIDERS", compose)
        self.assertIn(":19474 {", caddy)
        self.assertIn("frame-ancestors", caddy)
        self.assertIn(":19474 {", caddy)


class ComputeOverrideTests(unittest.TestCase):
    def test_one_system_mode_selects_only_a_curated_override(self):
        project = ROOT / "core/ollama"
        with patch("ctl.compute.resolved_mode", return_value="nvidia"):
            self.assertEqual(compose_overrides("ollama", project),
                             [project / "docker-compose.nvidia.yml"])
        with patch("ctl.compute.resolved_mode", return_value="cpu"):
            self.assertEqual(compose_overrides("ollama", project), [])


if __name__ == "__main__":
    unittest.main()
