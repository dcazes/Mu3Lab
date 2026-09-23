"""The API must not trust forgeable browser identity or mutation headers."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

from ctl.app import _calendar_owner, _homarr_control_allowed, _homarr_state, _mutation_allowed, homarr_service, identity


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

    def test_homarr_control_requires_bearer_and_trusted_proxy(self):
        value = request({
            "x-mu3lab-proxy-token": "proxy-token",
            "authorization": "Bearer homarr-token",
        })
        with patch("ctl.app._ingress_token", return_value="proxy-token"), \
             patch("ctl.secrets.read_runtime_env", return_value={"MU3LAB_HOMARR_TOKEN": "homarr-token"}):
            self.assertTrue(_homarr_control_allowed(value))
        forged = request({"authorization": "Bearer homarr-token"})
        with patch("ctl.app._ingress_token", return_value="proxy-token"), \
             patch("ctl.secrets.read_runtime_env", return_value={"MU3LAB_HOMARR_TOKEN": "homarr-token"}):
            self.assertFalse(_homarr_control_allowed(forged))

    def test_homarr_reports_healthy_authentik_as_running(self):
        result = _homarr_state({
            "id": "authentik",
            "name": "Authentik",
            "state": "needs_setup",
            "health_state": "healthy",
        })
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["stateLabel"], "Running")
        self.assertEqual(result["tone"], "teal")

    def test_combined_homarr_tile_exposes_only_current_valid_lifecycle_action(self):
        value = request({
            "x-mu3lab-proxy-token": "real-proxy",
            "authorization": "Bearer real-homarr",
        })
        project = Path("/tmp/mu3lab-test-mealie")
        with patch("ctl.app._homarr_control_allowed", return_value=True), \
             patch("ctl.app.load_registry") as registry, \
             patch("ctl.app.compose_snapshot", return_value=({str(project): "running"}, {})), \
             patch("ctl.app.project_path", return_value=project), \
             patch("ctl.app._healthy", return_value=(True, "HTTP 200")) as health_probe:
            from ctl.registry import load as load_registry
            registry.return_value = load_registry()
            with patch.object(Path, "is_file", return_value=True):
                result = homarr_service("mealie", value)
        health_probe.assert_called_once()
        self.assertEqual(result["service"]["stateLabel"], "Running")
        self.assertEqual(result["service"]["healthState"], "healthy")
        self.assertEqual(result["service"]["availableAction"], "stop")
        self.assertEqual(result["service"]["actionLabel"], "Stop")

    def test_combined_homarr_tile_reports_unhealthy_but_keeps_stop_available(self):
        value = request({"x-mu3lab-proxy-token": "real-proxy", "authorization": "Bearer real-homarr"})
        project = Path("/tmp/mu3lab-test-mealie")
        with patch("ctl.app._homarr_control_allowed", return_value=True), \
             patch("ctl.app.load_registry") as registry, \
             patch("ctl.app.compose_snapshot", return_value=({str(project): "running"}, {})), \
             patch("ctl.app.project_path", return_value=project), \
             patch("ctl.app._healthy", return_value=(False, "HTTP health probe failed")), \
             patch.object(Path, "is_file", return_value=True):
            from ctl.registry import load as load_registry
            registry.return_value = load_registry()
            result = homarr_service("mealie", value)
        self.assertEqual(result["service"]["healthState"], "unhealthy")
        self.assertEqual(result["service"]["stateLabel"], "Unhealthy")
        self.assertEqual(result["service"]["availableAction"], "stop")

    def test_combined_homarr_tile_never_offers_power_actions_for_protected_services(self):
        value = request({"x-mu3lab-proxy-token": "real-proxy", "authorization": "Bearer real-homarr"})
        with patch("ctl.app._homarr_control_allowed", return_value=True), \
             patch("ctl.app.load_registry") as registry:
            from ctl.registry import load as load_registry
            registry.return_value = load_registry()
            result = homarr_service("authentik", value)
        self.assertEqual(result["service"]["availableAction"], "")

    def test_combined_homarr_tile_offers_start_for_a_stopped_installed_stack(self):
        value = request({"x-mu3lab-proxy-token": "real-proxy", "authorization": "Bearer real-homarr"})
        project = Path("/tmp/mu3lab-test-mealie")
        with patch("ctl.app._homarr_control_allowed", return_value=True), \
             patch("ctl.app.load_registry") as registry, \
             patch("ctl.app.compose_snapshot", return_value=({str(project): "stopped"}, {})), \
             patch("ctl.app.project_path", return_value=project), \
             patch("ctl.app._healthy") as health_probe, \
             patch.object(Path, "is_file", return_value=True):
            from ctl.registry import load as load_registry
            registry.return_value = load_registry()
            result = homarr_service("mealie", value)
        self.assertEqual(result["service"]["stateLabel"], "Stopped")
        self.assertEqual(result["service"]["healthState"], "unknown")
        self.assertEqual(result["service"]["availableAction"], "start")
        self.assertEqual(result["service"]["actionLabel"], "Start")
        health_probe.assert_not_called()

    def test_calendar_mutation_requires_operator_in_addition_to_subject(self):
        value = request({"host": "host.ts.net", "origin": "https://host.ts.net"})
        with patch("ctl.app.identity", return_value={"subject_id": "subject", "writes_enabled": False}):
            result = _calendar_owner(value, write=True)
        self.assertEqual(result.status_code, 403)

    def test_calendar_read_remains_available_to_an_authenticated_subject(self):
        value = request({})
        with patch("ctl.app.identity", return_value={"subject_id": "subject", "writes_enabled": False}):
            result = _calendar_owner(value)
        self.assertEqual(result[1], "subject")
