from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import bootstrap_state, install
from ctl.authentik_blueprints import GatedApp, render_gate_blueprint
from ctl.integrations.authentik import AuthentikError
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from tests.support import runtime_paths


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
        with patch("ctl.integrations.vaultwarden.register") as register:
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
        with tempfile.TemporaryDirectory() as tmp, runtime_paths(RuntimePaths(Path(tmp))):
            values = install._authentik_compose_env(account, lambda _: None)
            saved = read_runtime_env(Path(values["AUTHENTIK_ENV_FILE"]))
            self.assertEqual(saved["AUTHENTIK_BOOTSTRAP_EMAIL"], account["email"])
            self.assertTrue(saved["AUTHENTIK_BOOTSTRAP_PASSWORD_HASH"].startswith("pbkdf2_sha256$"))
            self.assertNotIn(account["password"], Path(values["AUTHENTIK_ENV_FILE"]).read_text())
            self.assertGreaterEqual(len(saved["AUTHENTIK_BOOTSTRAP_TOKEN"]), 48)
            with patch("ctl.install.Authentik.runtime") as runtime:
                client = runtime.return_value
                client.user.return_value = {"pk": 1, "email": account["email"]}
                result = install.fix_authentik_setup({}, ctx)
            self.assertTrue(result["ok"])
            client.wait_for_defaults.assert_called_once()
            client.update_user.assert_called_once_with(1, name="Alex")
            final = read_runtime_env(Path(values["AUTHENTIK_ENV_FILE"]))
            self.assertNotIn("AUTHENTIK_BOOTSTRAP_PASSWORD_HASH", final)
            self.assertNotIn("AUTHENTIK_BOOTSTRAP_EMAIL", final)
            self.assertEqual(final["AUTHENTIK_BOOTSTRAP_TOKEN"], saved["AUTHENTIK_BOOTSTRAP_TOKEN"])

    def test_failed_owner_update_keeps_first_start_settings_for_a_retry(self):
        account = {"name": "Owner", "email": "owner@example.test", "password": "private password"}
        ctx = {"account": lambda: account, "log_fn": lambda _: lambda _: None}
        with tempfile.TemporaryDirectory() as tmp, runtime_paths(RuntimePaths(Path(tmp))):
            values = install._authentik_compose_env(account, lambda _: None)
            path = Path(values["AUTHENTIK_ENV_FILE"])
            original = path.read_text()
            with patch("ctl.install.Authentik.runtime") as runtime:
                client = runtime.return_value
                client.user.return_value = {"pk": 1, "email": account["email"]}
                client.update_user.side_effect = AuthentikError("Update refused.")
                self.assertFalse(install.fix_authentik_setup({}, ctx)["ok"])
            self.assertEqual(path.read_text(), original)

    def test_authentik_account_is_done_once_bootstrap_settings_are_removed(self):
        with (
            patch("ctl.install._authentik_check", return_value={"status": "ok"}),
            patch("ctl.install.Authentik.runtime") as runtime,
            patch("ctl.install.read_runtime_env", return_value={}),
        ):
            runtime.return_value.user.return_value = {"pk": 1, "email": "owner@example.test"}
            self.assertEqual(install.check_authentik_setup({})["state"], "ready")

    def test_identity_steps_are_before_final_dashboard_route(self):
        ids = [step["id"] for step in install.STEPS]
        self.assertLess(ids.index("vaultwarden_setup"), ids.index("vaultwarden_serve"))
        self.assertLess(ids.index("tailscale_join"), ids.index("authentik_setup"))
        self.assertLess(ids.index("authentik_setup"), ids.index("dashboard_protection"))
        self.assertLess(ids.index("serve"), ids.index("dashboard_protection"))

    def test_dashboard_authentik_blueprint_is_declarative_and_secret_free(self):
        content = render_gate_blueprint(
            "mu3lab-4.taile2cc7a.ts.net",
            8446,
            (
                GatedApp("litellm", "LiteLLM", 8454, "operators"),
                GatedApp("freellmapi", "FreeLLMAPI", 8455, "operators"),
            ),
        )
        self.assertIn("authentik_providers_proxy.proxyprovider", content)
        self.assertIn("mode: forward_single", content)
        self.assertIn("authentik Embedded Outpost", content)
        self.assertIn("slug: mu3lab", content)
        self.assertIn("name: Mu3Lab LiteLLM provider", content)
        self.assertIn('external_host: "https://mu3lab-4.taile2cc7a.ts.net:8454"', content)
        # The dashboard's provider keeps :8446 so sign-in returns to Mu3Lab, not Authentik's library.
        self.assertIn('external_host: "https://mu3lab-4.taile2cc7a.ts.net:8446"', content)
        # Only the dashboard appears in the Authentik library; the rest are hidden.
        self.assertIn('meta_launch_url: "https://mu3lab-4.taile2cc7a.ts.net:8446"', content)
        self.assertEqual(content.count('meta_launch_url: "blank://blank"'), 2)
        self.assertIn("name, Mu3Lab LiteLLM provider", content)
        self.assertIn("name: mu3lab-operators", content)
        self.assertIn("authentik_policies.policybinding", content)
        self.assertIn('authentik_host: "https://mu3lab-4.taile2cc7a.ts.net"', content)
        self.assertIn('authentik_host_browser: "https://mu3lab-4.taile2cc7a.ts.net"', content)
        self.assertNotIn("AUTHENTIK_BOOTSTRAP_PASSWORD", content)
        self.assertNotIn("client_secret", content.lower())

    def test_gated_app_rejects_invalid_private_port(self):
        with self.assertRaises(ValueError):
            render_gate_blueprint("mu3lab-4.taile2cc7a.ts.net", 8446, (GatedApp("llm", "LLM", 80, "operators"),))

    def test_caddy_preserves_public_authentik_origin_headers(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("Caddyfile", "Caddyfile.authenticated"):
            content = (root / "apps" / "ingress" / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn("header_up Host {http.request.host}", content)
                self.assertIn("header_up X-Forwarded-Host {http.request.host}", content)
                self.assertIn("header_up X-Forwarded-Proto https", content)

    def test_dashboard_blueprint_rejects_non_tailnet_hosts(self):
        with self.assertRaises(ValueError):
            render_gate_blueprint("127.0.0.1", 8446)
