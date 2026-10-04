"""Golden routes retain existing access behavior while following manifests.

Compared with the before fixtures: the generic renderer uses an explicit
ordered route, no redundant handle around a plain proxy, the declared
forwarded-host mode, and generic launcher body fields. Token bypass now uses
mutually exclusive handles. The trusted-header comment is shorter but keeps
Caddy's delete-after-set warning. LiteLLM's former plain generated route is
now gated as its manifest requires (the old base previously supplied the gate).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from ctl.registry import load
from ctl.routes import _block
from tools.check_no_app_ids import violations

FIXTURES = Path(__file__).parent / "fixtures/routes"


class ManifestRoutesTests(unittest.TestCase):
    def test_reviewed_routes_match_golden_files(self):
        for app_id in ("mealie", "surfsense", "baby-buddy", "actual-budget", "litellm"):
            with self.subTest(app=app_id):
                self.assertEqual(_block(load().get(app_id)) + "\n", (FIXTURES / (app_id + ".caddy")).read_text())

    def test_shared_code_has_no_app_id_literals(self):
        self.assertEqual(violations(), [])

    def test_phone_token_bypass_strips_identity_and_browser_gets_verified_identity(self):
        block = _block(load().get("baby-buddy"))
        bypass, browser = block.split("\t\thandle {", 1)
        self.assertIn("header_up -Remote-User", bypass)
        self.assertNotIn("forward_auth", bypass)
        self.assertIn("forward_auth", browser)
        self.assertIn("header_up Remote-User {http.request.header.X-Authentik-Username}", browser)
        self.assertNotIn("header_up -Remote-User", browser)

    def test_closed_registration_follows_the_manifest(self):
        self.assertIn(
            'respond /auth/register* "Accounts are provisioned by Mu3Lab." 403', _block(load().get("surfsense"))
        )
