"""The API must not trust forgeable browser identity or mutation headers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.requests import Request

from ctl.app import _mutation_allowed, identity


def request(headers: dict[str, str]) -> Request:
    return Request({
        "type": "http", "method": "GET", "scheme": "https", "path": "/",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": ("127.0.0.1", 1), "server": ("host.ts.net", 443),
    })


class IdentityBoundaryTests(unittest.TestCase):
    def test_forged_authentik_headers_without_proxy_token_are_rejected(self):
        value = request({
            "x-authentik-username": "attacker",
            "x-authentik-groups": "mu3lab-operators",
        })
        with patch("ctl.app._ingress_token", return_value="real-token"):
            self.assertFalse(identity(value)["writes_enabled"])

    def test_pipe_delimited_operator_group_from_trusted_proxy_is_accepted(self):
        value = request({
            "x-mu3lab-proxy-token": "real-token",
            "x-authentik-username": "owner",
            "x-authentik-groups": "household|mu3lab-operators",
        })
        with patch("ctl.app._ingress_token", return_value="real-token"):
            result = identity(value)
        self.assertTrue(result["writes_enabled"])
        self.assertEqual(result["groups"], ["household", "mu3lab-operators"])

    def test_mutation_requires_matching_https_origin_and_csrf(self):
        value = request({
            "host": "host.ts.net",
            "origin": "https://host.ts.net",
            "x-mu3lab-csrf": "bound-token",
        })
        with patch("ctl.app._csrf_token", return_value="bound-token"):
            self.assertTrue(_mutation_allowed(value))
        cross_site = request({
            "host": "host.ts.net", "origin": "https://evil.example",
            "x-mu3lab-csrf": "bound-token",
        })
        with patch("ctl.app._csrf_token", return_value="bound-token"):
            self.assertFalse(_mutation_allowed(cross_site))
