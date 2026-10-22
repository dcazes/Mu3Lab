"""ContextForge: connectors registered once, tools chosen in its screens, per-person keys for LobeChat."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from ctl import service_state
from ctl.authentik_blueprints import OidcApp, render_oidc_blueprint
from ctl.manifest.catalog import load as load_catalog

ROOT = Path(__file__).resolve().parents[1]
# ContextForge v1.0.11's settings.validation_name_pattern; it answers 422 to any other name.


class SignInTests(unittest.TestCase):
    def app(self, audience: str) -> OidcApp:
        return OidcApp(
            "contextforge",
            "ContextForge",
            8475,
            "mu3lab-contextforge",
            "s" * 40,
            ("/auth/sso/callback/mu3lab",),
            audience=audience,
        )

    def test_operator_only_oidc_apps_admit_administrators_only(self):
        content = render_oidc_blueprint("home.example.ts.net", self.app("operators"))
        bound = yaml.safe_load(content.replace("!Find", "").replace("!KeyOf", ""))["entries"]
        groups = [
            str(entry["attrs"]["group"])
            for entry in bound
            if entry["model"].endswith("policybinding") and entry.get("attrs")
        ]
        self.assertTrue(groups and all("mu3lab-household" not in group for group in groups))
        self.assertIn("offline_access", content)
        everyone = render_oidc_blueprint("home.example.ts.net", self.app("household"))
        self.assertIn("mu3lab-household", everyone)

    def test_the_contextforge_manifest_is_operator_only_and_trusts_open_webui(self):
        app = next(item for item in load_catalog().apps if item.id == "contextforge")
        self.assertEqual(app.manifest.route.audience, "operators")
        compose = yaml.safe_load((ROOT / "apps/contextforge/docker-compose.yml").read_text())
        env = compose["services"]["contextforge"]["environment"]
        self.assertEqual(env["SSO_API_TOKEN_AUTH_ENABLED"], "true")
        self.assertEqual(env["MCP_CLIENT_AUTH_ENABLED"], "true")
        self.assertIn("@sha256:", compose["services"]["contextforge"]["image"])
        # Connectors are reachable from ContextForge only.
        self.assertIn("mu3lab_mcp_upstream", compose["services"]["contextforge"]["networks"])
        for path in ROOT.glob("apps/*/connectors/*/docker-compose.yml"):
            for service in yaml.safe_load(path.read_text())["services"].values():
                self.assertNotIn("mu3lab_mcp", service.get("networks") or [], path)
        # ContextForge may register connectors on the connector networks only, not every private address.
        self.assertNotIn("SSRF_ALLOW_PRIVATE_NETWORKS", env)
        self.assertIn("CONTEXTFORGE_CONNECTOR_NETWORKS", env["SSRF_ALLOWED_NETWORKS"])
        self.assertEqual(app.manifest.env["CONTEXTFORGE_CONNECTOR_NETWORKS"], "{{connector_networks}}")
        owui = yaml.safe_load((ROOT / "apps/open-webui/docker-compose.yml").read_text())
        self.assertIn("offline_access", owui["services"]["open-webui"]["environment"]["OAUTH_SCOPES"])


class ConnectorNetworkTests(unittest.TestCase):
    def inspect(self, networks: list[dict], returncode: int = 0):
        return lambda _argv, **_kwargs: SimpleNamespace(returncode=returncode, stdout=json.dumps(networks))

    def test_the_allowlist_is_the_connector_subnets_and_the_host_address(self):
        networks = [
            {"Name": "mu3lab_mcp", "IPAM": {"Config": [{"Subnet": "10.210.3.0/24", "Gateway": "10.210.3.1"}]}},
            {"Name": "mu3lab_mcp_upstream", "IPAM": {"Config": [{"Subnet": "10.210.4.0/24"}]}},
            {"Name": "bridge", "IPAM": {"Config": [{"Subnet": "10.210.0.0/24", "Gateway": "10.210.0.1"}]}},
        ]
        allowed = json.loads(service_state.connector_networks(self.inspect(networks)))
        self.assertEqual(allowed, ["10.210.3.0/24", "10.210.4.0/24", "10.210.0.1/32"])

    def test_a_missing_network_or_docker_error_gives_nothing(self):
        only_one = [{"Name": "mu3lab_mcp", "IPAM": {"Config": [{"Subnet": "10.210.3.0/24"}]}}]
        self.assertEqual(service_state.connector_networks(self.inspect(only_one)), "")
        self.assertEqual(service_state.connector_networks(self.inspect([], returncode=1)), "")


if __name__ == "__main__":
    unittest.main()
