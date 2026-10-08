"""R20: usable status needs fresh, successful required checks."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl.api import create_app
from ctl.control_state import ControlState
from ctl.registry import load
from ctl.service_state import status
from ctl.status import checks, sign_in, snapshots
from ctl.status.checks import Check, iso, route_check, sign_in_check, sign_in_due
from ctl.store import db, records
from tests.test_app_security import OPERATOR, PROXY_TOKEN

ROOT = Path(__file__).resolve().parents[1]
NOW = 2_000_000_000.0


def reset_state():
    """The suite shares one temporary runtime root; start each test from empty observations."""
    with db.connect(db.database()) as connection:
        for table in ("status_snapshot", "service_identity_state"):
            connection.execute(f"DELETE FROM {table}")


def fold(previous, *, probe=None, configured=True, healthy=True, dns=True, now=NOW):
    return route_check(
        previous, required=True, configured=configured, healthy=healthy, dns_known=dns, probe=probe, now=now
    )


class RouteHistoryTests(unittest.TestCase):
    def test_one_missed_check_after_success_stays_usable_then_second_miss_fails(self):
        working = fold(None, probe=(True, NOW - 30))
        self.assertEqual(working.state, "pass")
        first = fold(working, probe=(False, NOW))
        self.assertEqual(first.state, "checking")
        self.assertTrue(first.satisfied)
        self.assertEqual(first.last_success_at, iso(NOW - 30))
        # The same cached probe seen by the next cycle is not another failure.
        repeated = fold(first, probe=(False, NOW), now=NOW + 5)
        self.assertEqual((repeated.state, repeated.failures), ("checking", 1))
        second = fold(repeated, probe=(False, NOW + 30), now=NOW + 30)
        self.assertEqual(second.state, "fail")
        self.assertFalse(second.satisfied)
        self.assertEqual(second.last_success_at, iso(NOW - 30))

    def test_grace_is_bounded_by_time_since_last_success(self):
        old = fold(None, probe=(True, NOW - checks.ROUTE_GRACE_SECONDS - 1))
        self.assertEqual(fold(old, probe=(False, NOW)).state, "fail")

    def test_never_verified_route_does_not_get_grace(self):
        self.assertEqual(fold(None, probe=(False, NOW)).state, "fail")

    def test_recovery_needs_a_real_successful_probe(self):
        failed = fold(fold(fold(None, probe=(True, NOW - 60)), probe=(False, NOW - 40)), probe=(False, NOW - 10))
        self.assertEqual(failed.state, "fail")
        recovered = fold(failed, probe=(True, NOW))
        self.assertEqual((recovered.state, recovered.failures, recovered.last_success_at), ("pass", 0, iso(NOW)))

    def test_missing_serve_route_fails_without_grace(self):
        working = fold(None, probe=(True, NOW - 5))
        missing = fold(working, configured=False)
        self.assertEqual(missing.state, "fail")
        self.assertIn("Tailscale Serve", missing.detail)

    def test_unreadable_serve_status_lets_the_probe_decide(self):
        self.assertEqual(fold(None, configured=None, probe=(True, NOW)).state, "pass")

    def test_unknown_address_counts_as_a_missed_check(self):
        working = fold(None, probe=(True, NOW - 5))
        self.assertEqual(fold(working, dns=False).state, "checking")
        self.assertEqual(fold(fold(working, dns=False), dns=False, now=NOW + 5).state, "fail")

    def test_unhealthy_process_keeps_history_but_is_pending(self):
        working = fold(None, probe=(True, NOW - 5))
        paused = fold(working, healthy=False)
        self.assertEqual((paused.state, paused.last_success_at), ("pending", iso(NOW - 5)))
        self.assertFalse(paused.satisfied)

    def test_malformed_history_is_ignored(self):
        self.assertIsNone(Check.load({"state": "green"}))
        self.assertEqual(Check.load({"state": "fail", "failures": "lots"}).failures, 0)


class WorkerObservationTests(unittest.TestCase):
    def observe(self, answers, previous=None, *, ports=None, readable=True):
        service = load().get("mealie")
        with (
            patch("ctl.service_state._healthy", return_value=(True, "healthy")),
            patch("ctl.service_state._compose_state", return_value="running"),
            patch("ctl.service_state.route_probe", return_value=(answers, NOW)) as probe,
        ):
            item = status(
                service,
                "test.ts.net",
                ROOT,
                {service.private_https_port} if ports is None else ports,
                serve_readable=readable,
                previous=previous,
                now=NOW,
            )
        return item, probe

    def test_route_miss_inside_grace_keeps_route_ready_and_history(self):
        previous = {"checks": {"route": fold(None, probe=(True, NOW - 20)).public()}}
        item, _probe = self.observe(False, previous)
        self.assertTrue(item["route_ready"])
        self.assertEqual(item["checks"]["route"]["state"], "checking")
        self.assertEqual(item["state"], "ready")

    def test_missing_serve_route_is_not_probed_and_is_reported(self):
        item, probe = self.observe(True, ports=set())
        probe.assert_not_called()
        self.assertFalse(item["route_ready"])
        self.assertEqual(item["blocking_check"], "route")
        self.assertIn("Tailscale Serve", item["detail"])

    def test_unreadable_serve_status_still_verifies_by_probe(self):
        item, probe = self.observe(True, ports=set(), readable=False)
        probe.assert_called_once()
        self.assertTrue(item["route_ready"])


class ServiceReadTests(unittest.TestCase):
    def setUp(self):
        for target, value in (
            ("ctl.api.security.ingress_token", PROXY_TOKEN),
            ("ctl.api.security.csrf_token", "bound-token"),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        reset_state()
        self.addCleanup(reset_state)
        records.put("status", "network", {"dns_name": "test.ts.net"})
        self.client = TestClient(create_app(), base_url="https://host.ts.net")
        self.state = ControlState(db.database())

    def write(self, *, answers=True, previous=None, observed_at=None):
        service = load().get("mealie")
        now = time.time()
        with (
            patch("ctl.service_state._healthy", return_value=(True, "healthy")),
            patch("ctl.service_state._compose_state", return_value="running"),
            patch("ctl.service_state.route_probe", return_value=(answers, now)),
        ):
            item = status(service, "test.ts.net", ROOT, {service.private_https_port}, previous=previous, now=now)
        item["containers"] = []
        snapshots.write([item], observed_at or iso(now))

    def verify_sign_in(self, *, age=0.0, state="ready"):
        self.state.set_service_identity("mealie", "native_oidc", state, detail="checked", verified=state == "ready")
        if age:
            with db.connect(db.database()) as connection:
                connection.execute(
                    "UPDATE service_identity_state SET last_verified_at=? WHERE service_id='mealie'",
                    (iso(time.time() - age),),
                )

    def mealie(self):
        response = self.client.get("/api/v1/services", headers=OPERATOR)
        self.assertEqual(response.status_code, 200, response.text)
        return next(item for item in response.json()["services"] if item["id"] == "mealie")

    def test_all_checks_passing_is_usable(self):
        self.write()
        self.verify_sign_in()
        app = self.mealie()
        self.assertEqual(app["display_state"], "running")
        self.assertTrue(app["ui"]["url"])
        self.assertEqual(
            {name: check["state"] for name, check in app["checks"].items()},
            dict.fromkeys(("process", "route", "sign_in"), "pass"),
        )

    def test_stale_observer_suppresses_usable_claim(self):
        self.write(observed_at=iso(time.time() - checks.OBSERVATION_MAX_AGE_SECONDS - 5))
        self.verify_sign_in()
        app = self.mealie()
        self.assertEqual(app["display_state"], "needs_attention")
        self.assertFalse(app["route_ready"])
        self.assertIsNone(app["ui"].get("url"))
        self.assertEqual(app["identity"]["launch_url"], "")
        self.assertIn("not refreshed", app["reason"])
        self.assertEqual(app["checks"]["route"]["state"], "stale")

    def test_route_grace_is_shown_as_checking_and_keeps_open(self):
        self.write(previous={"checks": {"route": fold(None, probe=(True, time.time() - 10)).public()}}, answers=False)
        self.verify_sign_in()
        app = self.mealie()
        self.assertEqual(app["display_state"], "checking")
        self.assertTrue(app["ui"]["url"])
        self.assertTrue(app["checks"]["route"]["last_success_at"])

    def test_failed_route_names_the_route(self):
        self.write(answers=False)
        self.verify_sign_in()
        app = self.mealie()
        self.assertEqual((app["display_state"], app["blocking_check"]), ("needs_attention", "route"))
        self.assertIn("route", app["reason"])

    def test_unverified_sign_in_is_checking_without_a_launch_link(self):
        self.write()
        app = self.mealie()
        self.assertEqual((app["display_state"], app["blocking_check"]), ("checking", "sign_in"))
        self.assertIsNone(app["ui"].get("url"))

    def test_expired_sign_in_evidence_needs_attention(self):
        self.write()
        self.verify_sign_in(age=checks.SIGN_IN_MAX_AGE_SECONDS + 60)
        app = self.mealie()
        self.assertEqual((app["display_state"], app["blocking_check"]), ("needs_attention", "sign_in"))
        self.assertIn("24 hours", app["reason"])
        self.assertIsNone(app["ui"].get("url"))

    def test_failed_sign_in_check_needs_attention(self):
        self.write()
        self.verify_sign_in(state="degraded")
        app = self.mealie()
        self.assertEqual((app["display_state"], app["checks"]["sign_in"]["state"]), ("needs_attention", "fail"))

    def test_internal_service_needs_neither_route_nor_sign_in(self):
        service = load().get("ollama")
        with (
            patch("ctl.service_state._healthy", return_value=(True, "healthy")),
            patch("ctl.service_state._compose_state", return_value="running"),
        ):
            item = status(service, "test.ts.net", ROOT, set())
        item["containers"] = []
        snapshots.write([item], iso(time.time()))
        response = self.client.get("/api/v1/services", headers=OPERATOR)
        app = next(item for item in response.json()["services"] if item["id"] == "ollama")
        self.assertEqual(app["display_state"], "running")
        self.assertEqual(app["checks"]["route"]["state"], "not_required")
        self.assertEqual(app["checks"]["sign_in"]["state"], "not_required")


class SignInEvidenceTests(unittest.TestCase):
    def test_manual_login_apps_never_claim_verified_sign_in(self):
        for app in ("vaultwarden", "authentik", "ollama"):
            self.assertEqual(sign_in_check(load().get(app), {"state": "ready"}, NOW).state, "not_required")

    def test_due_schedule(self):
        service = load().get("mealie")
        self.assertTrue(sign_in_due(service, None, NOW))
        fresh = {"state": "ready", "last_verified_at": iso(NOW - 60)}
        self.assertFalse(sign_in_due(service, fresh, NOW))
        old = {"state": "ready", "last_verified_at": iso(NOW - checks.SIGN_IN_RECHECK_SECONDS)}
        self.assertTrue(sign_in_due(service, old, NOW))
        failed = {"state": "degraded", "updated_at": iso(NOW - 60)}
        self.assertFalse(sign_in_due(service, failed, NOW))
        self.assertTrue(sign_in_due(service, failed, NOW + checks.SIGN_IN_RETRY_SECONDS))
        self.assertFalse(sign_in_due(service, {"state": "configuring"}, NOW))


class SignInRefreshTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.addCleanup(reset_state)
        records.put("status", "network", {"dns_name": "test.ts.net"})
        # Other suites leave jobs behind in the shared store; only the busy test declares one.
        patcher = patch("ctl.status.sign_in.JobStore.runtime", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = ControlState(db.database())
        working = {"route": fold(None, probe=(True, time.time())).public()}
        snapshots.write(
            [
                {
                    "id": "mealie",
                    "health_state": "healthy",
                    "detail": "",
                    "containers": [],
                    "route_ready": True,
                    "checks": working,
                },
                {
                    "id": "immich",
                    "health_state": "healthy",
                    "detail": "",
                    "containers": [],
                    "route_ready": False,
                    "checks": {"route": fold(None, probe=(False, time.time())).public()},
                },
            ],
            iso(time.time()),
        )

    def test_due_app_with_working_route_is_rechecked_once_per_step(self):
        with patch("ctl.status.sign_in.verify_sign_in", return_value="Sign-in through Authentik verified.") as verify:
            self.assertEqual(sign_in.refresh(lambda _line: None), 1)
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[:2], ("mealie", "test.ts.net"))
        self.assertEqual(verify.call_args.kwargs, {"patience": 0})
        saved = self.state.service_identity("mealie")
        self.assertEqual(saved["state"], "ready")
        self.assertTrue(saved["last_verified_at"])
        # Fresh evidence is not checked again.
        with patch("ctl.status.sign_in.verify_sign_in") as verify:
            self.assertEqual(sign_in.refresh(lambda _line: None), 0)
        verify.assert_not_called()

    def test_failed_check_is_recorded_without_secrets(self):
        error = sign_in.SignInError("Authentik refused secret=abc123")
        with patch("ctl.status.sign_in.verify_sign_in", side_effect=error):
            sign_in.refresh(lambda _line: None)
        saved = self.state.service_identity("mealie")
        self.assertEqual(saved["state"], "degraded")
        self.assertEqual(saved["last_error"]["code"], "sign_in_failed")

    def test_busy_app_is_left_to_its_job(self):
        with (
            patch("ctl.status.sign_in.JobStore.runtime") as runtime,
            patch("ctl.status.sign_in.verify_sign_in") as verify,
        ):
            runtime.return_value.active_jobs.return_value = [{"service_id": "mealie"}]
            self.assertEqual(sign_in.refresh(lambda _line: None), 0)
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
