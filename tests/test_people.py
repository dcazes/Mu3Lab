"""Household operations use real REST shapes, including nested groups and recovery URLs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ctl import people
from ctl.integrations.authentik import Authentik
from ctl.runtime import RuntimePaths


class PeopleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.users = [
            self._user(1, "owner", ["authentik Admins"]),
            self._user(2, "member", ["mu3lab-household"]),
            self._user(3, "ak-service", ["authentik Admins"]),
            self._user(4, "outsider", []),
        ]
        self.calls = []
        self.groups = {
            name: {"pk": name, "name": name} for name in ("authentik Admins", "mu3lab-operators", "mu3lab-household")
        }
        client = Authentik("test-token", transport=httpx.MockTransport(self._send))
        runtime = patch("ctl.people.Authentik.runtime", return_value=client)
        runtime.start()
        self.addCleanup(runtime.stop)
        paths = patch("ctl.people.RuntimePaths", return_value=RuntimePaths(Path(temporary.name)))
        paths.start()
        self.addCleanup(paths.stop)

    def _user(self, pk, username, groups):
        return {
            "pk": pk,
            "username": username,
            "uid": f"uid-{pk}",
            "name": username,
            "email": f"{username}@example.test",
            "is_active": True,
            "last_login": None,
            "groups": groups,
            "groups_obj": [{"pk": group, "name": group} for group in groups],
        }

    def _send(self, request):
        path = request.url.path.removeprefix("/api/v3")
        self.calls.append((request.method, path))
        body = json.loads(request.content) if request.content else {}
        if path == "/core/users/":
            if request.method == "POST":
                user = self._user(len(self.users) + 1, body["username"], [])
                user.update(body)
                self.users.append(user)
                return httpx.Response(201, json=user)
            username = request.url.params.get("username")
            return httpx.Response(
                200, json={"results": [user for user in self.users if not username or user["username"] == username]}
            )
        if path == "/core/groups/":
            return httpx.Response(200, json={"results": [self.groups[request.url.params["name"]]]})
        if path.startswith("/core/groups/"):
            group = path.split("/")[3]
            user = next(user for user in self.users if user["pk"] == body["pk"])
            groups = set(user["groups"])
            groups.add(group) if "add_user" in path else groups.discard(group)
            user["groups"] = sorted(groups)
            user["groups_obj"] = [self.groups[name] for name in sorted(groups)]
            return httpx.Response(204)
        if path.startswith("/core/users/"):
            pk = int(path.split("/")[3])
            if path.endswith("/recovery/"):
                self.assertEqual(body, {"token_duration": "hours=24"})
                return httpx.Response(
                    200, json={"link": "http://127.0.0.1:9001/if/flow/mu3lab-welcome/?token=private-invite"}
                )
            user = next(user for user in self.users if user["pk"] == pk)
            user.update(body)
            return httpx.Response(200, json=user)
        self.fail(f"Unexpected REST request: {request.method} {path}")

    def test_listing_filters_service_users_and_outsiders(self):
        result = people.list_people()
        self.assertEqual([user["username"] for user in result], ["member", "owner"])
        self.assertEqual(result[-1]["role"], "admin")
        self.assertNotIn("has_password", result[-1])

    def test_add_uses_suffix_and_private_invite_origin_without_a_password(self):
        result = people.add_person("New Member", " MEMBER@new.test ", "member", "https://mu3lab.example.ts.net")
        self.assertEqual(result["person"]["username"], "member2")
        self.assertEqual(result["person"]["role"], "member")
        self.assertEqual(
            result["invite"]["url"], "https://mu3lab.example.ts.net/if/flow/mu3lab-welcome/?token=private-invite"
        )
        self.assertFalse(any("password" in path for _, path in self.calls))

    def test_duplicate_email_and_invalid_input_make_no_mutations(self):
        for email, role in (("owner@example.test", "member"), ("invalid", "member"), ("new@example.test", "invalid")):
            with self.subTest(email=email), self.assertRaises(people.PeopleError):
                people.add_person("Name", email, role, "https://mu3lab.example.ts.net")
        self.assertTrue(all(method == "GET" for method, _ in self.calls))

    def test_last_admin_cannot_be_demoted_or_deactivated(self):
        for action in ("role", "deactivate"):
            with self.subTest(action=action), self.assertRaisesRegex(people.PeopleError, "at least one"):
                people.change("owner", action, "https://mu3lab.example.ts.net", "member")
        self.assertTrue(all(method == "GET" for method, _ in self.calls))

    def test_demoting_superuser_removes_inherited_admin_group(self):
        people.change("member", "role", "https://mu3lab.example.ts.net", "admin")
        result = people.change("owner", "role", "https://mu3lab.example.ts.net", "member")
        self.assertEqual(result["person"]["role"], "member")
        self.assertNotIn("authentik Admins", self.users[0]["groups"])
        result = people.change("owner", "deactivate", "https://mu3lab.example.ts.net")
        self.assertFalse(result["person"]["active"])
        result = people.change("owner", "reactivate", "https://mu3lab.example.ts.net")
        self.assertTrue(result["person"]["active"])

    def test_bootstrap_token_owner_cannot_be_disabled_even_with_another_admin(self):
        self.users[0]["username"] = "akadmin"
        people.change("member", "role", "https://mu3lab.example.ts.net", "admin")
        for action in ("role", "deactivate"):
            with self.subTest(action=action), self.assertRaisesRegex(people.PeopleError, "installation administrator"):
                people.change("akadmin", action, "https://mu3lab.example.ts.net", "member")

    def test_outsiders_and_service_users_cannot_be_changed(self):
        for username in ("outsider", "ak-service"):
            with self.subTest(username=username), self.assertRaisesRegex(people.PeopleError, "not part"):
                people.change(username, "invite", "https://mu3lab.example.ts.net")

    def test_invite_existing_person_returns_no_password(self):
        result = people.change("member", "invite", "https://mu3lab.example.ts.net")
        self.assertEqual(set(result), {"invite"})
        self.assertEqual(result["invite"]["valid_hours"], 24)


if __name__ == "__main__":
    unittest.main()
