"""The API must not trust forgeable browser identity or mutation headers."""

from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

from ctl.api import create_app
from ctl.api.security import mutation_allowed, resolve_identity

PROXY_TOKEN = "real-token"
OPERATOR = {
    "x-mu3lab-proxy-token": PROXY_TOKEN,
    "x-authentik-username": "owner",
    "x-authentik-uid": "subject",
    "x-authentik-email": "owner@example.test",
    "x-authentik-groups": "household|mu3lab-operators",
}
HOUSEHOLD = OPERATOR | {"x-authentik-groups": "household"}


def request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "client": ("127.0.0.1", 1),
            "server": ("host.ts.net", 443),
        }
    )


def _mutating_routes(app) -> list[tuple[str, str]]:
    routes = []
    for path, operations in app.openapi()["paths"].items():
        concrete = re.sub(r"\{[^}]+\}", "example", path)
        routes.extend((method.upper(), concrete) for method in operations if method in {"post", "put", "delete"})
    return routes


class IdentityBoundaryTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("ctl.api.security.ingress_token", return_value=PROXY_TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app())

    def test_forged_authentik_headers_without_proxy_token_are_rejected(self):
        forged = {key: value for key, value in OPERATOR.items() if key != "x-mu3lab-proxy-token"}
        body = self.client.get("/api/v1/identity", headers=forged).json()
        self.assertFalse(body["writes_enabled"])
        self.assertEqual(body["username"], "")

    def test_pipe_delimited_operator_group_from_trusted_proxy_is_accepted(self):
        result = resolve_identity(request(OPERATOR))
        self.assertTrue(result["writes_enabled"])
        self.assertEqual(result["groups"], ["household", "mu3lab-operators"])

    def test_mutation_requires_matching_https_origin_and_csrf(self):
        headers = {"host": "host.ts.net", "origin": "https://host.ts.net", "x-mu3lab-csrf": "bound-token"}
        with patch("ctl.api.security.csrf_token", return_value="bound-token"):
            self.assertTrue(mutation_allowed(request(headers)))
            self.assertFalse(mutation_allowed(request(headers | {"origin": "https://evil.example"})))
            self.assertFalse(mutation_allowed(request(headers | {"origin": "http://host.ts.net"})))
            self.assertFalse(mutation_allowed(request(headers | {"x-mu3lab-csrf": "other"})))

    def test_every_mutating_route_rejects_an_operator_without_csrf(self):
        routes = _mutating_routes(self.client.app)
        self.assertGreater(len(routes), 30)
        for method, path in routes:
            with self.subTest(route=f"{method} {path}"):
                response = self.client.request(method, path, headers=OPERATOR, json={})
                self.assertEqual(response.status_code, 403, response.text)
                self.assertFalse(response.json()["ok"])

    def test_every_mutating_route_rejects_a_non_operator_even_with_csrf(self):
        headers = HOUSEHOLD | {"host": "testserver", "origin": "https://testserver", "x-mu3lab-csrf": "bound"}
        with patch("ctl.api.security.csrf_token", return_value="bound"):
            for method, path in _mutating_routes(self.client.app):
                with self.subTest(route=f"{method} {path}"):
                    response = self.client.request(method, path, headers=headers, json={})
                    self.assertEqual(response.status_code, 403, response.text)

    def test_operator_reads_require_an_operator(self):
        for path in (
            "/api/v1/providers",
            "/api/v1/mcp/servers",
            "/api/v1/system/config",
            "/api/v1/services/lobehub/logs",
            "/api/v1/chat/status",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=HOUSEHOLD).status_code, 403)

    def test_calendar_read_remains_available_to_an_authenticated_subject(self):
        response = self.client.get("/api/v1/calendar/connection", headers=HOUSEHOLD)
        self.assertNotEqual(response.status_code, 403)

    def test_calendar_read_requires_an_authenticated_subject(self):
        self.assertEqual(self.client.get("/api/v1/calendar/connection").status_code, 403)

    def test_unknown_api_paths_are_json_errors(self):
        response = self.client.get("/api/v1/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["content-type"], "application/json")


if __name__ == "__main__":
    unittest.main()
