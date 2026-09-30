"""First-use provisioning, owner preservation, and callback-driven finalization."""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ctl import identity_reconcile, onboarding_state
from ctl.authentik_blueprints import render_oidc_application_blueprint
from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.lifecycle.accounts import linked_owner_verified
from ctl.lifecycle.onboarding import OnboardingError, provision_immich, provision_surfsense
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.vault_setup import DesiredItem, seed
from ctl.workflow_secrets import WorkflowSecretError
from tests.support import runtime_paths
from tests.test_vault_setup import EMAIL, PASSWORD, FakeVaultwarden, session_for

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
        response = io.BytesIO(json.dumps({"access_token": "test-token"}).encode())
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

    def test_nextcloud_local_admin_login_is_not_mistaken_for_oidc_callback(self):
        profile = {
            "user_id": "owner",
            "email": OWNER["email"],
            "enabled": True,
            "groups": ["admin"],
            "last_seen": "2026-09-30T12:00:00Z",
        }
        with patch(
            "ctl.lifecycle.accounts.actions.compose_exec", side_effect=[(0, json.dumps(profile)), (1, "missing")]
        ):
            self.assertFalse(
                linked_owner_verified(load().get("nextcloud"), self.paths.projects / "nextcloud", OWNER, lambda _: None)
            )

    def test_actual_requires_owner_role_and_live_openid_session(self):
        data = self.paths.data / "actual-budget" / "server-files"
        data.mkdir(parents=True)
        with sqlite3.connect(data / "account.sqlite") as conn:
            conn.execute("CREATE TABLE users (id TEXT,user_name TEXT,role TEXT,enabled INTEGER,owner INTEGER)")
            conn.execute("CREATE TABLE sessions (user_id TEXT,auth_method TEXT,expires_at INTEGER)")
            conn.execute("INSERT INTO users VALUES ('1','owner','ADMIN',1,1)")
            conn.execute("INSERT INTO sessions VALUES ('1','password',-1)")
            conn.commit()
            with runtime_paths(self.paths), patch("ctl.lifecycle.accounts.time.time", return_value=1000):

                def verify():
                    return linked_owner_verified(
                        load().get("actual-budget"), self.paths.projects / "actual-budget", OWNER, lambda _: None
                    )

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

    def test_worker_queues_finalization_once_and_only_after_callback_evidence(self):
        state = ControlState(self.paths.runtime / "control.sqlite3")
        store = JobStore(self.paths.runtime / "control.sqlite3")
        state.set_installation("immich", "running")
        state.set_service_identity("immich", "native_oidc", "migration_required", owner_uid="owner")
        with (
            patch("ctl.identity_reconcile.ControlState.runtime", return_value=state),
            patch("ctl.identity_reconcile.RuntimePaths", return_value=self.paths),
            patch("ctl.identity_reconcile.onboarding_state.read", return_value={"owner": OWNER}),
            patch("ctl.identity_reconcile.linked_owner_verified", return_value=False) as verified,
        ):
            identity_reconcile.queue_verified(store, Path(self.tmp.name), lambda _: None)
            self.assertEqual(store.jobs(), [])
            verified.return_value = True
            identity_reconcile.queue_verified(store, Path(self.tmp.name), lambda _: None)
            identity_reconcile.queue_verified(store, Path(self.tmp.name), lambda _: None)
            self.assertEqual(len(store.jobs()), 1)
            self.assertEqual(store.jobs()[0]["action"], "configure_identity")

    def test_upgrade_preserves_owner_and_stages_existing_ready_app_only_once(self):
        from ctl.api.routes.services import resume_onboarding

        state = ControlState(self.paths.runtime / "control.sqlite3")
        store = JobStore(self.paths.runtime / "control.sqlite3")
        state.set_installation("immich", "running")
        state.set_service_identity("immich", "native_oidc", "ready", owner_uid="owner")
        onboarding_state.remember_owner("immich", OWNER, self.paths)
        record = onboarding_state.read("immich", self.paths)
        with (
            patch("ctl.api.routes.services.runtime.control_state", return_value=state),
            patch("ctl.api.routes.services.runtime.job_store", return_value=store),
            patch("ctl.api.routes.services.runtime.registry", return_value=load()),
            patch("ctl.api.routes.services.onboarding_state.read", return_value=record),
            patch("ctl.api.routes.services.onboarding_state.remember_owner") as remember,
        ):
            self.assertEqual(resume_onboarding({"subject_id": "other"})["adopted"], [])
            remember.assert_not_called()
            operator = {"subject_id": "owner", "email": OWNER["email"], "username": "owner"}
            self.assertEqual(resume_onboarding(operator)["adopted"], ["immich"])
            self.assertEqual(resume_onboarding(operator)["adopted"], [])
            self.assertEqual(len(store.jobs()), 1)
            # A current definition does not need to restart on later visits.
            store.transition(store.jobs()[0]["id"], "running", actor="worker")
            store.transition(store.jobs()[0]["id"], "succeeded", actor="worker")
            state.set_service_identity("immich", "native_oidc", "ready", owner_uid="owner")
            record["config_version"] = onboarding_state.CONFIG_VERSION
            self.assertEqual(resume_onboarding(operator)["adopted"], [])

    def test_actual_admission_guard_cannot_be_bypassed_by_group_binding(self):
        content = render_oidc_application_blueprint(
            "host.example.ts.net",
            service_id="actual-budget",
            name="Actual Budget",
            private_port=8451,
            client_id="client",
            client_secret="test-secret",
            redirect_paths=("/openid/callback",),
            initial_owner="owner",
        )
        entries = yaml.load(content, Loader=yaml.BaseLoader)["entries"]
        bindings = [e for e in entries if e["model"] == "authentik_policies.policybinding"]
        self.assertEqual([e["state"] for e in bindings], ["absent", "absent", "present"])
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
