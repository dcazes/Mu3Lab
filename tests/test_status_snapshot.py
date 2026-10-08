"""Browser reads use shared observations; personal progress stays personal."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import bootstrap_state
from ctl.api import create_app
from ctl.control_state import ControlState
from ctl.registry import load
from ctl.status import snapshots
from ctl.status.display import projection
from ctl.store import checklist, db
from tests.test_app_security import OPERATOR, PROXY_TOKEN
from tests.test_household_roles import MEMBER, MUTATION


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for target, args in (("os.environ", {"MU3LAB_RUNTIME_ROOT": temporary.name}),):
            patcher = patch.dict(target, args)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in (
            ("ctl.api.security.ingress_token", PROXY_TOKEN),
            ("ctl.api.security.csrf_token", "bound-token"),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        with db.connect(db.database()):
            pass
        self.client = TestClient(create_app(), base_url="https://host.ts.net")

    def test_installed_gateway_does_not_close_snapshot_and_success_verifies_dashboard(self):
        state = ControlState(db.database())
        for service in ("firecrawl", "mealie"):
            state.set_installation(service, "running")
        # The gateway reads tool permissions before the next installation read.
        response = self.client.get("/api/v1/snapshot", headers=OPERATOR)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["identity"]["is_admin"])
        self.assertTrue(bootstrap_state.dashboard_ready_since(0))

    def test_failed_snapshot_and_household_snapshot_do_not_verify_installer(self):
        response = self.client.get("/api/v1/snapshot", headers=MEMBER)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(bootstrap_state.dashboard_ready_since(0))
        with patch("ctl.api.routes.snapshot.chat.chat_status", side_effect=RuntimeError("snapshot failed")):
            client = TestClient(create_app(), raise_server_exceptions=False)
            self.assertEqual(client.get("/api/v1/snapshot", headers=OPERATOR).status_code, 500)
        self.assertFalse(bootstrap_state.dashboard_ready_since(0))

    def test_readiness_from_a_previous_install_does_not_complete_a_new_attempt(self):
        with patch("ctl.bootstrap_state.time.time", return_value=100):
            bootstrap_state.confirm_dashboard_ready()
        self.assertTrue(bootstrap_state.dashboard_ready_since(99))
        self.assertFalse(bootstrap_state.dashboard_ready_since(101))

    def test_services_and_snapshot_never_run_host_commands(self):
        with patch("subprocess.Popen", side_effect=AssertionError("page load ran a host command")):
            admin = self.client.get("/api/v1/services", headers=OPERATOR)
            member = self.client.get("/api/v1/services", headers=MEMBER)
            snapshot = self.client.get("/api/v1/snapshot", headers=MEMBER)
        self.assertEqual(admin.status_code, 200, admin.text)
        self.assertEqual(member.status_code, 200, member.text)
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        self.assertFalse(snapshot.json()["audit"]["available"])
        self.assertEqual(
            [(s["id"], s["display_state"]) for s in admin.json()["services"]],
            [(s["id"], s["display_state"]) for s in member.json()["services"]],
        )
        for service in admin.json()["services"]:
            self.assertNotIn("state", service)
            self.assertNotIn("route_state", service)
            self.assertIn("observed_at", service)

    def test_checklist_merges_devices_and_separates_people(self):
        for item in ("devices", "extension"):
            response = self.client.put(
                "/api/v1/me/checklist", headers=OPERATOR | MUTATION, json={"items": {item: True}}
            )
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.client.get("/api/v1/me/checklist", headers=OPERATOR).json()["items"],
            {"devices": True, "extension": True},
        )
        self.assertEqual(self.client.get("/api/v1/me/checklist", headers=MEMBER).json()["items"], {})
        response = self.client.put(
            "/api/v1/me/checklist", headers=OPERATOR | MUTATION, json={"items": {"anything": True}}
        )
        self.assertEqual(response.status_code, 422)
        checklist.update("subject", {"devices": False})
        self.assertEqual(checklist.read("subject"), {"extension": True})

    def test_validation_does_not_echo_secret_inputs(self):
        secret = "do-not-return-this-master-password"
        response = self.client.post(
            "/api/v1/vault/setup",
            headers=OPERATOR | MUTATION,
            json={"email": "owner@example.com", "master_password": secret, "unexpected": secret},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)
        self.assertEqual(response.json()["code"], "invalid_request")

    def test_stored_observation_is_shared_and_timestamped(self):
        value = {"id": "example", "health_state": "healthy", "detail": "Ready", "containers": [], "route_ready": True}
        snapshots.write([value], "2026-10-04T10:00:00+00:00")
        self.assertEqual(snapshots.read()["example"]["observed_at"], "2026-10-04T10:00:00+00:00")

    def test_display_states_preserve_installed_failed_and_busy_apps(self):
        self.assertEqual(
            projection({"state": "verifying", "installation_state": "partial"})["display_state"], "working"
        )
        self.assertEqual(projection({"state": "degraded", "installation_state": "installed"})["installed"], True)
        self.assertEqual(projection({"state": "planned"})["display_state"], "not_installed")

    def test_legacy_configured_observation_cannot_claim_ready(self):
        service = load().get("mealie")
        observed = service.public() | {
            "state": "ready",
            "health_state": "healthy",
            "route_state": "configured",
            "route_ready": True,
            "detail": "Ready",
            "containers": [],
            "compose_present": True,
            "last_error": "",
            "update": None,
            "ui": {
                "state": "ready",
                "url": "https://test.ts.net:8447",
                "label": "Open",
                "authentication": service.auth,
                "reason": None,
            },
        }
        snapshots.write([observed], "2026-10-08T12:00:00+00:00")
        response = self.client.get("/api/v1/services", headers=OPERATOR)
        self.assertEqual(response.status_code, 200, response.text)
        app = next(item for item in response.json()["services"] if item["id"] == "mealie")
        self.assertFalse(app["route_ready"])
        self.assertEqual(app["display_state"], "needs_attention")
        self.assertIsNone(app["ui"].get("url"))
