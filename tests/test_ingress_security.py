"""Opt-in checks through real pinned Caddy, isolated from installed Mu3Lab.

Run MU3LAB_TEST_CADDY=1 python -m unittest tests.test_ingress_security -v.
Only a uniquely named -test- container and ephemeral loopback ports are used.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml
from fastapi.testclient import TestClient

from ctl.api import create_app

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("MU3LAB_TEST_CADDY") == "1", "opt-in real Caddy boundary test")
class IngressSecurityTests(unittest.TestCase):
    def setUp(self):
        self.dashboard_headers = []
        self.identity_headers = []
        self.auth_failed = False
        self.verified_admin = False
        patcher = patch(
            "ctl.api.security._runtime_env",
            return_value={
                "MU3LAB_INGRESS_TOKEN": "test-trusted-token",
                "MU3LAB_CTL_TOKEN": "test-control-secret",
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = TestClient(create_app())
        self.addCleanup(self.api.close)
        outer = self

        class Dashboard(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.dashboard_headers.append(dict(self.headers))
                if self.path.startswith("/api/"):
                    response = outer.api.get(self.path, headers=dict(self.headers))
                    payload = response.content
                    status = response.status_code
                else:
                    payload = json.dumps(dict(self.headers)).encode()
                    status = 200
                self.send_response(status)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        class Identity(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.identity_headers.append(dict(self.headers))
                if outer.auth_failed:
                    self.send_response(503)
                elif self.headers.get("Cookie") == "test-auth=valid":
                    self.send_response(200)
                    # Deliberately omit JWT/groups: inbound values must not fill them in.
                    self.send_header("X-Authentik-Username", "verified-person")
                    self.send_header("X-Authentik-Uid", "verified-subject")
                    if outer.verified_admin:
                        self.send_header("X-Authentik-Groups", "mu3lab-operators")
                        self.send_header("X-Authentik-Jwt", "verified-jwt")
                else:
                    self.send_response(401)
                self.end_headers()

            def log_message(self, *_):
                pass

        self.dashboard = self._server(Dashboard)
        self.identity = self._server(Identity)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.forged = {
            "X-Authentik-Username": "forged-owner",
            "X-Authentik-Uid": "forged-subject",
            "X-Authentik-Groups": "mu3lab-operators",
            "X-Authentik-Jwt": "forged-jwt",
            "X-Authentik-Entitlements": "forged-admin",
            "X-Mu3Lab-Proxy-Token": "forged-token",
        }

    def _server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(close)
        return server

    def _start(self, variant):
        # Reserve all listener ports together to avoid collisions with this host's services.
        sockets = [socket.socket() for _ in range(3)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        ports = [sock.getsockname()[1] for sock in sockets]
        content = (ROOT / "apps/ingress" / variant).read_text()
        for original, replacement in zip((19460, 19461, 19462), ports, strict=True):
            content = content.replace(f":{original}", f":{replacement}")
        content = content.replace("127.0.0.1:8787", f"127.0.0.1:{self.dashboard.server_port}")
        content = content.replace("127.0.0.1:9001", f"127.0.0.1:{self.identity.server_port}")
        config = Path(self.tmp.name) / variant
        config.write_text(content)
        image = yaml.safe_load((ROOT / "apps/ingress/docker-compose.yml").read_text())["services"]["caddy"]["image"]
        name = "mu3lab-test-ingress-" + uuid.uuid4().hex
        for sock in sockets:
            sock.close()
        log = self.enterContext(open(Path(self.tmp.name) / "caddy.log", "w+b"))
        process = subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "host",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "NET_BIND_SERVICE",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--read-only",
                "--tmpfs",
                "/config:mode=1777",
                "--tmpfs",
                "/data:mode=1777",
                "-e",
                "MU3LAB_INGRESS_TOKEN=test-trusted-token",
                "-v",
                f"{config}:/etc/caddy/Caddyfile:ro",
                image,
                "caddy",
                "run",
                "--config",
                "/etc/caddy/Caddyfile",
                "--adapter",
                "caddyfile",
            ],
            stdout=log,
            stderr=log,
        )

        def stop():
            subprocess.run(["docker", "stop", "-t", "1", name], capture_output=True, timeout=15, check=False)
            process.wait(timeout=15)

        self.addCleanup(stop)
        self.url = f"http://127.0.0.1:{ports[0]}"
        for _ in range(100):
            if process.poll() is not None:
                log.seek(0)
                self.fail(f"Caddy exited before readiness: {log.read().decode()}")
            try:
                with urlopen(self.url + "/__mu3lab_caddy_health", timeout=1) as response:
                    if response.status == 204:
                        return
            except (URLError, OSError):
                time.sleep(0.1)
        self.fail("Test Caddy did not become ready")

    def _request(self, path, *, signed_in=False, method="GET"):
        headers = dict(self.forged)
        if signed_in:
            headers["Cookie"] = "test-auth=valid"
        request = Request(self.url + path, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            exc.close()
            return exc.code, {}

    def test_starter_never_forwards_dashboard_or_csrf_requests(self):
        self._start("Caddyfile")
        for method, path in (
            ("GET", "/"),
            ("GET", "/api/v1/session"),
            ("GET", "/api/v1/services"),
            ("POST", "/api/v1/services/example/actions"),
            ("PUT", "/api/v1/me/checklist"),
        ):
            with self.subTest(path=path, method=method):
                self.assertEqual(self._request(path, method=method)[0], 503)
        self.assertEqual(self.dashboard_headers, [])
        self.assertEqual(self.identity_headers, [])

    def test_gate_strips_forgery_and_uses_only_verified_identity(self):
        self._start("Caddyfile.authenticated")
        self.assertEqual(self._request("/api/v1/session")[0], 401)
        self.assertEqual(self._request("/api/v1/services/example/actions", method="POST")[0], 401)
        self.assertEqual(self.dashboard_headers, [])
        status, headers = self._request("/echo", signed_in=True)
        self.assertEqual(status, 200)
        normalized = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(normalized["x-authentik-username"], "verified-person")
        self.assertEqual(normalized["x-authentik-uid"], "verified-subject")
        self.assertEqual(normalized["x-mu3lab-proxy-token"], "test-trusted-token")
        for key in ("x-authentik-jwt", "x-authentik-groups", "x-authentik-entitlements"):
            self.assertNotIn(key, normalized)
        for request_headers in self.identity_headers:
            normalized = {key.lower(): value for key, value in request_headers.items()}
            self.assertFalse(any(key.startswith("x-authentik-") for key in normalized))
            self.assertNotIn("x-mu3lab-proxy-token", normalized)
        status, identity = self._request("/api/v1/identity", signed_in=True)
        self.assertEqual(status, 200)
        self.assertEqual(identity["username"], "verified-person")
        self.assertFalse(identity["writes_enabled"])
        self.assertEqual(self._request("/api/v1/session", signed_in=True)[0], 403)
        self.verified_admin = True
        self.assertTrue(self._request("/api/v1/identity", signed_in=True)[1]["writes_enabled"])
        self.assertTrue(self._request("/api/v1/session", signed_in=True)[1]["csrf_token"])
        self.auth_failed = True
        before = len(self.dashboard_headers)
        self.assertEqual(self._request("/api/v1/session", signed_in=True)[0], 503)
        self.assertEqual(len(self.dashboard_headers), before)
