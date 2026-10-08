"""R07: deactivation denies at once and revokes every Mu3Lab credential until done."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from ctl import access, people
from ctl.api import create_app
from ctl.api.security import resolve_identity
from ctl.integrations.authentik import Authentik, AuthentikError
from ctl.runtime import RuntimePaths
from gateway_authority import Authority
from tests.test_app_security import OPERATOR, PROXY_TOKEN, request
from tests.test_people import AuthentikFixture

ORIGIN = "https://mu3lab.example.ts.net"


class AccessCase(AuthentikFixture, unittest.TestCase):
    """The people tests' Authentik fixture, with an isolated access store and gateway authority."""

    def setUp(self):
        super().setUp()
        client = Authentik("test-token", transport=httpx.MockTransport(_sessions_fixture(self)))
        for target, value in (
            ("ctl.people.Authentik.runtime", client),
            ("ctl.integrations.authentik.Authentik.runtime", client),
            ("ctl.api.security.ingress_token", PROXY_TOKEN),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name))
        for module in ("ctl.people", "ctl.access"):
            patcher = patch(f"{module}.RuntimePaths", return_value=self.paths)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.authority = Authority(self.paths.projects / "mcp-gateway" / "authority")
        self.outages: set[str] = set()
        self.revoked: list[tuple[str, str]] = []

        def outage(target):
            def handler(subject, username):
                if target in self.outages:
                    raise RuntimeError(f"{target} is unavailable")
                self.revoked.append((target, subject))

            return handler

        handlers = dict(access.HANDLERS) | {name: outage(name) for name in ("voice_key", "chat")}
        patcher = patch.dict(access.HANDLERS, handlers)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pending(self, uid="uid-1"):
        entry = access.holds().get(uid)
        return sorted(task["target"] for task in entry["tasks"] if task["state"] == "pending") if entry else []


