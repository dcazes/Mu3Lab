"""First-use provisioning, owner preservation, and callback-driven finalization."""

from __future__ import annotations

import email.message
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from ctl import onboarding_state
from ctl.authentik_blueprints import OidcApp, render_oidc_blueprint
from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext, StepFailed, load_app_hooks
from ctl.engine.loopback import LoopbackError
from ctl.engine.project import Facts
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.vault_setup import DesiredItem, seed
from ctl.workflow_secrets import WorkflowSecretError
from tests.test_vault_setup import EMAIL, PASSWORD, FakeVaultwarden, session_for


def login_response(body: dict, *cookies: str) -> io.BytesIO:
    response = io.BytesIO(json.dumps(body).encode())
    response.headers = email.message.Message()
    for cookie in cookies:
        response.headers["Set-Cookie"] = cookie
    return response


OWNER = {"owner_uid": "owner", "username": "owner", "email": "owner@example.test", "display_name": "Owner"}


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = RuntimePaths(Path(self.tmp.name))
        self.catalog = load().catalog
        app_id = "surfsense" if "surfsense" in self._testMethodName else "immich"
        self.app = self.catalog.get(app_id)
        self.hooks = load_app_hooks(self.app)
        self.globals = self.hooks.bootstrap_account.__globals__
        self.network = SimpleNamespace(request=self.globals["request"])
        self.globals["request"] = lambda *args, **kwargs: self.network.request(*args, **kwargs)

    def provision(self, app_id):
        ctx = HookContext(
            self.app,
            self.paths.projects / app_id,
            Compose(self.paths.projects / app_id),
            lambda _: None,
            lambda *_: None,
            OWNER,
            Facts("host.test", self.paths, self.catalog),
        )
        return self.hooks.bootstrap_account(ctx)

    def test_login_survives_restart_preserves_owner_and_is_removed_only_after_vault_save(self):
        onboarding_state.remember_owner("immich", OWNER, self.paths)
        record = onboarding_state.prepare_login("immich", self.paths)
        onboarding_state.complete_login("immich", OWNER["email"], "https://host.test:8452", self.paths)
        self.assertEqual(onboarding_state.prepare_login("immich", self.paths)["password"], record["password"])
        other = dict(OWNER, owner_uid="other", email="other@example.test")
        self.assertEqual(onboarding_state.remember_owner("immich", other, self.paths), OWNER)
        self.assertEqual(onboarding_state.pending_logins("other", self.paths), [])
        encrypted = (self.paths.runtime / "onboarding-immich.enc").read_bytes()
        self.assertNotIn(record["password"].encode(), encrypted)
        self.assertNotIn(OWNER["email"].encode(), encrypted)
        self.assertEqual((self.paths.runtime / "onboarding.key").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(WorkflowSecretError):
            onboarding_state.vault_saved("immich", "other", self.paths)
        onboarding_state.vault_saved("immich", "owner", self.paths)
        self.assertEqual(onboarding_state.pending_logins("owner", self.paths), [])
        self.assertEqual(onboarding_state.read("immich", self.paths)["owner"], OWNER)
        with self.assertRaises(WorkflowSecretError):
            onboarding_state.prepare_login("immich", self.paths)

    def test_immich_recovers_interrupted_setup_without_duplicate_admin_or_password_rotation(self):
        onboarding_state.remember_owner("immich", OWNER, self.paths)
        with patch.object(
            self.network,
            "request",
            side_effect=[
                {"isInitialized": False},
                {},
                {"accessToken": "test-token", "isAdmin": True},
                LoopbackError("interrupted"),
                None,
            ],
        ) as api:
            with self.assertRaises(StepFailed):
                self.provision("immich")
            password = api.call_args_list[1].kwargs["data"]["password"]
        with patch.object(
            self.network,
            "request",
            side_effect=[
                {"isInitialized": True},
                {"accessToken": "test-token", "isAdmin": True},
                {},
                None,
            ],
        ) as api:
            self.provision("immich")
            self.assertEqual(api.call_args_list[1].kwargs["data"]["password"], password)
            self.assertFalse(any(call.args[1] == "/api/auth/admin-sign-up" for call in api.call_args_list))
        with patch.object(self.network, "request", return_value={"isInitialized": True}) as api:
            self.provision("immich")
            self.assertEqual(api.call_count, 1)  # SSO-only installs cannot use the old local login.

    def test_existing_immich_owner_is_not_replaced(self):
        with patch.object(self.network, "request", return_value={"isInitialized": True}) as api:
            self.provision("immich")
            self.assertEqual(api.call_count, 1)
            self.assertEqual(onboarding_state.read("immich", self.paths), {})

    def test_surfsense_recovers_registration_hook_failure_and_verifies_workspace(self):
        onboarding_state.remember_owner("surfsense", OWNER, self.paths)
        response = login_response({"access_token": "test-token"})
        with (
            patch("urllib.request.urlopen", return_value=response),
            patch.object(
                self.network,
                "request",
                side_effect=[
                    LoopbackError("account already created"),
                    {"email": OWNER["email"], "is_active": True},
                    [],
                    {"id": 7},
                ],
            ) as api,
        ):
            self.provision("surfsense")
            self.assertEqual(api.call_args_list[-1].args[1], "/api/v1/workspaces")
        self.assertEqual(len(onboarding_state.pending_logins("owner", self.paths)), 1)
        with patch.object(self.network, "request") as api:
            self.provision("surfsense")
            api.assert_not_called()

    def test_surfsense_reads_the_session_cookie_login(self):
        onboarding_state.remember_owner("surfsense", OWNER, self.paths)
        response = login_response(
            {"authenticated": True},
            "surfsense_session=cookie-token; HttpOnly; Path=/; SameSite=lax",
            "surfsense_refresh=refresh-token; HttpOnly; Path=/; SameSite=lax",
        )
        with (
            patch("urllib.request.urlopen", return_value=response),
            patch.object(
                self.network,
                "request",
                side_effect=[{}, {"email": OWNER["email"], "is_active": True}, [{"id": 1}]],
            ) as api,
        ):
            self.provision("surfsense")
            self.assertEqual(api.call_args_list[1].kwargs["token"], "cookie-token")

    def test_surfsense_without_any_login_token_fails(self):
        onboarding_state.remember_owner("surfsense", OWNER, self.paths)
        with (
            patch(
                "urllib.request.urlopen",
                return_value=login_response({"authenticated": True}),
            ),
            patch.object(self.network, "request", return_value={}),
            self.assertRaisesRegex(StepFailed, "generated login"),
        ):
            self.provision("surfsense")

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

    def test_unmanaged_vault_item_does_not_discard_unsaved_generated_password(self):
        vault = FakeVaultwarden()
        vault.add_login("My SurfSense", "https://host.test:8447")
        with session_for(vault) as session:
            session.login(EMAIL, PASSWORD)
            result = seed(
                session,
                [
                    DesiredItem(
                        "service:surfsense",
                        "SurfSense",
                        "Mu3Lab",
                        OWNER["email"],
                        (("https://host.test:8447", None),),
                        password="generated",
                        rotate=True,
                        onboarding_service_id="surfsense",
                    )
                ],
            )
        self.assertEqual(result.saved_onboarding, [])
        self.assertEqual(result.skipped, ["SurfSense"])
