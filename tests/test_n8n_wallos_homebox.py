"""n8n, Wallos and HomeBox: who gets in, which paths skip or refuse Authentik, and Wallos's sign-in launcher."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ctl.engine.launch import caddy_handler
from ctl.manifest.models import Oidc
from ctl.registry import load
from ctl.routes import _block

ROOT = Path(__file__).resolve().parents[1]


def _compose(app: str) -> dict:
    return yaml.safe_load((ROOT / "apps" / app / "docker-compose.yml").read_text(encoding="utf-8"))


def _env(app: str, service: str) -> dict:
    return _compose(app)["services"][service]["environment"]


class N8nTests(unittest.TestCase):
    def test_only_administrators_pass_the_gate(self):
        manifest = load().get("n8n").manifest
        self.assertEqual((manifest.route.access, manifest.route.audience), ("gate", "operators"))
        self.assertTrue(manifest.account.save_login_to_vault)

    def test_webhooks_and_forms_skip_authentik_and_nothing_else_does(self):
        block = _block(load().get("n8n"))
        bypass, gated = block.split("\t\thandle {", 1)
        self.assertIn("path /webhook* /form*", bypass)
        self.assertNotIn("forward_auth", bypass)
        self.assertIn("forward_auth 127.0.0.1:9001", gated)

    def test_code_runs_in_a_runner_with_no_way_out(self):
        compose = _compose("n8n")
        self.assertEqual(compose["services"]["runners"]["networks"], ["n8n_internal"])
        self.assertEqual(compose["networks"]["n8n_internal"], {"internal": True})
        self.assertEqual(_env("n8n", "n8n")["N8N_RUNNERS_MODE"], "external")
        self.assertEqual(_env("n8n", "n8n")["N8N_DIAGNOSTICS_ENABLED"], "false")


class WallosTests(unittest.TestCase):
    def test_authentik_is_the_only_way_in(self):
        env = _env("wallos", "wallos")
        self.assertEqual(env["OIDC_DISABLE_PASSWORD_LOGIN"], "true")
        self.assertEqual(env["OIDC_REQUIRE_EMAIL_VERIFIED"], "true")
        self.assertEqual(env["OIDC_AUTO_CREATE_USER"], "true")
        self.assertIn("redir /registration.php / 302", _block(load().get("wallos")))

    def test_link_launch_needs_its_details(self):
        base = {
            "client_id": "x",
            "redirect_paths": ["/cb"],
            "launch_path": "/l",
            "env": {"client_id": "A", "client_secret": "B"},
        }
        with self.assertRaises(ValueError):
            Oidc.model_validate(base | {"launch": "link"})
        with self.assertRaises(ValueError):
            Oidc.model_validate(base | {"link_launch": {"page": "/login.php"}})
        Oidc.model_validate(base | {"launch": "link", "link_launch": {"page": "/login.php"}})

    def test_the_launch_page_follows_only_authentiks_authorize_link_on_this_server(self):
        handler = caddy_handler(load().get("wallos").manifest)
        self.assertIn("handle /__mu3lab/login", handler)
        self.assertIn('"page": "/login.php"', handler)
        self.assertIn("url.hostname === location.hostname && !url.port", handler)
        self.assertIn("startsWith('/application/o/authorize/')", handler)
        # Inline scripts run only by their hash; nothing else may load.
        self.assertIn("script-src 'sha256-", handler)


class HomeBoxTests(unittest.TestCase):
    def test_password_sign_in_and_sign_up_are_refused_before_homebox(self):
        block = _block(load().get("homebox"))
        before_proxy = block.split("reverse_proxy", 1)[0]
        for path in (
            "/api/v1/users/login ",
            "/api/v1/users/login/ ",
            "/api/v1/users/register* ",
            "/api/v1/users/forgot-password* ",
            "/api/v1/users/reset-password* ",
        ):
            self.assertIn(f"respond {path}", before_proxy)
        # Authentik sign-in itself still reaches HomeBox.
        self.assertNotIn("respond /api/v1/users/login/oidc", block)

    def test_everyone_signs_in_through_authentik_to_the_shared_inventory(self):
        manifest = load().get("homebox").manifest
        self.assertEqual(manifest.sign_in.oidc.redirect_paths, ("/api/v1/users/login/oidc/callback",))
        self.assertEqual([rule.rule for rule in manifest.rules], ["per_person_logins"])
        env = _env("homebox", "homebox")
        self.assertEqual(env["HBOX_OIDC_AUTO_REDIRECT"], "true")
        self.assertEqual(env["HBOX_OIDC_VERIFY_EMAIL"], "true")


if __name__ == "__main__":
    unittest.main()
