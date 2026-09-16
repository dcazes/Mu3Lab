"""Mu3Lab :: tests/test_install.py

WHAT: Tests for ctl/install.py dispatch WITHOUT touching the host: the pure
      state→fix mapping, plus fix functions with mocked actions asserting
      that each state triggers EXACTLY its remedy (daemon_down starts but
      never reinstalls; ready touches nothing).
WHY:  "Remediate precisely, never reinstall" is the core promise of card ③.
      These tests are the executable proof: wrong remedy = red test.
RUN:  `.venv/bin/python -m unittest tests.test_install -v`.
DEBUG: Patch target is `ctl.install.actions` attributes (module looked up at
      call time, so production code paths stay identical).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl import install


def _ctx(log=None, inputs=None):
    lines: list[str] = []
    return {"root": Path("/nonexistent"), "log_fn": lambda step: lines.append,
            "inputs": inputs or {}, "wait_input": lambda step: {},
            "stopped": lambda: False, "lines": lines}


def _ok(*args, **kwargs):
    return {"ok": True, "changed": True, "log": []}


class DispatchTests(unittest.TestCase):
    def test_every_state_maps(self):
        # Every dispatchable (step, state) pair resolves; typos surface as
        # "unknown" instead of silently skipping.
        for (step, state), fix in install.DISPATCH.items():
            self.assertEqual(install.fix_for_state(step, state), fix)

    def test_unknown_state(self):
        self.assertEqual(install.fix_for_state("docker", "nope"), "unknown")

    def test_ready_always_skips(self):
        for step in ("host_base", "node", "venv", "docker", "tailscale_pkg",
                     "caddy"):
            self.assertEqual(install.fix_for_state(step, "ready"), "skip")

    def test_steps_have_check_and_fix(self):
        # Every STEPS entry is runnable by run_job: id unique, check+fix set.
        ids = [meta["id"] for meta in install.STEPS]
        self.assertEqual(len(ids), len(set(ids)))
        for meta in install.STEPS:
            self.assertTrue(callable(meta["check"]), meta["id"])
            self.assertTrue(callable(meta["fix"]), meta["id"])

    def test_job_matches_steps(self):
        job = install.new_job()
        self.assertEqual([s["id"] for s in job["steps"]],
                         [m["id"] for m in install.STEPS])
        for step in job["steps"]:
            self.assertEqual(step["status"], "pending")


class DockerFixTests(unittest.TestCase):
    def _check(self, state):
        return {"name": "docker", "status": "missing", "detail": state,
                "action": "", "state": state, "blocking": False}

    def test_down_starts_never_reinstalls(self):
        with patch("ctl.install.actions.systemctl_enable_now",
                   return_value=_ok()) as start, \
             patch("ctl.install.actions.apt_install") as apt, \
             patch("ctl.install.actions.usermod_add_group",
                   return_value=_ok()), \
             patch("ctl.install.preflight") as _pre:
            _pre.MU3LAB_NETWORKS = []
            import grp
            with patch("os.getgrouplist", return_value=[0]):
                with patch.object(grp, "getgrgid") as getgr:
                    getgr.return_value.gr_name = "docker"
                    result = install.fix_docker(
                        self._check("daemon_down"), _ctx())
        self.assertTrue(result.get("ok"))
        start.assert_called_once()
        apt.assert_not_called()

    def test_networks_only_creates_missing(self):
        created: list[str] = []
        def fake_net(name, log, internal=False):
            created.append(name)
            return _ok()
        with patch("ctl.install.actions.usermod_add_group",
                   return_value=_ok()), \
             patch("ctl.install.actions.docker_network_create",
                   side_effect=fake_net), \
             patch("ctl.install.actions.privilege") as priv, \
             patch("ctl.install.preflight") as _pre:
            _pre.MU3LAB_NETWORKS = ["mu3lab_frontend", "mu3lab_backend"]
            # frontend present, backend missing: only backend gets created.
            priv._exec.side_effect = lambda argv: (
                (0, "") if argv[-1] == "mu3lab_frontend" else (1, ""))
            import grp
            with patch("os.getgrouplist", return_value=[0]):
                with patch.object(grp, "getgrgid") as getgr:
                    getgr.return_value.gr_name = "docker"
                    result = install.fix_docker(
                        self._check("no_networks"), _ctx())
        self.assertTrue(result.get("ok"))
        self.assertEqual(created, ["mu3lab_backend"])

    def test_group_pause_is_waiting(self):
        with patch("ctl.install.actions.usermod_add_group",
                   return_value=_ok()), \
             patch("os.getgrouplist", return_value=[0]):
            import grp
            with patch.object(grp, "getgrgid") as getgr:
                getgr.return_value.gr_name = "users"  # group NOT live
                result = install.fix_docker(self._check("no_group"), _ctx())
        self.assertTrue(result.get("waiting"))
        self.assertEqual(result["prompt"]["kind"], "relogin")
        self.assertIn("newgrp", str(result["prompt"].get("commands")))


class PropagateTests(unittest.TestCase):
    def test_terminal_becomes_waiting(self):
        result = install._propagate({"ok": False, "changed": False, "log": [],
                                     "need_terminal": True,
                                     "terminal_command": "sudo apt-get update"})
        self.assertTrue(result.get("waiting"))
        self.assertEqual(result["prompt"]["kind"], "terminal")
        self.assertIn("apt-get", result["prompt"]["terminal_command"])

    def test_failure_passes_error(self):
        result = install._propagate({"ok": False, "changed": False,
                                     "log": ["boom"]})
        self.assertFalse(result.get("ok"))
        self.assertIn("boom", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