class DeactivationTests(AccessCase):
    def setUp(self):
        super().setUp()
        people.change("member", "role", ORIGIN, "admin")
        self.authority.grant_subject("uid-1", operator=True)
        self.token = self.authority.credential("uid-1", "demo")

    def test_deactivation_while_voice_and_chat_are_down_completes_after_recovery(self):
        self.outages = {"voice_key", "chat"}
        result = people.change("owner", "deactivate", ORIGIN, actor="admin-uid")
        self.assertFalse(result["person"]["active"])
        self.assertEqual(result["person"]["access"]["state"], "pending")
        # Mu3Lab's own API denies at once, whatever the Authentik session still claims.
        identity = resolve_identity(request(OPERATOR | {"x-authentik-username": "owner", "x-authentik-uid": "uid-1"}))
        self.assertEqual((identity["role"], identity["writes_enabled"]), ("", False))
        self.assertIn("deactivated", identity["detail"])
        # Chat tool access ends before anything that needs another service.
        access.run(lambda _line: None, subject="uid-1", targets=("gateway",))
        with self.assertRaises(ValueError):
            self.authority.authenticate(self.token, "demo")

        remaining = access.run(lambda _line: None, subject="uid-1")
        self.assertEqual(remaining, 2)
        self.assertEqual(self.pending(), ["chat", "voice_key"])
        self.assertFalse(self.users[0]["is_active"])
        summary = people.list_people()[-1]["access"]
        self.assertIn("2 access revocations pending", summary["detail"])
        self.assertIn("unavailable", summary["detail"])

        # Not yet due: the worker waits out the backoff instead of hammering a down service.
        self.outages.clear()
        self.assertEqual(access.run(lambda _line: None), 2)
        # After recovery the worker finishes without anyone asking again.
        self.assertEqual(access.run(lambda _line: None, now=lambda: 4_000_000_000.0), 0)
        self.assertEqual(sorted(target for target, _ in self.revoked), ["chat", "voice_key"])
        self.assertEqual(people.list_people()[-1]["access"]["state"], "complete")
        self.assertIn(("DELETE", "/core/authenticated_sessions/s-1/"), self.calls)

    def test_backoff_doubles_and_is_capped(self):
        self.outages = {"voice_key"}
        people.change("owner", "deactivate", ORIGIN)
        clock = [1000.0]
        delays = []
        for _ in range(9):
            access.run(lambda _line: None, now=lambda: clock[0])
            task = next(t for t in access.holds()["uid-1"]["tasks"] if t["target"] == "voice_key")
            delays.append(task["next_attempt_at"] - clock[0])
            clock[0] = task["next_attempt_at"]
        self.assertEqual(delays[:4], [30, 60, 120, 240])
        self.assertEqual(delays[-1], access.RETRY_MAX_SECONDS)

    def test_authentik_outage_does_not_lose_the_deactivation(self):
        with patch.object(Authentik, "update_user", side_effect=AuthentikError("Authentik could not be reached")):
            people.change("owner", "deactivate", ORIGIN)
            access.run(lambda _line: None, subject="uid-1")
        self.assertIn("authentik_account", self.pending())
        self.assertTrue(self.users[0]["is_active"])
        access.run(lambda _line: None, now=lambda: 4_000_000_000.0)
        self.assertFalse(self.users[0]["is_active"])
        self.assertNotIn("authentik_account", self.pending())

    def test_operator_credentials_are_not_reissued_while_held(self):
        people.change("owner", "deactivate", ORIGIN)
        self.assertNotIn("uid-1", people.operator_subjects())

    def test_reactivation_releases_the_hold_and_cancels_revocation(self):
        self.outages = {"voice_key", "chat"}
        people.change("owner", "deactivate", ORIGIN)
        result = people.change("owner", "reactivate", ORIGIN)
        self.assertTrue(result["person"]["active"])
        self.assertNotIn("access", result["person"])
        self.assertIsNone(access.hold("uid-1"))
        self.assertEqual(access.run(lambda _line: None, now=lambda: 4_000_000_000.0), 0)

    def test_repeating_a_deactivation_runs_revocation_again(self):
        people.change("owner", "deactivate", ORIGIN)
        access.run(lambda _line: None, subject="uid-1")
        self.assertEqual(self.pending(), [])
        people.change("owner", "deactivate", ORIGIN)
        self.assertEqual(len(self.pending()), len(access.TARGETS["deactivated"]))

    def test_last_effective_admin_cannot_remove_themselves_through_a_pending_hold(self):
        people.change("owner", "role", ORIGIN, "member")
        with self.assertRaisesRegex(people.PeopleError, "at least one"):
            people.change("member", "deactivate", ORIGIN)


class DemotionTests(AccessCase):
    def setUp(self):
        super().setUp()
        people.change("member", "role", ORIGIN, "admin")

    def test_demotion_denies_administration_but_keeps_membership(self):
        people.change("owner", "role", ORIGIN, "member")
        identity = resolve_identity(request(OPERATOR | {"x-authentik-username": "owner", "x-authentik-uid": "uid-1"}))
        self.assertFalse(identity["is_admin"])
        self.assertTrue(identity["writes_enabled"])
        self.assertEqual(identity["role"], "member")
        self.assertEqual(self.pending(), ["authentik_role", "chat", "gateway"])

    def test_failed_group_change_is_retried_and_listed_as_member(self):
        with patch.object(Authentik, "remove_from_group", side_effect=AuthentikError("Authentik could not be reached")):
            result = people.change("owner", "role", ORIGIN, "member")
        self.assertEqual(result["person"]["role"], "member")
        self.assertIn("authentik Admins", self.users[0]["groups"])
        access.run(lambda _line: None, subject="uid-1")
        self.assertNotIn("authentik Admins", self.users[0]["groups"])
        self.assertEqual(self.pending(), [])

    def test_reactivation_keeps_an_unfinished_demotion(self):
        with patch.object(Authentik, "remove_from_group", side_effect=AuthentikError("down")):
            people.change("owner", "role", ORIGIN, "member")
        people.change("owner", "deactivate", ORIGIN)
        people.change("owner", "reactivate", ORIGIN)
        self.assertEqual(access.hold("uid-1")["kind"], "demoted")

    def test_promotion_releases_a_demotion(self):
        people.change("owner", "role", ORIGIN, "member")
        people.change("owner", "role", ORIGIN, "admin")
        self.assertIsNone(access.hold("uid-1"))
        self.assertIn("uid-1", people.operator_subjects())


