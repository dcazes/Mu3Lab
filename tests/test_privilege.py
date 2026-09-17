"""Mu3Lab :: tests/test_privilege.py

WHAT: Tests for ctl/privilege.py with injected fakes — no sudo, pkexec, or
      subprocess ever runs. Elevation ORDER is what's pinned: fresh sudo
      first, pkexec second, terminal fallback last.
WHY:  Privilege is the scariest code here. These tests prove the fallback
      chain without ever elevating, and prove no secret-handling surface
      exists (no parameter may be named *password*).
RUN:  `.venv/bin/python -m unittest tests.test_privilege -v`.
DEBUG: Fake _exec as `lambda argv: (0, "ok")`; force paths with _sudo_fresh /
      _agent overrides.
"""

import inspect
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl import privilege


def _silent(message: str) -> None:
    pass


class OrderTests(unittest.TestCase):
    def test_fresh_sudo_runs_direct(self):
        calls: list[list[str]] = []
        result = privilege.run_privileged(
            ["apt-get", "update"], _silent,
            _exec=lambda argv: (calls.append(argv) or (0, "ok")),
            _sudo_fresh=True)
        self.assertTrue(result["ok"])
        self.assertEqual(calls[0][:2], ["sudo", "apt-get"])
        self.assertNotIn("pkexec", calls[0])

    def test_pkexec_when_stale_but_graphical(self):
        calls: list[list[str]] = []
        result = privilege.run_privileged(
            ["usermod", "-aG", "docker", "dak"], _silent,
            _exec=lambda argv: (calls.append(argv) or (0, "ok")),
            _sudo_fresh=False, _agent=True)
        self.assertTrue(result["ok"])
        self.assertEqual(calls[0][0], "pkexec")

    def test_terminal_fallback(self):
        result = privilege.run_privileged(
            ["systemctl", "enable", "--now", "docker"], _silent,
            _sudo_fresh=False, _agent=False)
        self.assertFalse(result["ok"])
        self.assertTrue(result["need_terminal"])
        self.assertEqual(result["terminal_command"],
                         "sudo systemctl enable --now docker")


class QuotingTests(unittest.TestCase):
    def test_quoting(self):
        cmd = privilege.quote_terminal(["usermod", "-aG", "my group", "dak"])
        self.assertEqual(cmd, "sudo usermod -aG 'my group' dak")

    def test_agent_detection(self):
        self.assertTrue(privilege.has_polkit_agent({"WAYLAND_DISPLAY": "wayland-0"}))
        self.assertFalse(privilege.has_polkit_agent({}))


class NoSecretsTests(unittest.TestCase):
    def test_no_secret_params(self):
        # The backend must never accept secrets: fail if any parameter in
        # this module is named like a credential.
        import ctl.privilege as module
        for name, func in vars(module).items():
            if not callable(func) or not getattr(func, "__module__", "") == module.__name__:
                continue
            for param in inspect.signature(func).parameters:
                self.assertNotIn("password", param.lower(), name)
                self.assertNotIn("secret", param.lower(), name)
                self.assertNotIn("token", param.lower(), name)


class SessionTests(unittest.TestCase):
    def tearDown(self):
        privilege.release_elevation()

    def test_fresh_sudo_needs_no_worker(self):
        with unittest.mock.patch.object(
                privilege, "has_fresh_sudo", return_value=True):
            mode = privilege.ensure_elevation(_silent)
        self.assertEqual(mode, "sudo")
        self.assertIsNone(privilege._worker)

    def test_worker_spawned_once(self):
        made: list[str] = []
        class FakeWorker:
            def start(self, log=None):
                made.append("spawn")
                return True
            def alive(self):
                return True
        with unittest.mock.patch.object(
                privilege, "has_fresh_sudo", return_value=False), \
             unittest.mock.patch.object(
                privilege, "has_polkit_agent", return_value=True), \
             unittest.mock.patch("ctl.elevate.Worker", FakeWorker):
            self.assertEqual(privilege.ensure_elevation(_silent), "worker")
            # Second call reuses; no second spawn.
            self.assertEqual(privilege.ensure_elevation(_silent), "worker")
        self.assertEqual(made, ["spawn"])

    def test_cancelled_dialog_falls_back(self):
        class DeadWorker:
            def start(self, log=None):
                return False
        with unittest.mock.patch.object(
                privilege, "has_fresh_sudo", return_value=False), \
             unittest.mock.patch.object(
                privilege, "has_polkit_agent", return_value=True), \
             unittest.mock.patch("ctl.elevate.Worker", DeadWorker):
            logged: list[str] = []
            mode = privilege.ensure_elevation(logged.append)
        self.assertEqual(mode, "terminal")

    def test_run_prefers_live_worker(self):
        class LiveWorker:
            def alive(self):
                return True
            def run(self, argv, timeout=300):
                return 0, "via-worker"
        privilege._worker = LiveWorker()
        logged: list[str] = []
        result = privilege.run_privileged(["apt-get", "update"], logged.append)
        self.assertTrue(result["ok"])
        self.assertIn("via-worker", logged[-1])

    def test_release_stops_worker(self):
        stopped: list[str] = []
        class LiveWorker:
            def alive(self):
                return True
            def stop(self):
                stopped.append("stop")
        privilege._worker = LiveWorker()
        privilege.release_elevation()
        self.assertEqual(stopped, ["stop"])
        self.assertIsNone(privilege._worker)


if __name__ == "__main__":
    unittest.main()
