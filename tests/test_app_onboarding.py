"""First-use provisioning: the owner's generated login is kept safely, and app admission stays the owner's."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from ctl.authentik_blueprints import OidcApp, render_oidc_blueprint
from ctl.runtime import RuntimePaths
from ctl.store import onboarding as onboarding_state
from ctl.store.workflows import WorkflowSecretError

OWNER = {"owner_uid": "owner", "username": "owner", "email": "owner@example.test", "display_name": "Owner"}


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = RuntimePaths(Path(self.tmp.name))

    def test_login_survives_restart_preserves_owner_and_is_removed_only_after_vault_save(self):
        onboarding_state.remember_owner("immich", OWNER, self.paths)
        record = onboarding_state.prepare_login("immich", self.paths)
        onboarding_state.complete_login("immich", OWNER["email"], "https://host.test:8452", self.paths)
        self.assertEqual(onboarding_state.prepare_login("immich", self.paths)["password"], record["password"])
        other = dict(OWNER, owner_uid="other", email="other@example.test")
        self.assertEqual(onboarding_state.remember_owner("immich", other, self.paths), OWNER)
        self.assertEqual(onboarding_state.pending_logins("other", self.paths), [])
        encrypted = (self.paths.state / "mu3lab.db").read_bytes()
        self.assertNotIn(record["password"].encode(), encrypted)
        self.assertEqual(record["owner"]["email"], OWNER["email"])
        self.assertEqual((self.paths.runtime / "secrets.key").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(WorkflowSecretError):
            onboarding_state.vault_saved("immich", "other", self.paths)
        onboarding_state.vault_saved("immich", "owner", self.paths)
        self.assertEqual(onboarding_state.pending_logins("owner", self.paths), [])
        self.assertEqual(onboarding_state.read("immich", self.paths)["owner"], OWNER)
        with self.assertRaises(WorkflowSecretError):
            onboarding_state.prepare_login("immich", self.paths)

    def test_actual_admission_guard_cannot_be_bypassed_by_group_binding(self):
        content = render_oidc_blueprint(
            "host.example.ts.net",
            OidcApp("actual-budget", "Actual Budget", 8451, "client", "test-secret", ("/openid/callback",), "owner"),
        )
        entries = yaml.load(content, Loader=yaml.BaseLoader)["entries"]
        bindings = [e for e in entries if e["model"] == "authentik_policies.policybinding"]
        # Every group binding (admins, operators, household) is withdrawn; only the owner rule admits.
        self.assertEqual([e["state"] for e in bindings], ["absent", "absent", "absent", "present"])
        policy = next(e for e in entries if e["model"] == "authentik_policies_expression.expressionpolicy")
        self.assertIn("request.user.username == 'owner'", policy["attrs"]["expression"])
        self.assertIn("default-provider-authorization-implicit-consent", content)
        self.assertNotIn("- implicit", content)
