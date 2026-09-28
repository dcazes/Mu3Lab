from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import bootstrap_state, install
from ctl.authentik_blueprints import render_dashboard_blueprint, write_dashboard_blueprint
from ctl.runtime import RuntimePaths


class BootstrapIdentityTests(unittest.TestCase):
    def test_acknowledgements_store_only_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            bootstrap_state.confirm("vaultwarden_account", paths)
            value = bootstrap_state.read(paths)
            self.assertIn("vaultwarden_account", value)
            self.assertNotIn("password", str(value).lower())

    def test_vaultwarden_account_is_created_from_the_terminal_answers(self):
        account = {"name": "Alex", "email": "alex@example.com", "password": "correct horse battery"}
        ctx = {"inputs": {}, "root": Path("/tmp"), "log_fn": lambda _: lambda _: None, "account": lambda: account}
        with patch("ctl.vaultwarden_api.register") as register:
            result = install.fix_vaultwarden_setup({}, ctx)
        self.assertTrue(result["ok"])
        register.assert_called_once_with("http://127.0.0.1:19462", "alex@example.com", "correct horse battery", "Alex")

    def test_account_steps_fail_clearly_without_answers(self):
        ctx = {"inputs": {}, "root": Path("/tmp"), "log_fn": lambda _: lambda _: None}
        for fix in (install.fix_vaultwarden_setup, install.fix_authentik_setup):
            with self.subTest(fix=fix.__name__):
                result = fix({}, ctx)
                self.assertFalse(result["ok"])
                self.assertIn("./install.sh", result["error"])

    def test_authentik_owner_gets_the_same_login(self):
        account = {"name": "Alex", "email": "alex@example.com", "password": "correct horse battery"}
        ctx = {"inputs": {}, "root": Path("/tmp"), "log_fn": lambda _: lambda _: None, "account": lambda: account}
        with patch("ctl.install.actions.authentik_set_owner", return_value={"ok": True}) as set_owner:
            result = install.fix_authentik_setup({}, ctx)
        self.assertTrue(result["ok"])
        self.assertEqual(set_owner.call_args.args[:3], ("alex@example.com", "Alex", "correct horse battery"))

    def test_authentik_account_is_done_once_first_run_setup_is_over(self):
        with (
            patch("ctl.install._authentik_check", return_value={"status": "ok"}),
            patch("ctl.install._authentik_initial_setup_pending", return_value=False),
        ):
            self.assertEqual(install.check_authentik_setup({})["state"], "ready")

    def test_identity_steps_are_before_final_dashboard_route(self):
        ids = [step["id"] for step in install.STEPS]
        self.assertLess(ids.index("vaultwarden_setup"), ids.index("tailscale_join"))
        self.assertLess(ids.index("tailscale_join"), ids.index("authentik_setup"))
        self.assertLess(ids.index("authentik_setup"), ids.index("dashboard_protection"))
        self.assertLess(ids.index("serve"), ids.index("dashboard_protection"))

    def test_dashboard_authentik_blueprint_is_declarative_and_secret_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = write_dashboard_blueprint(Path(tmp), "mu3lab-4.taile2cc7a.ts.net")
            content = target.read_text(encoding="utf-8")
        self.assertIn("authentik_providers_proxy.proxyprovider", content)
        self.assertIn("mode: forward_single", content)
        self.assertIn("authentik Embedded Outpost", content)
        self.assertIn("slug: mu3lab", content)
        self.assertIn("name: Mu3Lab LiteLLM provider", content)
        self.assertIn('external_host: "https://mu3lab-4.taile2cc7a.ts.net:8454"', content)
        self.assertIn('meta_launch_url: "https://mu3lab-4.taile2cc7a.ts.net:8454/ui/"', content)
        self.assertIn("name, Mu3Lab LiteLLM provider", content)
        self.assertIn("name: mu3lab-operators", content)
        self.assertIn("authentik_policies.policybinding", content)
        self.assertIn('authentik_host: "https://mu3lab-4.taile2cc7a.ts.net"', content)
        self.assertIn('authentik_host_browser: "https://mu3lab-4.taile2cc7a.ts.net"', content)
        self.assertNotIn("password", content.lower())
        self.assertNotIn("client_secret", content.lower())

    def test_authentik_host_must_be_the_private_https_origin(self):
        with self.assertRaises(ValueError):
            render_dashboard_blueprint("mu3lab-4.taile2cc7a.ts.net", "http://localhost:9001")

    def test_litellm_authentik_external_host_must_include_its_private_port(self):
        with self.assertRaises(ValueError):
            render_dashboard_blueprint("mu3lab-4.taile2cc7a.ts.net", litellm_host="https://mu3lab-4.taile2cc7a.ts.net")

    def test_caddy_preserves_public_authentik_origin_headers(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("Caddyfile", "Caddyfile.authenticated"):
            content = (root / "core" / "ingress" / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn("header_up Host {http.request.host}", content)
                self.assertIn("header_up X-Forwarded-Host {http.request.host}", content)
                self.assertIn("header_up X-Forwarded-Proto https", content)

    def test_dashboard_blueprint_rejects_non_tailnet_hosts(self):
        with self.assertRaises(ValueError):
            render_dashboard_blueprint("127.0.0.1")
