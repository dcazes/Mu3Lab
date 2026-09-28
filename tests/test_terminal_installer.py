"""The terminal installer's questions, account creation and core-app checks."""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ctl import core_setup, vaultwarden_api
from ctl.bootstrap import terminal


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
        self.assertEqual(seen["path"], "/api/accounts/register")
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
