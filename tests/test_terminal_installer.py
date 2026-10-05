"""The terminal installer's questions, account creation and core-app checks."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from ctl import core_setup
from ctl.bootstrap import terminal
from ctl.integrations.vaultwarden import bootstrap as vaultwarden_api


class DashboardReadinessTests(unittest.TestCase):
    def test_http_errors_and_unexpected_redirects_are_not_ready(self):
        for code in (401, 403, 404, 500, 302):
            with (
                self.subTest(code=code),
                patch("ctl.bootstrap.terminal.urllib.request.build_opener") as opener,
                patch("ctl.bootstrap.terminal.time.monotonic", side_effect=[0, 0, 2]),
                patch("ctl.bootstrap.terminal.time.sleep"),
            ):
                opener.return_value.open.side_effect = urllib.error.HTTPError(
                    "https://host.ts.net:8446", code, "failure", {"Location": "https://wrong.ts.net/login"}, None
                )
                self.assertFalse(terminal.warm_up("https://host.ts.net:8446", timeout=1, redirect_host="host.ts.net"))

    def test_expected_authentik_redirect_only_verifies_route(self):
        with patch("ctl.bootstrap.terminal.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.HTTPError(
                "https://host.ts.net:8446", 302, "login", {"Location": "https://host.ts.net/if/flow/login/"}, None
            )
            self.assertTrue(terminal.warm_up("https://host.ts.net:8446", redirect_host="host.ts.net"))

    def test_route_failure_stops_before_opening_dashboard(self):
        with (
            patch.object(terminal, "warm_up", return_value=False),
            patch.object(terminal.webbrowser, "open") as browser,
        ):
            ok, message = terminal.verify_dashboard(Mock(), "host.ts.net", lambda: False)
        self.assertFalse(ok)
        self.assertIn("did not become ready", message)
        browser.assert_not_called()

    def test_sign_in_redirect_does_not_finish_without_a_fresh_snapshot(self):
        with (
            patch.object(terminal, "warm_up", return_value=True),
            patch.object(terminal.bootstrap_state, "dashboard_ready_since", return_value=False),
            patch.object(terminal.time, "monotonic", side_effect=[0, 0, 2]),
            patch.object(terminal.time, "sleep"),
            patch.dict("os.environ", {}, clear=True),
        ):
            ok, message = terminal.verify_dashboard(Mock(), "host.ts.net", lambda: False, timeout=1)
        self.assertFalse(ok)
        self.assertIn("No successful signed-in dashboard load", message)

    def test_finishes_only_after_a_new_signed_in_snapshot(self):
        with (
            patch.object(terminal, "warm_up", return_value=True),
            patch.object(terminal.bootstrap_state, "dashboard_ready_since", side_effect=[False, True]) as ready,
            patch.object(terminal.time, "time", return_value=123),
            patch.object(terminal.time, "sleep"),
            patch.dict("os.environ", {"DISPLAY": ":0"}),
            patch.object(terminal.webbrowser, "open") as browser,
        ):
            self.assertEqual(terminal.verify_dashboard(Mock(), "host.ts.net", lambda: False), (True, ""))
        ready.assert_called_with(123)
        browser.assert_called_once_with("https://host.ts.net:8446/")

    def test_failed_final_verification_exits_without_reporting_success(self):
        def finish_job(job, _ctx):
            job["status"] = "ready"

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(terminal, "ROOT", Path(tmp)),
            patch.object(terminal.sys.stdin, "isatty", return_value=True),
            patch.object(terminal, "Screen"),
            patch.object(terminal.install, "_vaultwarden_account_exists", return_value=True),
            patch.object(terminal.install, "new_job", return_value={"steps": [], "inputs": {}}),
            patch.object(terminal.install, "run_job", side_effect=finish_job),
            patch.object(terminal, "wait_for_core_apps", return_value=(True, "")),
            patch.object(terminal, "tailnet_name", return_value="host.ts.net"),
            patch.object(terminal, "verify_dashboard", return_value=(False, "snapshot unavailable")),
            patch.object(terminal, "report_success") as success,
        ):
            self.assertEqual(terminal.run(), 1)
        success.assert_not_called()


class RegisterTests(unittest.TestCase):
    def test_sends_only_derived_keys_and_the_vault_can_be_unlocked(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"object": "register"})

        client = httpx.Client(base_url="http://vault", transport=httpx.MockTransport(handler))
        vaultwarden_api.register("http://vault", "Alex@Example.com", "correct horse battery", "Alex", client=client)
        body = seen["body"]
        self.assertEqual(seen["path"], "/identity/accounts/register")
        self.assertEqual(body["email"], "alex@example.com")
        self.assertNotIn("correct horse battery", json.dumps(body))
        master_key = vaultwarden_api.derive_master_key("correct horse battery", "alex@example.com", body)
        self.assertEqual(
            body["masterPasswordHash"], vaultwarden_api.master_password_hash(master_key, "correct horse battery")
        )
        user_key = vaultwarden_api.decrypt_bytes(body["key"], vaultwarden_api.stretch(master_key))
        self.assertEqual(len(user_key), 64)

    def test_existing_account_is_reported_plainly(self):
        client = httpx.Client(
            base_url="http://vault",
            transport=httpx.MockTransport(lambda _r: httpx.Response(400, text="User already exists")),
        )
        with self.assertRaises(vaultwarden_api.VaultError) as caught:
            vaultwarden_api.register("http://vault", "a@b.co", "correct horse battery", "A", client=client)
        self.assertEqual(caught.exception.code, "exists")


class EmbeddingTests(unittest.TestCase):
    def test_waits_for_the_model_to_load(self):
        vector = {"data": [{"embedding": [0.1] * 768}]}
        answers = iter([(0, {}), (0, {}), (200, vector)])
        with patch.object(core_setup, "_http_json", side_effect=lambda *a, **k: next(answers)) as call:
            self.assertTrue(core_setup._embedding_ok({}, sleep=lambda _s: None))
        self.assertEqual(call.call_count, 3)
        self.assertGreaterEqual(call.call_args.kwargs["timeout"], 60)

    def test_gives_up_after_its_attempts(self):
        with patch.object(core_setup, "_http_json", return_value=(500, {})):
            self.assertFalse(core_setup._embedding_ok({}, sleep=lambda _s: None))


class AccountQuestionTests(unittest.TestCase):
    def ask(self, answers: list[str]) -> tuple[dict, str]:
        replies = iter(answers)
        screen = terminal.Screen.__new__(terminal.Screen)
        screen.tty = False
        out = io.StringIO()
        with (
            patch.object(terminal, "_ask", side_effect=lambda *_a, **_k: next(replies)),
            patch.object(terminal.Screen, "pause", lambda _self: None),
            patch("sys.stdout", out),
        ):
            account = terminal.ask_new_account(screen)
        return account, out.getvalue()

    def test_retries_until_the_answers_are_usable(self):
        account, output = self.ask(
            [
                "Alex",
                "not-an-email",
                "alex@example.com",
                "short",
                "alex-is-great-123",
                "correct horse battery",
                "different words here",
                "correct horse battery",
                "correct horse battery",
                "",
            ]
        )
        self.assertEqual(account, {"name": "Alex", "email": "alex@example.com", "password": "correct horse battery"})
        self.assertIn("doesn't look like an email", output)
        self.assertIn("Too short", output)
        self.assertIn("don't use your email", output)
        self.assertIn("were different", output)
        self.assertIn("cannot be recovered", output)


class LogFileTests(unittest.TestCase):
    def test_log_lines_never_reach_the_screen(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            screen = terminal.Screen.__new__(terminal.Screen)
            screen.log_file = (Path(tmp) / "install.log").open("a", encoding="utf-8")
            screen.log("docker", "$ sudo apt-get install docker-ce")
            screen.log_file.close()
            self.assertIn("docker: $ sudo apt-get install docker-ce", (Path(tmp) / "install.log").read_text())


if __name__ == "__main__":
    unittest.main()
