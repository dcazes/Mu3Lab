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
        # Network creation must NOT go through elevation (relies on group
        # or the sg fallback inside docker_cmd, never pkexec/sudo).
        seen: list[list[str]] = []
        def fake(argv, timeout=300, env=None):
            seen.append(argv)
            return 0, "created"
        def _no_elevate(*args, **kwargs):
            raise AssertionError("must not elevate")
        with patch.object(actions.privilege, "_exec", fake), \
             patch.object(actions.privilege, "run_privileged", _no_elevate), \
             patch("shutil.which", return_value="/usr/bin/sg"), \
             patch("ctl.preflight._db_has_group", return_value=True):
            result = actions.docker_network_create("mu3lab_backend", _silent,
                                                   internal=True)
        self.assertTrue(result["ok"])
        # Direct or sg-wrapped — but never elevated.
        flat = " ".join(seen[0])
        self.assertIn("docker", flat)
        self.assertIn("mu3lab_backend", flat)

    def test_runtime_layout_keeps_secrets_root_only(self):
        seen: list[list[str]] = []
        def fake(argv, log):
            seen.append(argv)
            return {"ok": True}
        with patch.object(actions.privilege, "run_privileged", fake):
            result = actions.ensure_runtime_layout(Path("/srv/mu3lab"), "tester", _silent)
        self.assertTrue(result["ok"])
        self.assertIn(["install", "-d", "-m", "0700", "/srv/mu3lab/secrets"], seen)
        self.assertIn(["chown", "root:tester", "/srv/mu3lab"], seen)
        self.assertNotIn("/srv/mu3lab/secrets", seen[-1])

    def test_remove_root_file_uses_exact_path(self):
        seen: list[list[str]] = []
        def fake(argv, log):
            seen.append(argv)
            return _privileged_ok(argv, log)
        with patch.object(actions.privilege, "run_privileged", fake):
            result = actions.remove_root_file(
                "/etc/apt/sources.list.d/docker.list", _silent)
        self.assertTrue(result["ok"])
        self.assertEqual(seen[0], ["rm", "-f",
                                   "/etc/apt/sources.list.d/docker.list"])


class DockerCmdTests(unittest.TestCase):
    """Selection contract for the docker choke point: live group → direct;
    DB-member-only → `sg docker -c`; neither → clean error, nothing runs."""

    def test_direct_with_live_group(self):
        seen: list = []
        def fake(argv, timeout=300, env=None):
            seen.append(argv)
            return 0, "ok"
        # Step 1 must run before Docker is installed.  Do not make this unit
        # test depend on the host's /etc/group (a fresh host has no docker
        # group yet).
        docker_gid = 4242
        with patch.object(actions.privilege, "_exec", fake), \
             patch("os.getgroups", return_value=[docker_gid]), \
             patch("grp.getgrgid") as getgrgid:
            getgrgid.return_value.gr_name = "docker"
            rc, _ = actions.docker_cmd(["docker", "info"], _silent)
        self.assertEqual(rc, 0)
        self.assertEqual(seen[0][:2], ["docker", "info"])

    def test_sg_when_db_only(self):
        seen: list = []
        def fake(argv, timeout=300, env=None):
            seen.append(argv)
            return 0, "ok"
        with patch.object(actions.privilege, "_exec", fake), \
             patch("os.getgroups", return_value=[1000]), \
             patch("shutil.which", return_value="/usr/bin/sg"), \
             patch("ctl.preflight._db_has_group", return_value=True):
            rc, _ = actions.docker_cmd(
                ["docker", "network", "create", "net with space"], _silent)
        self.assertEqual(rc, 0)
        # sg wrapper, single -c string, space-containing arg safely quoted.
        self.assertEqual(seen[0][:3], ["sg", "docker", "-c"])
        self.assertIn("net with space", seen[0][3])
        self.assertNotIn("sudo", seen[0])
        self.assertNotIn("pkexec", seen[0])

    def test_error_when_nowhere(self):
        with patch("os.getgroups", return_value=[1000]), \
             patch("shutil.which", return_value=None), \
             patch("ctl.preflight._db_has_group", return_value=False):
            rc, out = actions.docker_cmd(["docker", "info"], _silent)
        self.assertNotEqual(rc, 0)
        self.assertIn("docker unavailable", out)

    def test_docker_config_isolated(self):
        # Worker/root-run docker must not poison ~/.docker for the user.
        seen: list = []
        def fake(argv, timeout=300, env=None):
            seen.append(env or {})
            return 0, "ok"
        with patch.object(actions.privilege, "_exec", fake), \
             patch("os.getgroups", return_value=[4242]), \
             patch("grp.getgrgid") as getgrgid:
            getgrgid.return_value.gr_name = "docker"
            actions.docker_cmd(["docker", "info"], _silent)
        self.assertIn("DOCKER_CONFIG", seen[0])
        self.assertNotIn(".docker", seen[0]["DOCKER_CONFIG"].replace(
            "mu3lab-docker-cfg", ""))


class ComposeTests(unittest.TestCase):
    def test_extra_compose_file_is_appended_after_base(self):
        seen: list[list[str]] = []
        def fake(argv, log, timeout=300, env=None):
            seen.append(argv)
            return 0, "ok"
        project = Path("/srv/mu3lab/projects/vaultwarden")
        with patch.object(actions, "docker_cmd", fake):
            actions.compose_up(project, _silent,
                               extra_files=[project / "docker-compose.tailnet.yml"])
        self.assertEqual(seen[0], [
            "docker", "compose",
            "-f", "/srv/mu3lab/projects/vaultwarden/docker-compose.yml",
            "-f", "/srv/mu3lab/projects/vaultwarden/docker-compose.tailnet.yml",
            "--project-directory", "/srv/mu3lab/projects/vaultwarden",
            "up", "-d",
        ])


if __name__ == "__main__":
    unittest.main()
