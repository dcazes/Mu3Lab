from __future__ import annotations

import tempfile
import unittest
import sqlite3
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
        self.assertEqual(mode_for(registry.get("litellm")), "proxy_gate")
        self.assertEqual(mode_for(registry.get("surfsense")), "proxy_gate")
        self.assertEqual(mode_for(registry.get("vaultwarden")), "local")
        self.assertEqual(mode_for(registry.get("ollama")), "none")

    def test_native_oidc_never_becomes_ready_from_manifest_alone(self):
        service = load().get("nextcloud")
        item = {"route_ready": True, "health_state": "healthy",
                "ui": {"url": "https://host.example:8453"}}
        identity = projection(service, item, None)
        self.assertEqual(identity["state"], "unconfigured")

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


if __name__ == "__main__":
    unittest.main()
