"""Mu3Lab :: tests/test_actions.py

WHAT: Tests for ctl/actions.py with the privilege layer mocked — no apt,
      systemctl, usermod, or docker ever runs. What's pinned: exact argv
      handed to elevation, terminal-fallback propagation, and the ok/changed
      envelope shape.
WHY:  Host mutations are the blast radius. Reviewing recorded argv in tests
      is how we audit "what would this run on my box" without running it.
RUN:  `.venv/bin/python -m unittest tests.test_actions -v`.
DEBUG: Patch target is `ctl.actions.privilege` (module attribute) so production
      code paths stay identical; only the elevation backend is fake.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl import actions


def _silent(message: str) -> None:
    pass


def _privileged_ok(argv, log):
    log("$ sudo " + " ".join(argv))
    return {"ok": True, "rc": 0, "output": "ok"}


def _privileged_terminal(argv, log):
    return {"ok": False, "need_terminal": True,
            "terminal_command": "sudo " + " ".join(argv)}


class EnvelopeTests(unittest.TestCase):
    def test_ok_shape(self):
        result = actions._ok(["line"])
        self.assertEqual((result["ok"], result["changed"], result["log"]),
                         (True, True, ["line"]))

    def test_fail_shape(self):
        result = actions._fail(["boom"])
        self.assertEqual((result["ok"], result["changed"]), (False, False))


class AptTests(unittest.TestCase):
    def test_install_argv(self):
        seen: list[list[str]] = []
        def fake(argv, log):
            seen.append(argv)
            return _privileged_ok(argv, log)
        with patch.object(actions.privilege, "run_privileged", fake):
            result = actions.apt_install(["docker-ce", "tailscale"], _silent)
        self.assertTrue(result["ok"])
        self.assertEqual(seen[0], ["apt-get", "install", "-y",
                                   "docker-ce", "tailscale"])

    def test_empty_list_rejected(self):
        result = actions.apt_install([], _silent)
        self.assertFalse(result["ok"])

    def test_terminal_propagates(self):
        with patch.object(actions.privilege, "run_privileged",
                          _privileged_terminal):
            result = actions.apt_install(["docker-ce"], _silent)
        self.assertFalse(result["ok"])
        self.assertIn("terminal_command", result)
        self.assertIn("docker-ce", result["terminal_command"])


class SystemTests(unittest.TestCase):
    def test_enable_argv(self):
        seen: list[list[str]] = []
        def fake(argv, log):
            seen.append(argv)
            return _privileged_ok(argv, log)
        with patch.object(actions.privilege, "run_privileged", fake):
            result = actions.systemctl_enable_now("docker", _silent)
        self.assertTrue(result["ok"])
        self.assertEqual(seen[0], ["systemctl", "enable", "--now", "docker"])

    def test_usermod_append_only(self):
        # -aG (append) is load-bearing: without -a the user LOSES groups.
        seen: list[list[str]] = []
        def fake(argv, log):
            seen.append(argv)
            return _privileged_ok(argv, log)
        with patch.object(actions.privilege, "run_privileged", fake):
            actions.usermod_add_group("dak", "docker", _silent)
        self.assertEqual(seen[0], ["usermod", "-aG", "docker", "dak"])

    def test_network_unprivileged(self):
        # Network creation must NOT go through elevation (relies on group).
        seen: list[list[str]] = []
        def fake(argv, timeout=300):
            seen.append(argv)
            return 0, "created"
        with patch.object(actions.privilege, "_exec", fake):
            result = actions.docker_network_create("mu3lab_backend", _silent,
                                                   internal=True)
        self.assertTrue(result["ok"])
        self.assertEqual(seen[0], ["docker", "network", "create",
                                   "--internal", "mu3lab_backend"])


if __name__ == "__main__":
    unittest.main()
