"""Mu3Lab :: tests/test_privilege.py

WHAT: Tests for ctl/privilege.py with injected fakes — no sudo or subprocess
      ever runs.
WHY:  Privilege is the scariest code here. These tests pin the one elevation
      path (a fresh sudo session, always non-interactive), the plain-language
      failure when that session has expired, and that no secret-handling
      surface exists.
RUN:  `.venv/bin/python -m unittest tests.test_privilege -v`.
"""

import inspect
import unittest

from ctl import privilege


def _silent(message: str) -> None:
    pass


def _recorder(calls: list[list[str]], rc: int = 0):
    def fake(argv, timeout=300, env=None):
        calls.append(argv)
        return rc, "ok"

    return fake


class RunPrivilegedTests(unittest.TestCase):
    def test_fresh_sudo_runs_non_interactively(self):
        calls: list[list[str]] = []
        result = privilege.run_privileged(["apt-get", "update"], _silent, _exec=_recorder(calls), _sudo_fresh=True)
        self.assertTrue(result["ok"])
        # -n: an expired session must fail fast, never wait for a password.
        self.assertEqual(calls, [["sudo", "-n", "apt-get", "update"]])

    def test_expired_session_runs_nothing_and_explains_what_to_do(self):
        calls: list[list[str]] = []
        result = privilege.run_privileged(
            ["usermod", "-aG", "docker", "me"], _silent, _exec=_recorder(calls), _sudo_fresh=False
        )
        self.assertEqual(calls, [])
        self.assertFalse(result["ok"])
        self.assertTrue(result["need_terminal"])
        self.assertEqual(result["terminal_command"], "sudo usermod -aG docker me")
        self.assertIn("./install.sh", result["error"])

    def test_command_failure_is_reported(self):
        result = privilege.run_privileged(["false"], _silent, _exec=_recorder([], rc=1), _sudo_fresh=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["rc"], 1)

    def test_every_command_is_logged_before_it_runs(self):
        lines: list[str] = []
        privilege.run_privileged(
            ["apt-get", "install", "-y", "git"], lines.append, _exec=_recorder([]), _sudo_fresh=True
        )
        self.assertEqual(lines[0], "$ sudo apt-get install -y git")

    def test_quoting_is_shell_safe(self):
        self.assertEqual(privilege.quote_terminal(["echo", "a b", "$(x)"]), "sudo echo 'a b' '$(x)'")

    def test_fresh_sudo_probe_is_non_interactive(self):
        calls: list[list[str]] = []
        self.assertTrue(privilege.has_fresh_sudo(_recorder(calls)))
        self.assertEqual(calls, [["sudo", "-n", "true"]])

    def test_no_secret_parameters(self):
        for name, function in inspect.getmembers(privilege, inspect.isfunction):
            for parameter in inspect.signature(function).parameters:
                with self.subTest(function=name, parameter=parameter):
                    self.assertNotIn("password", parameter.lower())
                    self.assertNotIn("secret", parameter.lower())


if __name__ == "__main__":
    unittest.main()
