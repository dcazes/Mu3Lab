"""Household members use Mu3Lab; only administrators run it."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl.api import create_app
from ctl.api.security import resolve_identity
from tests.test_app_security import OPERATOR, PROXY_TOKEN, request

MEMBER = OPERATOR | {
    "x-authentik-username": "partner",
    "x-authentik-uid": "partner-uid",
    "x-authentik-groups": "mu3lab-household",
}
MUTATION = {"host": "host.ts.net", "origin": "https://host.ts.net", "x-mu3lab-csrf": "bound-token"}

# Administration a household member must never reach, whatever the body says.
ADMIN_ONLY = [
    ("GET", "/api/v1/audit"),
    ("GET", "/api/v1/system/config"),
    ("PUT", "/api/v1/system/config"),
    ("GET", "/api/v1/services/immich/logs"),
    ("POST", "/api/v1/providers"),
    ("DELETE", "/api/v1/providers/groq"),
    ("POST", "/api/v1/mcp/servers/immich-photo-manager/switch"),
    ("PUT", "/api/v1/mcp/servers/immich-photo-manager/categories/search"),
    ("PUT", "/api/v1/mcp/servers/immich-photo-manager/tools/search_smart/permission"),
    ("GET", "/api/v1/mcp/servers/immich-photo-manager/logs"),
    ("POST", "/api/v1/jobs/example/retry"),
    ("POST", "/api/v1/jobs/example/cancel"),
    ("POST", "/api/v1/jobs/core-install"),
    ("GET", "/api/v1/people"),
    ("POST", "/api/v1/people"),
    ("POST", "/api/v1/people/owner/deactivate"),
]


class RoleTests(unittest.TestCase):
    def setUp(self):
        for target, value in (
            ("ctl.api.security.ingress_token", PROXY_TOKEN),
            ("ctl.api.security.csrf_token", "bound-token"),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(), base_url="https://host.ts.net")

    def test_roles_follow_authentik_groups(self):
        admin, member = resolve_identity(request(OPERATOR)), resolve_identity(request(MEMBER))
        self.assertEqual((admin["role"], admin["is_admin"], admin["writes_enabled"]), ("admin", True, True))
        self.assertEqual((member["role"], member["is_admin"], member["writes_enabled"]), ("member", False, True))
        outsider = resolve_identity(request(OPERATOR | {"x-authentik-groups": "someone-else"}))
        self.assertEqual((outsider["role"], outsider["writes_enabled"]), ("", False))

    def test_a_member_cannot_reach_administration(self):
        for method, path in ADMIN_ONLY:
            with self.subTest(route=f"{method} {path}"):
                response = self.client.request(method, path, headers=MEMBER | MUTATION, json={})
                self.assertEqual(response.status_code, 403, response.text)

    def test_a_member_cannot_stop_or_uninstall_apps(self):
        for action in ("stop", "restart", "start", "uninstall", "uninstall_delete_data", "repair"):
            with self.subTest(action=action):
                response = self.client.post(
                    "/api/v1/services/immich/actions", headers=MEMBER | MUTATION, json={"action": action}
                )
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json().get("code"), "admin_required")

    def test_a_member_may_ask_to_install_an_app(self):
        response = self.client.post(
            "/api/v1/services/immich/actions", headers=MEMBER | MUTATION, json={"action": "install"}
        )
        # Past the role check; the app's own state decides from here.
        self.assertNotEqual(response.json().get("code"), "admin_required")

    def test_a_member_can_see_the_dashboard(self):
        for path in (
            "/api/v1/services",
            "/api/v1/system",
            "/api/v1/jobs",
            "/api/v1/chat/status",
            "/api/v1/mcp/servers",
        ):
            with self.subTest(path=path):
                self.assertNotEqual(self.client.get(path, headers=MEMBER).status_code, 403)


if __name__ == "__main__":
    unittest.main()