class SessionEndTests(unittest.TestCase):
    def test_sessions_and_tokens_of_only_that_person_are_deleted(self):
        calls = []

        def send(req):
            calls.append((req.method, req.url.path.removeprefix("/api/v3"), dict(req.url.params)))
            path = req.url.path.removeprefix("/api/v3")
            if path == "/core/authenticated_sessions/":
                return httpx.Response(
                    200,
                    json={"results": [{"uuid": "a", "user": 7}, {"uuid": "b", "user": {"username": "other"}}]},
                )
            if path == "/core/tokens/":
                return httpx.Response(
                    200, json={"results": [{"identifier": "app-pass", "user_obj": {"username": "p"}}]}
                )
            return httpx.Response(204)

        ended = Authentik("t", transport=httpx.MockTransport(send)).end_sessions("p")
        self.assertEqual(ended, 2)
        deletes = [path for method, path, _ in calls if method == "DELETE"]
        self.assertEqual(deletes, ["/core/authenticated_sessions/a/", "/core/tokens/app-pass/"])
        self.assertTrue(all(params.get("user__username") == "p" for method, _, params in calls if method == "GET"))

    def test_username_reused_by_someone_else_is_never_touched(self):
        client = patch.object(Authentik, "runtime").start()
        self.addCleanup(patch.stopall)
        client.return_value.user.return_value = {"pk": 9, "uid": "someone-new", "is_active": True}
        access._authentik_account("old-uid", "reused")
        access._authentik_sessions("old-uid", "reused")
        client.return_value.update_user.assert_not_called()
        client.return_value.end_sessions.assert_not_called()


class ApiDenialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        paths = RuntimePaths(Path(temporary.name))
        patcher = patch("ctl.access.RuntimePaths", return_value=paths)
        patcher.start()
        self.addCleanup(patcher.stop)
        for target, value in (
            ("ctl.api.security.ingress_token", PROXY_TOKEN),
            ("ctl.api.security.csrf_token", "bound-token"),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(), base_url="https://host.ts.net")

    def test_held_operator_session_cannot_administer(self):
        self.assertEqual(self.client.get("/api/v1/system/config", headers=OPERATOR).status_code, 200)
        access.record(OPERATOR["x-authentik-uid"], "owner", "deactivated", "admin")
        response = self.client.get("/api/v1/system/config", headers=OPERATOR)
        self.assertEqual(response.status_code, 403)
        me = self.client.get("/api/v1/identity", headers=OPERATOR)
        if me.status_code == 200:
            self.assertFalse(me.json()["writes_enabled"])

    def test_unreadable_access_store_fails_closed(self):
        access.record("someone", "someone", "demoted", "admin")
        with patch("ctl.access.db.connect", side_effect=__import__("sqlite3").OperationalError("locked")):
            self.assertEqual(access.hold("anyone")["kind"], "deactivated")


def _sessions_fixture(case: AuthentikFixture):
    """Teach the people fixture Authentik's session and token endpoints."""
    original = case._send

    def send(req):
        path = req.url.path.removeprefix("/api/v3")
        if path == "/core/authenticated_sessions/":
            case.calls.append((req.method, path))
            return httpx.Response(200, json={"results": [{"uuid": "s-1", "user": 1}]})
        if path == "/core/tokens/":
            case.calls.append((req.method, path))
            return httpx.Response(200, json={"results": []})
        if req.method == "DELETE":
            case.calls.append((req.method, path))
            return httpx.Response(204)
        return original(req)

    return send


if __name__ == "__main__":
    unittest.main()
