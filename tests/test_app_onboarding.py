"""First-use provisioning, owner preservation, and callback-driven finalization."""

from __future__ import annotations

import email.message
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ctl import identity_reconcile, onboarding_state
from ctl.authentik_blueprints import OidcApp, render_oidc_blueprint
from ctl.integrations.authentik import AuthentikError
from ctl.jobs import JobStore
from ctl.lifecycle.accounts import actual_owner_linked
from ctl.lifecycle.onboarding import OnboardingError, provision_immich, provision_surfsense
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text
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
        service = load().get("immich")
        with patch(
            "ctl.lifecycle.onboarding.request",
            side_effect=[
                {"isInitialized": False},
                {},
                {"accessToken": "test-token", "isAdmin": True},
                OnboardingError("interrupted"),
                None,
            ],
        ) as api:
            with self.assertRaises(OnboardingError):
                provision_immich(service, OWNER, "https://host.test:8452", self.paths)
            password = api.call_args_list[1].kwargs["data"]["password"]
        with patch(
            "ctl.lifecycle.onboarding.request",
            side_effect=[
                {"isInitialized": True},
                {"accessToken": "test-token", "isAdmin": True},
                {},
                None,
            ],
        ) as api:
            provision_immich(service, OWNER, "https://host.test:8452", self.paths)
            self.assertEqual(api.call_args_list[1].kwargs["data"]["password"], password)
            self.assertFalse(any(call.args[1] == "/api/auth/admin-sign-up" for call in api.call_args_list))
        with patch("ctl.lifecycle.onboarding.request", return_value={"isInitialized": True}) as api:
            provision_immich(service, OWNER, "https://host.test:8452", self.paths)
            self.assertEqual(api.call_count, 1)  # SSO-only installs cannot use the old local login.

    def test_existing_immich_owner_is_not_replaced(self):
        with patch("ctl.lifecycle.onboarding.request", return_value={"isInitialized": True}) as api:
            provision_immich(load().get("immich"), OWNER, "https://host.test:8452", self.paths)
            self.assertEqual(api.call_count, 1)
            self.assertEqual(onboarding_state.read("immich", self.paths), {})

    def test_surfsense_recovers_registration_hook_failure_and_verifies_workspace(self):
        onboarding_state.remember_owner("surfsense", OWNER, self.paths)
        response = login_response({"access_token": "test-token"})
        with (
            patch("ctl.lifecycle.onboarding.urllib.request.urlopen", return_value=response),
            patch(
                "ctl.lifecycle.onboarding.request",
                side_effect=[
                    OnboardingError("account already created"),
                    {"email": OWNER["email"], "is_active": True},
                    [],
                    {"id": 7},
                ],
            ) as api,
        ):
            provision_surfsense(load().get("surfsense"), OWNER, "https://host.test:8447", self.paths)
            self.assertEqual(api.call_args_list[-1].args[1], "/api/v1/workspaces")
        self.assertEqual(len(onboarding_state.pending_logins("owner", self.paths)), 1)
        with patch("ctl.lifecycle.onboarding.request") as api:
            provision_surfsense(load().get("surfsense"), OWNER, "https://host.test:8447", self.paths)
            api.assert_not_called()

    def test_surfsense_reads_the_session_cookie_login(self):
        onboarding_state.remember_owner("surfsense", OWNER, self.paths)
        response = login_response(
            {"authenticated": True},
            "surfsense_session=cookie-token; HttpOnly; Path=/; SameSite=lax",
            "surfsense_refresh=refresh-token; HttpOnly; Path=/; SameSite=lax",
        )
        with (
            patch("ctl.lifecycle.onboarding.urllib.request.urlopen", return_value=response),
            patch(
                "ctl.lifecycle.onboarding.request",
                side_effect=[{}, {"email": OWNER["email"], "is_active": True}, [{"id": 1}]],
            ) as api,
        ):
            provision_surfsense(load().get("surfsense"), OWNER, "https://host.test:8447", self.paths)
            self.assertEqual(api.call_args_list[1].kwargs["token"], "cookie-token")

    def test_surfsense_without_any_login_token_fails(self):
        onboarding_state.remember_owner("surfsense", OWNER, self.paths)
        with (
            patch(
                "ctl.lifecycle.onboarding.urllib.request.urlopen",
                return_value=login_response({"authenticated": True}),
            ),
            patch("ctl.lifecycle.onboarding.request", return_value={}),
            self.assertRaisesRegex(OnboardingError, "valid login"),
        ):
            provision_surfsense(load().get("surfsense"), OWNER, "https://host.test:8447", self.paths)

    def test_actual_requires_owner_role_and_live_openid_session(self):
        data = self.paths.data / "actual-budget" / "server-files"
        data.mkdir(parents=True)
        with sqlite3.connect(data / "account.sqlite") as conn:
            conn.execute("CREATE TABLE users (id TEXT,user_name TEXT,role TEXT,enabled INTEGER,owner INTEGER)")
            conn.execute("CREATE TABLE sessions (user_id TEXT,auth_method TEXT,expires_at INTEGER)")
            conn.execute("INSERT INTO users VALUES ('1','owner','ADMIN',1,1)")
            conn.execute("INSERT INTO sessions VALUES ('1','password',-1)")
            conn.commit()
            with patch("ctl.lifecycle.accounts.time.time", return_value=1000):

                def verify():
                    return actual_owner_linked(OWNER, self.paths)

                self.assertFalse(verify())
                conn.execute("UPDATE sessions SET auth_method='openid',expires_at=999")
                conn.commit()
                self.assertFalse(verify())
                conn.execute("UPDATE sessions SET expires_at=-1")
                conn.commit()
                self.assertTrue(verify())
                conn.execute("UPDATE users SET owner=0")
                conn.commit()
                self.assertFalse(verify())

    def test_actual_opens_to_the_household_only_after_the_owner_signs_in(self):
        project = self.paths.projects / "actual-budget"
        project.mkdir(parents=True)
        (project / ".env").write_text(runtime_env_text({"MU3LAB_INITIAL_OWNER_USERNAME": "owner"}), encoding="utf-8")
        onboarding_state.remember_owner("actual-budget", OWNER, self.paths)
        store = JobStore(self.paths.runtime / "control.sqlite3")
        with (
            patch("ctl.identity_reconcile.actual_owner_linked", return_value=False) as linked,
            patch("ctl.identity_reconcile.sync_sign_in") as reconcile,
            patch("ctl.identity_reconcile.Authentik.runtime"),
            patch("ctl.service_state.tailnet_dns_name", return_value="mu3lab.example.ts.net"),
        ):
            self.assertFalse(
                identity_reconcile.lift_owner_guard(store, Path(self.tmp.name), lambda _: None, self.paths)
            )
            reconcile.assert_not_called()
            linked.return_value = True
            self.assertTrue(identity_reconcile.lift_owner_guard(store, Path(self.tmp.name), lambda _: None, self.paths))
            self.assertFalse(
                identity_reconcile.lift_owner_guard(store, Path(self.tmp.name), lambda _: None, self.paths)
            )
        self.assertEqual(read_runtime_env(project / ".env")["MU3LAB_INITIAL_OWNER_USERNAME"], "")
        reconcile.assert_called_once()
        self.assertEqual(store.jobs(), [])

    def test_owner_guard_retries_after_authentik_refuses_the_update(self):
        project = self.paths.projects / "actual-budget"
        project.mkdir(parents=True)
        env_path = project / ".env"
        env_path.write_text(runtime_env_text({"MU3LAB_INITIAL_OWNER_USERNAME": "owner"}))
        original = env_path.read_text()
        onboarding_state.remember_owner("actual-budget", OWNER, self.paths)
        store = JobStore(self.paths.runtime / "control.sqlite3")
        with (
            patch("ctl.identity_reconcile.actual_owner_linked", return_value=True),
            patch("ctl.identity_reconcile.Authentik.runtime"),
            patch("ctl.identity_reconcile.sync_sign_in", side_effect=AuthentikError("Not ready")),
        ):
            self.assertFalse(
                identity_reconcile.lift_owner_guard(store, Path(self.tmp.name), lambda _: None, self.paths)
            )
        self.assertEqual(env_path.read_text(), original)

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
