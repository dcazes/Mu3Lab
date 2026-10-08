"""Sanitized Tailscale status projections used by the Home inspector."""

from __future__ import annotations

import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import service_state
from ctl.app import app
from ctl.service_state import (
    container_memory,
    tailnet_dns_name,
    tailnet_serve_ports,
    tailnet_serve_status,
    tailscale_status,
)


def result(stdout: str, returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, returncode=returncode)


class TailscaleStatusTests(unittest.TestCase):
    def read_status(self, payload: object, returncode: int = 0) -> dict[str, object]:
        return tailscale_status(run=lambda *args, **kwargs: result(json.dumps(payload), returncode))

    def test_running_online_normalizes_trailing_dns_period(self):
        status = self.read_status(
            {
                "BackendState": "Running",
                "Self": {"Online": True, "DNSName": "mu3lab-8.example.ts.net."},
                "MagicDNSSuffix": "must not leak",
                "Peer": {"PublicKey": "private"},
            }
        )
        self.assertEqual(
            status,
            {
                "state": "connected",
                "backend_state": "Running",
                "online": True,
                "dns_name": "mu3lab-8.example.ts.net",
                "detail": "Connected to the tailnet.",
            },
        )
        self.assertEqual(
            tailnet_dns_name(
                run=lambda *args, **kwargs: result(
                    json.dumps(
                        {
                            "BackendState": "Running",
                            "Self": {"Online": True, "DNSName": "node.ts.net."},
                        }
                    )
                )
            ),
            "node.ts.net",
        )

    def test_running_online_without_magicdns_remains_connected(self):
        status = self.read_status({"BackendState": "Running", "Self": {"Online": True}})
        self.assertEqual(status["state"], "connected")
        self.assertEqual(status["dns_name"], "")
        self.assertEqual(status["detail"], "Connected to the tailnet.")

    def test_running_but_offline_is_disconnected(self):
        status = self.read_status({"BackendState": "Running", "Self": {"Online": False}})
        self.assertEqual(status["state"], "disconnected")
        self.assertEqual(status["detail"], "Tailscale is running, but this device is offline.")

    def test_needs_login_is_disconnected_with_sign_in_detail(self):
        status = self.read_status({"BackendState": "NeedsLogin", "Self": {"Online": False}})
        self.assertEqual(status["state"], "disconnected")
        self.assertEqual(status["detail"], "Tailscale needs sign-in.")

    def test_nonzero_command_is_disconnected(self):
        status = self.read_status({"BackendState": "Running"}, returncode=1)
        self.assertEqual(status["state"], "disconnected")
        self.assertEqual(status["detail"], "Tailscale is installed but its status could not be read.")

    def test_missing_binary_and_timeout_are_unavailable(self):
        for error in (FileNotFoundError("tailscale"), subprocess.TimeoutExpired("tailscale", 5)):
            with self.subTest(error=type(error).__name__):
                status = tailscale_status(run=lambda *args, error=error, **kwargs: (_ for _ in ()).throw(error))
                self.assertEqual(status["state"], "unavailable")
                self.assertEqual(status["detail"], "Tailscale status is unavailable.")

    def test_invalid_json_and_non_object_json_are_unavailable(self):
        for output in ("", "{bad", "[]", "null", "false"):
            with self.subTest(output=output):
                status = tailscale_status(run=lambda *args, output=output, **kwargs: result(output))
                self.assertEqual(status["state"], "unavailable")
                self.assertEqual(status["detail"], "Tailscale status is unavailable.")

    def test_successful_serve_status_extracts_sorted_unique_ports(self):
        serve = tailnet_serve_status(
            run=lambda *args, **kwargs: result(
                "https://mu3lab.ts.net:8446 (TLS terminated)\n"
                "https=443\nhttps://mu3lab.ts.net:8443\nhttps://mu3lab.ts.net:443\n"
            )
        )
        self.assertEqual(serve, {"state": "available", "ports": [443, 8443, 8446]})

    def test_default_https_listener_counts_as_443_and_backends_are_ignored(self):
        # Real `tailscale serve status` output: the 443 listener prints no port.
        serve = tailnet_serve_status(
            run=lambda *args, **kwargs: result(
                "https://mu3lab.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:19461\n\n"
                "https://mu3lab.ts.net:8443 (tailnet only)\n|-- / proxy http://127.0.0.1:19462\n"
            )
        )
        self.assertEqual(serve, {"state": "available", "ports": [443, 8443]})

    def test_successful_empty_serve_status_is_available(self):
        self.assertEqual(
            tailnet_serve_status(run=lambda *args, **kwargs: result("")), {"state": "available", "ports": []}
        )

    def test_failed_serve_status_is_unavailable(self):
        failed = tailnet_serve_status(run=lambda *args, **kwargs: result("https=443", 1))
        self.assertEqual(failed, {"state": "unavailable", "ports": []})
        errored = tailnet_serve_status(
            run=lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("tailscale", 5))
        )
        self.assertEqual(errored, {"state": "unavailable", "ports": []})

    def test_legacy_serve_port_helper_still_returns_a_set(self):
        with patch("ctl.service_state.tailnet_serve_status", return_value={"state": "available", "ports": [443, 8443]}):
            self.assertEqual(tailnet_serve_ports(), {443, 8443})
            self.assertIsInstance(tailnet_serve_ports(), set)


