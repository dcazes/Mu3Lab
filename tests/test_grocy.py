"""Grocy's install, identity, mobile API, and reviewed chat contracts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from ctl.engine.project import Facts, render
from ctl.identity import gate_blueprint, mode_for
from ctl.manifest.catalog import load as load_catalog
from ctl.mcp_catalog import load as load_mcp
from ctl.mcp_gateway import tool_states
from ctl.mcp_review import load as load_review
from ctl.registry import load
from ctl.routes import _block
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/grocy"
CONNECTOR = APP / "connectors/grocy-community"


class GrocyTests(unittest.TestCase):
    def test_compose_contract_matches_manifest(self):
        service = load().get("grocy")
        compose = yaml.safe_load((APP / "docker-compose.yml").read_text())
        app = compose["services"]["grocy"]
        self.assertEqual(app["ports"], ["127.0.0.1:8003:80"])
        self.assertEqual(app["environment"]["GROCY_REVERSE_PROXY_AUTH_HEADER"], service.manifest.route.trusted_header)
        self.assertEqual(
            app["environment"]["GROCY_AUTH_CLASS"], "Grocy\\Middleware\\Auth\\Mu3labReverseProxyAuthMiddleware"
        )
        self.assertIn(
            "./Mu3labReverseProxyAuthMiddleware.php:/app/www/middleware/Auth/Mu3labReverseProxyAuthMiddleware.php:ro",
            app["volumes"],
        )
        self.assertIn("${MU3LAB_DATA_ROOT:-/srv/mu3lab/data}/grocy:/config", app["volumes"])
        self.assertIn(service.manifest.version, app["image"])
        self.assertIn("@sha256:", app["image"])
        self.assertIn(service.manifest.service.health.path, str(app["healthcheck"]["test"]))
        self.assertEqual(mode_for(service), "trusted_header")
        self.assertNotIn("docker.sock", str(app))
        self.assertIn("ALL", app["cap_drop"])

    def test_rendered_project_has_public_url_and_private_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = load_catalog()
            facts = Facts("mu3lab.example.ts.net", RuntimePaths(Path(tmp)), catalog)
            project = render(catalog.get("grocy"), facts)
            first = (project / ".env").read_text()
            render(catalog.get("grocy"), facts)
            self.assertEqual(first, (project / ".env").read_text())
            self.assertEqual(
                read_runtime_env(project / ".env")["GROCY_PUBLIC_URL"], "https://mu3lab.example.ts.net:8459"
            )
            self.assertEqual((project / ".env").stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                (project / "Mu3labReverseProxyAuthMiddleware.php").read_text(),
                (APP / "Mu3labReverseProxyAuthMiddleware.php").read_text(),
            )

    def test_outpost_only_registers_installed_grocy(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load_catalog()
            self.assertNotIn("Mu3Lab Grocy", gate_blueprint(catalog, "mu3lab.example.ts.net", paths))
            project = paths.projects / "grocy"
            project.mkdir(parents=True)
            (project / "docker-compose.yml").write_text("services: {}\n")
            text = gate_blueprint(catalog, "mu3lab.example.ts.net", paths)
            self.assertIn("Mu3Lab Grocy provider", text)
            self.assertIn("https://mu3lab.example.ts.net:8459", text)

    def test_api_key_bypass_strips_identity_and_browser_overwrites_it(self):
        block = _block(load().get("grocy"))
        bypass, browser = block.split("\t\thandle {", 1)
        self.assertIn("path /api/*", bypass)
        self.assertIn('header GROCY-API-KEY "*"', bypass)
        self.assertIn("header_up -X-Mu3lab-User", bypass)
        self.assertNotIn("header_up X-Mu3lab-User", bypass)
        self.assertNotIn("forward_auth", bypass)
        self.assertIn("forward_auth", browser)
        self.assertIn("header_up X-Mu3lab-User {http.request.header.X-Authentik-Username}", browser)
        self.assertNotIn("header_up -X-Mu3lab-User", browser)
        # People never see Grocy's local password form, and only after Authentik admits them.
        self.assertLess(browser.index("forward_auth"), browser.index("redir /login* / 302"))

    def test_owner_setup_runs_after_healthy_and_needs_the_owner(self):
        manifest = load_catalog().get("grocy").manifest
        (rule,) = manifest.rules
        self.assertEqual(rule.rule, "container_script")
        self.assertEqual(rule.with_["at"], "after_healthy")
        self.assertTrue(rule.with_["needs_owner"])
        self.assertEqual(rule.with_["args"], ["{{owner_json}}"])
        self.assertEqual(rule.with_["expect"], "MU3LAB_GROCY_OWNER_OK")
        script = (APP / rule.with_["script"]).read_text()
        self.assertIn("MU3LAB_GROCY_OWNER_OK", script)
        self.assertIn("password_verify('admin'", script)
        self.assertIn("RemoveApiKey", script)

    def test_connector_credential_comes_only_from_the_output_marker(self):
        app, connector = load_catalog().connector("grocy-community")
        self.assertEqual(connector.provision.service, "grocy")
        self.assertEqual(connector.credentials[0].key, "api_key")
        script = (app.connector_folder(connector.id) / connector.provision.script).read_text()
        self.assertIn("'MU3LAB_OUTPUT api_key='", script)
        self.assertIn("expected exactly one administrator", script)
        self.assertIn("MU3LAB_ERROR", script)

    def test_review_and_connector_keep_writes_off_at_gateway(self):
        server = next(server for server in load_mcp(load()) if server.id == "grocy-community")
        review = load_review(server.id, server.review)
        self.assertEqual(review.revision, server.revision)
        activity = MagicMock()
        activity.explicit_permissions.return_value = {}
        activity.category_switches.return_value = {}
        states = tool_states(server, review, activity)
        categories, tools = states["categories"], states["tools"]
        self.assertTrue(categories["inventory"])
        self.assertTrue(tools["inventory_stock_get_volatile"])
        self.assertTrue(all(not tools[name] for name, tool in review.tools.items() if tool.access == "write"))
        self.assertIn("system_dev_call_api", review.blocked)
        self.assertIn("system_users_get", review.blocked)
        config = yaml.safe_load((CONNECTOR / "mcp-grocy.yaml").read_text())
        self.assertEqual(set(config["tools"]), set(review.tools) | set(review.blocked))
        self.assertTrue(all(config["tools"][name]["enabled"] for name in review.tools))
        self.assertTrue(all(not config["tools"][name]["enabled"] for name in review.blocked))
        compose = yaml.safe_load((CONNECTOR / "docker-compose.yml").read_text())
        app = compose["services"]["grocy-mcp"]
        self.assertEqual(app["environment"]["GROCY_BASE_URL"], "http://grocy-app:80")
        # Only the tool gateway can reach a reviewed connector.
        self.assertEqual(app["networks"], ["mu3lab_backend", "mu3lab_mcp_upstream"])


if __name__ == "__main__":
    unittest.main()
