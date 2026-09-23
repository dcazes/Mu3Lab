from __future__ import annotations

import tempfile
import unittest
import sqlite3
import json
from pathlib import Path
from unittest.mock import patch

from ctl.control_state import ControlState
from ctl.identity import mode_for, projection, reconcile_blueprints
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.service_ops import _linked_owner_verified


class IdentityContractTests(unittest.TestCase):
    def test_authentication_modes_are_not_conflated(self):
        registry = load()
        self.assertEqual(mode_for(registry.get("nextcloud")), "native_oidc")
        self.assertEqual(mode_for(registry.get("open-webui")), "trusted_header")
        self.assertEqual(mode_for(registry.get("lobehub")), "native_oidc")
        self.assertEqual(mode_for(registry.get("homarr")), "native_oidc")
        self.assertEqual(mode_for(registry.get("litellm")), "proxy_gate")
        self.assertEqual(mode_for(registry.get("surfsense")), "proxy_gate")
        self.assertEqual(mode_for(registry.get("vaultwarden")), "local")
        self.assertEqual(mode_for(registry.get("ollama")), "none")
        self.assertEqual(mode_for(registry.get("firecrawl")), "none")

    def test_login_free_api_uses_its_identity_note(self):
        service = load().get("firecrawl")
        identity = projection(service, {"route_ready": True, "health_state": "healthy"}, None)
        self.assertEqual(identity["mode"], "none")
        self.assertEqual(identity["detail"], service.identity_note)

    def test_native_oidc_never_becomes_ready_from_manifest_alone(self):
        service = load().get("nextcloud")
        item = {"route_ready": True, "health_state": "healthy",
                "ui": {"url": "https://host.example:8453"}}
        identity = projection(service, item, None)
        self.assertEqual(identity["state"], "unconfigured")

    def test_nextcloud_launcher_enters_oidc_flow_directly(self):
        service = load().get("nextcloud")
        identity = projection(service, {
            "route_ready": True, "health_state": "healthy",
            "url": "https://host.example:8453",
        }, None)
        self.assertEqual(identity["launch_url"],
                         "https://host.example:8453/index.php/apps/user_oidc/login/1")

    def test_identity_state_is_additive_and_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            state.set_service_identity("nextcloud", "native_oidc", "migration_required",
                                       owner_uid="owner-subject", job_id="job-1",
                                       detail="callback required")
            reopened = ControlState(Path(tmp) / "control.sqlite3").service_identity("nextcloud")
            self.assertEqual(reopened["owner_uid"], "owner-subject")
            self.assertEqual(reopened["state"], "migration_required")

    def test_missing_blueprint_is_restored_without_rotating_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime")
            project = paths.projects / "nextcloud"
            project.mkdir(parents=True)
            (project / ".env").write_text(
                "NEXTCLOUD_OIDC_CLIENT_ID=mu3lab-nextcloud\n"
                "NEXTCLOUD_OIDC_CLIENT_SECRET=preserved-secret\n", encoding="utf-8")
            written = reconcile_blueprints(load(), "mu3lab.example.ts.net", paths)
            self.assertIn("nextcloud", written)
            blueprint = paths.projects / "authentik" / "blueprints" / "mu3lab-nextcloud.yaml"
            content = blueprint.read_text(encoding="utf-8")
            self.assertIn("preserved-secret", content)
            self.assertIn("email_verified", content)
            self.assertIn("preferred_username", content)
            self.assertEqual((project / ".env").read_text(encoding="utf-8").count("preserved-secret"), 1)

    def test_mealie_owner_requires_oidc_link_and_admin_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime")
            data = paths.data / "mealie"
            data.mkdir(parents=True)
            conn = sqlite3.connect(data / "mealie.db")
            conn.execute("CREATE TABLE users (email TEXT, admin INTEGER, auth_method TEXT, password TEXT)")
            conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)",
                         ("owner@example.com", 1, "OIDC", "password-hash-is-never-selected"))
            conn.commit(); conn.close()
            with patch("ctl.service_ops.RuntimePaths", return_value=paths):
                verified = _linked_owner_verified(
                    load().get("mealie"), paths.projects / "mealie",
                    {"email": "OWNER@example.com"}, lambda _line: None)
            self.assertTrue(verified)

    def test_nextcloud_owner_requires_matching_email_and_admin_role(self):
        profile = {"user_id": "akadmin", "email": "owner@example.com",
                   "enabled": True, "groups": ["admin"]}
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime")
            with patch("ctl.service_ops.actions.compose_exec",
                       return_value=(0, json.dumps(profile))):
                verified = _linked_owner_verified(
                    load().get("nextcloud"), paths.projects / "nextcloud",
                    {"username": "akadmin", "email": "OWNER@example.com"},
                    lambda _line: None)
            self.assertTrue(verified)

    def test_lobehub_owner_requires_verified_authentik_account(self):
        evidence = "4cb1f144288e5d61695b0d3f9c63835c|t|authentik\n"
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime")
            with patch("ctl.service_ops.actions.compose_exec",
                       return_value=(0, evidence)) as compose_exec:
                verified = _linked_owner_verified(
                    load().get("lobehub"), paths.projects / "lobehub",
                    {"email": "OWNER@example.com"}, lambda _line: None)
            self.assertTrue(verified)
            command = compose_exec.call_args.args[2]
            self.assertNotIn("owner@example.com", " ".join(command).lower())
            self.assertNotIn("access_token", " ".join(command).lower())


if __name__ == "__main__":
    unittest.main()
