"""The local setup page: session security, status shape, and resume logic."""

from __future__ import annotations

import http.client
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from ctl import install
from ctl.bootstrap import server

TOKEN = "t" * 43


class ServerHarness(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(server.Session, "start_scan", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.session = server.Session(TOKEN)
        self.httpd.session = self.session  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method: str, path: str, *, token: str | None = TOKEN, host: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token is not None:
            headers["X-Mu3Lab-Token"] = token
        connection.request(method, path, body=b"{}" if method == "POST" else None, headers=headers)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response, body


class SecurityTests(ServerHarness):
    def test_page_is_served_without_secrets(self):
        response, body = self.request("GET", "/", token=None)
        self.assertEqual(response.status, 200)
        self.assertNotIn(TOKEN.encode(), body)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")

    def test_api_requires_the_session_token(self):
        for token in (None, "wrong"):
            with self.subTest(token=token):
                self.assertEqual(self.request("GET", "/api/status", token=token)[0].status, 401)
                self.assertEqual(self.request("POST", "/api/install", token=token)[0].status, 401)

    def test_foreign_host_headers_are_rejected(self):
        # DNS rebinding: a hostile site resolving to 127.0.0.1 still sends its own Host.
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1:1"):
            with self.subTest(host=host):
                self.assertEqual(self.request("GET", "/api/status", host=host)[0].status, 403)
                self.assertEqual(self.request("GET", "/", token=None, host=host)[0].status, 403)

    def test_localhost_alias_is_accepted(self):
        response, _ = self.request("GET", "/api/status", host=f"localhost:{self.port}")
        self.assertEqual(response.status, 200)


class StatusTests(ServerHarness):
    def test_status_lists_every_step_with_its_phase(self):
        response, body = self.request("GET", "/api/status")
        status = json.loads(body)
        self.assertEqual(response.status, 200)
        self.assertEqual(status["job"]["status"], "idle")
        self.assertEqual([step["id"] for step in status["job"]["steps"]], [step["id"] for step in install.STEPS])
        self.assertEqual(status["phases"], [title for title, _ids in install.PHASES])
        self.assertTrue(all(step["phase"] in status["phases"] for step in status["job"]["steps"]))

    def test_dashboard_url_is_only_offered_when_setup_is_ready(self):
        with patch.object(server, "tailnet_dashboard_url", return_value="https://host.ts.net:8446/"):
            self.assertEqual(json.loads(self.request("GET", "/api/status")[1])["dashboard_url"], "")
            self.session.job["status"] = "ready"
            self.assertEqual(
                json.loads(self.request("GET", "/api/status")[1])["dashboard_url"], "https://host.ts.net:8446/"
            )


def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.02)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(server.Session, "start_scan", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = server.Session(TOKEN)
        self.runs: list[dict] = []

    def fake_run(self, prompt_kind: str | None):
        def run(job, ctx):
            self.runs.append(dict(ctx["inputs"]))
            step = job["steps"][1]
            if prompt_kind and not ctx["inputs"].get(f"{step['id']}_confirmed") and len(self.runs) == 1:
                step["status"] = "waiting"
                step["prompt"] = {"kind": prompt_kind}
                job["status"] = "waiting"
                return
            step["status"] = "ready"
            job["status"] = "ready"

        return run

    def test_manual_step_confirmation_starts_a_resumed_run(self):
        with patch.object(install, "run_job", self.fake_run("manual_setup")):
            self.session.start_install()
            _wait_for(lambda: self.session.job["status"] == "waiting")
            self.session.continue_install()
            _wait_for(lambda: self.session.job["status"] == "ready")
        self.assertEqual(len(self.runs), 2)
        self.assertTrue(self.runs[1][f"{install.STEPS[1]['id']}_confirmed"])

    def test_terminal_prompt_resumes_by_running_again(self):
        with patch.object(install, "run_job", self.fake_run("terminal")):
            self.session.start_install()
            _wait_for(lambda: self.session.job["status"] == "waiting")
            self.session.continue_install()
            _wait_for(lambda: self.session.job["status"] == "ready")
        self.assertEqual(len(self.runs), 2)

    def test_install_cannot_start_twice(self):
        release = threading.Event()
        with patch.object(install, "run_job", lambda job, ctx: release.wait(2)):
            self.assertTrue(self.session.start_install())
            self.assertFalse(self.session.start_install())
            release.set()

    def test_crash_is_reported_in_plain_language(self):
        def crash(job, ctx):
            raise RuntimeError("boom")

        with patch.object(install, "run_job", crash):
            self.session.start_install()
            _wait_for(lambda: self.session.job["status"] == "failed")
        self.assertIn("boom", self.session.job["error"])

    def test_authentik_reset_only_at_its_explicit_step(self):
        self.assertFalse(self.session.reset_authentik_admin()["ok"])
        step = next(step for step in self.session.job["steps"] if step["id"] == "authentik_setup")
        step["status"] = "waiting"
        step["prompt"] = {"recovery_action": "reset_authentik_admin"}
        with patch.object(install, "reset_authentik_admin_password", return_value={"ok": True, "username": "akadmin"}):
            self.assertTrue(self.session.reset_authentik_admin()["ok"])


class DashboardUrlTests(unittest.TestCase):
    def test_uses_the_magicdns_name_and_dashboard_port(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": '{"Self": {"DNSName": "mu3lab.tail1.ts.net."}}'})
        with patch.object(server.subprocess, "run", return_value=completed):
            self.assertEqual(server.tailnet_dashboard_url(), "https://mu3lab.tail1.ts.net:8446/")

    def test_rejects_non_tailnet_names(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": '{"Self": {"DNSName": "localhost"}}'})
        with patch.object(server.subprocess, "run", return_value=completed):
            self.assertEqual(server.tailnet_dashboard_url(), "")


if __name__ == "__main__":
    unittest.main()