class ContainerMemoryTests(unittest.TestCase):
    def setUp(self):
        service_state._memory_cache = (0.0, {})

    def test_parses_docker_stats_and_caches_briefly(self):
        calls = []

        def run(*args, **kwargs):
            calls.append(args)
            return result("mu3lab-ollama\t3.5GiB / 15GiB\nmu3lab-caddy\t20.5MiB / 15GiB\nbroken\t--\n")

        clock = iter([100.0, 105.0, 200.0, 200.0])
        first = container_memory(run=run, now=lambda: next(clock))
        self.assertEqual(first, {"mu3lab-ollama": int(3.5 * 1024**3), "mu3lab-caddy": int(20.5 * 1024**2)})
        self.assertEqual(container_memory(run=run, now=lambda: next(clock)), first)
        self.assertEqual(len(calls), 1)
        container_memory(run=run, now=lambda: next(clock))
        self.assertEqual(len(calls), 2)

    def test_failure_returns_last_known_values(self):
        self.assertEqual(container_memory(run=lambda *a, **k: result("", 1)), {})


class SystemTailscaleContractTests(unittest.TestCase):
    def test_system_exposes_only_the_sanitized_snapshot_and_sorted_serve_array(self):
        status = {
            "state": "connected",
            "backend_state": "Running",
            "online": True,
            "dns_name": "mu3lab-8.taile2cc7a.ts.net",
            "detail": "Connected to the tailnet.",
        }
        serve = {"state": "available", "ports": [443, 8443, 8446]}
        with (
            patch(
                "ctl.status.host.psutil.virtual_memory",
                return_value=SimpleNamespace(total=100, used=40, percent=40),
            ),
            patch("ctl.status.host.psutil.disk_usage", return_value=SimpleNamespace(total=500, used=200, percent=40)),
            patch("ctl.status.host.psutil.cpu_percent", return_value=2.0),
            patch("ctl.status.host.psutil.boot_time", return_value=1),
            patch("ctl.status.host.process.completed", return_value=SimpleNamespace(returncode=0, stdout="active\n")),
            patch("ctl.status.host.RuntimePaths") as runtime_paths,
            patch("ctl.status.host.backup_readiness", return_value={}),
            patch("ctl.status.host.container_memory", return_value={"mu3lab-ollama": 5}),
            patch("ctl.status.host.tailscale_status", return_value=status) as status_call,
            patch("ctl.status.host.tailnet_serve_status", return_value=serve) as serve_call,
        ):
            runtime_paths.return_value.root = __import__("pathlib").Path("/tmp/mu3lab")
            from ctl.api.routes import system
            from ctl.api.security import require_member
            from ctl.status.host import observe_system

            observation = observe_system()
            app.dependency_overrides[require_member] = lambda: {"writes_enabled": True, "role": "member"}
            try:
                with patch.object(system.records, "get", return_value=observation):
                    response = TestClient(app).get("/api/v1/system")
            finally:
                app.dependency_overrides.pop(require_member, None)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        expected = {**status, "serve": serve}
        self.assertEqual(body["tailscale"], expected)
        self.assertEqual(body["tailnet_dns_name"], body["tailscale"]["dns_name"])
        self.assertEqual(body["tailscale"]["serve"]["ports"], [443, 8443, 8446])
        self.assertEqual(set(body["tailscale"]), {"state", "backend_state", "online", "dns_name", "detail", "serve"})
        self.assertEqual(body["worker_state"], "active")
        self.assertEqual(body["container_memory"], {"mu3lab-ollama": 5})
        self.assertNotIn("Peer", body)
        self.assertNotIn("PublicKey", json.dumps(body))
        status_call.assert_called_once_with()
        serve_call.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
