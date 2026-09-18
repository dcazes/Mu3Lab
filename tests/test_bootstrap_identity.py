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

    def test_manual_prompts_are_copyable_and_explicit(self):
        ctx = {"inputs": {}, "root": Path("/tmp"), "log_fn": lambda _: lambda _: None}
        with patch("ctl.install._runtime_marker", return_value=False):
            prompt = install.fix_vaultwarden_setup({}, ctx)["prompt"]
        self.assertEqual(prompt["kind"], "manual_setup")
        self.assertEqual(prompt["copy_url"], prompt["url"])
        self.assertIn("password", prompt["body"].lower())
        self.assertIn("check", prompt["check_label"].lower())

    def test_authentik_setup_uses_version_stable_private_root(self):
        host = "mu3lab-3.example.ts.net"
        ctx = {"inputs": {}, "root": Path("/tmp"), "log_fn": lambda _: lambda _: None}
        with patch("ctl.install._tailscale_dns_name_for_install", return_value=host), \
             patch("ctl.install._runtime_marker", return_value=False):
            prompt = install.fix_authentik_setup({}, ctx)["prompt"]
            check = install.check_authentik_setup(ctx)
        expected = f"https://{host}:{install.AUTHENTIK_SERVE_PORT}/"
        self.assertEqual(prompt["url"], expected)
        self.assertEqual(check["setup_url"], expected)
        self.assertNotIn("initial-setup", prompt["url"])
        self.assertEqual(prompt["recovery_action"], "reset_authentik_admin")
        self.assertEqual(prompt["recovery_username"], "akadmin")

    def test_authentik_recovery_is_one_time_and_not_a_marker(self):
        ctx = {"inputs": {}, "root": Path("/tmp"),
               "log_fn": lambda _: lambda _: None}
        with patch("ctl.install.actions.reset_authentik_admin_password",
                   return_value={"ok": True}) as reset:
            result = install.reset_authentik_admin_password(ctx)
        self.assertEqual(result["username"], "akadmin")
        self.assertTrue(result["temporary_password"].startswith("Mu3Lab-"))
        self.assertNotIn("confirmed", result)
        reset.assert_called_once()

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
        self.assertIn('authentik_host: "https://mu3lab-4.taile2cc7a.ts.net:8444"', content)
        self.assertIn('authentik_host_browser: "https://mu3lab-4.taile2cc7a.ts.net:8444"', content)
        self.assertNotIn("password", content.lower())
        self.assertNotIn("client_secret", content.lower())

    def test_authentik_host_must_be_the_private_https_origin(self):
        with self.assertRaises(ValueError):
            render_dashboard_blueprint(
                "mu3lab-4.taile2cc7a.ts.net", "http://localhost:9001")

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
