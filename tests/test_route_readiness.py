"""R20: published routes do not establish that an app is usable."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.registry import load
from ctl.service_state import status
from ctl.status.display import projection

ROOT = Path(__file__).resolve().parents[1]


class RouteReadinessTests(unittest.TestCase):
    def observe(self, app, dns="test.ts.net", answers=False):
        service = load().get(app)
        with (
            patch("ctl.service_state._healthy", return_value=(True, "healthy")),
            patch("ctl.service_state._compose_state", return_value="running"),
            patch("ctl.service_state.route_answers", return_value=answers),
        ):
            return status(service, dns, ROOT, {service.private_https_port})

    def test_failed_required_route_does_not_appear_usable(self):
        item = self.observe("mealie")
        self.assertEqual(item["health_state"], "healthy")
        self.assertFalse(item["route_ready"])
        self.assertEqual(item["route_state"], "configured")
        self.assertEqual(item["state"], "needs_setup")
        self.assertEqual(item["ui"]["state"], "route_pending")
        self.assertIsNone(item["ui"]["url"])
        self.assertEqual(projection(item)["display_state"], "needs_attention")
        self.assertIn("route", projection(item)["reason"])

    def test_successful_required_route_becomes_usable(self):
        item = self.observe("mealie", answers=True)
        self.assertEqual(item["state"], "ready")
        self.assertTrue(item["route_ready"])
        self.assertEqual(item["route_state"], "verified")
        self.assertEqual(item["ui"]["state"], "ready")

    def test_missing_dns_is_not_a_successful_route_check(self):
        item = self.observe("mealie", dns="", answers=True)
        self.assertFalse(item["route_ready"])
        self.assertEqual(item["state"], "needs_setup")

    def test_internal_service_needs_no_browser_route(self):
        item = self.observe("ollama")
        self.assertEqual(item["route_state"], "not_required")
        self.assertEqual(item["state"], "ready")
