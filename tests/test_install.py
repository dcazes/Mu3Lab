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

    def test_runtime_layout_check_uses_persistent_root_not_checkout(self):
        step = next(meta for meta in install.STEPS if meta["id"] == "runtime_layout")
        with patch("ctl.install._runtime_layout_check", return_value={"state": "ready"}) as check:
            step["check"](_ctx())
        self.assertEqual(check.call_args.args[0], install.RuntimePaths().root)

    def test_tailscale_serve_uses_privilege_boundary(self):
        with patch("ctl.install.privilege.run_privileged", return_value={"ok": True}) as run:
            result = install.fix_serve({}, _ctx())
        self.assertTrue(result["ok"])
        self.assertEqual(run.call_args.args[0],
                         ["tailscale", "serve", "--bg", install.SERVE_PORT])
        self.assertEqual(run.call_args.kwargs["timeout"], 60)


class DockerFixTests(unittest.TestCase):
    def _check(self, state):
        return {"name": "docker", "status": "missing", "detail": state,
                "action": "", "state": state, "blocking": False}

    def test_down_starts_never_reinstalls(self):
        with patch("ctl.install.actions.systemctl_enable_now",
                   return_value=_ok()) as start, \
             patch("ctl.install.actions.apt_install") as apt, \
             patch("ctl.install.actions.usermod_add_group",
                   return_value=_ok()):
            result = install.fix_docker(self._check("daemon_down"), _ctx())
        self.assertTrue(result.get("ok"))
        start.assert_called_once()
        apt.assert_not_called()

    def test_networks_only_creates_missing(self):
        # Networks are a DEDICATED step now (fix_networks_router): only the
        # missing ones get created, and liveness is someone else's job.
        # Live group is stubbed (never the test box's real groups).
        created: list[str] = []
        def fake_net(name, log, internal=False):
            created.append(name)
            return _ok()
        import types as _types
        fake_grp = _types.SimpleNamespace(gr_name="docker")
        with patch("ctl.install.actions.docker_network_create",
                   side_effect=fake_net), \
             patch("ctl.install.actions.privilege") as priv, \
             patch("ctl.install.preflight") as _pre, \
             patch("os.getgroups", return_value=[999]), \
             patch("grp.getgrgid", return_value=fake_grp):
            _pre.MU3LAB_NETWORKS = ["mu3lab_frontend", "mu3lab_backend"]
            # frontend present, backend missing: only backend gets created.
            priv._exec.side_effect = lambda argv, **kw: (
                (0, "") if argv[-1] == "mu3lab_frontend" else (1, ""))
            result = install.fix_networks_router(
                self._check("missing"), _ctx())
        self.assertTrue(result.get("ok"))
        self.assertEqual(created, ["mu3lab_backend"])

    def test_group_ensures_membership_defers_liveness(self):
        # fix_docker ensures membership but NEVER judges liveness (that's the
        # checkpoint's job): no waiting here, just ok.
        with patch("ctl.install.actions.usermod_add_group",
                   return_value=_ok()) as mod:
            result = install.fix_docker(self._check("no_group"), _ctx())
        self.assertTrue(result.get("ok"))
        self.assertIsNone(result.get("waiting"))
        mod.assert_called_once()

    def test_probe_distinguishes_denied_from_down(self):        # REGRESSION: the install-side probe once discarded docker-info
        # stderr, misreading "permission denied" as a dead daemon (which then
        # failed verify after a pointless start). It must mirror run_all().
        def fake_exec(argv, timeout=300):
            cmd = " ".join(argv)
            if argv[:2] == ["docker", "info"]:
                return 1, "permission denied while trying to connect"
            if argv[:2] == ["systemctl", "is-active"]:
                return 0, "active"
            return 1, ""
        import shutil as _sh
        with patch("ctl.install.actions.privilege") as priv, \
             patch.object(_sh, "which", return_value="/usr/bin/docker"), \
             patch("ctl.preflight._db_has_group", return_value=False):
            priv._exec.side_effect = fake_exec
            check = install._docker_check(_ctx())
        self.assertEqual(check["state"], "no_access")

    def test_verify_tolerates_downstream_states(self):
        meta = next(m for m in install.STEPS if m["id"] == "docker")
        for state in ("ready", "no_group", "no_networks", "no_access"):
            self.assertIn(state, meta["verify_ok_states"])
        self.assertNotIn("daemon_down", meta["verify_ok_states"])


class CaddyFixTests(unittest.TestCase):
    """`compose up -d` returns at container START, not when Caddy listens:
    the fix must poll the port (the live failure), not declare victory."""

    def _ctx(self, root):
        return {"root": root, "log_fn": lambda step: lambda line: None,
                "inputs": {}, "wait_input": lambda step: {},
                "stopped": lambda: False}

    def _projdir(self, root):
        projdir = root / "core" / "ingress"
        projdir.mkdir(parents=True)
        (projdir / "docker-compose.yml").touch()
        return projdir

    def test_waits_for_port(self):
        import tempfile
        import urllib.request as _url
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._projdir(root)
            calls = {"n": 0}

            class FakeResp:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            with patch("ctl.install.actions.compose_up",
                       return_value=(0, "up")), \
                 patch("ctl.install._tcp_open",
                       side_effect=lambda port: calls.__setitem__(
                           "n", calls["n"] + 1) or calls["n"] >= 2), \
                 patch.object(_url, "urlopen", return_value=FakeResp()):
                result = install.fix_caddy({"state": "down"}, self._ctx(root))
            self.assertTrue(result.get("ok"))
            self.assertGreaterEqual(calls["n"], 2)

    def test_times_out_honestly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._projdir(root)
            with patch("ctl.install.actions.compose_up",
                       return_value=(0, "up")), \
                 patch("ctl.install._tcp_open", return_value=False), \
                 patch("time.sleep", return_value=None):
                result = install.fix_caddy({"state": "down"}, self._ctx(root))
            self.assertFalse(result.get("ok"))
            self.assertIn("19460", result.get("error", ""))


class WorkspaceStepTests(unittest.TestCase):
    def _ctx(self, root):
        return {"root": root, "log_fn": lambda step: lambda line: None,
                "inputs": {}, "wait_input": lambda step: {},
                "stopped": lambda: False}

    def test_venv_ready_skips(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "python").touch()
            check = install._venv_check(root)
            self.assertEqual((check["status"], check["state"]), ("ok", "ready"))
            self.assertEqual(install.fix_for_state("venv", "ready"), "skip")

    def test_venv_missing_creates(self):
        import tempfile
        from unittest.mock import patch as _patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            check = install._venv_check(root)
            self.assertEqual(check["state"], "no_venv")
            def fake_run(argv, **kwargs):
                # Simulate a real venv creation (mock must produce the
                # artifact the fix verifies, like the real command would).
                (root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
                (root / ".venv" / "bin" / "python").touch(exist_ok=True)
                result = type("R", (), {})()
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
                return result
            with _patch("subprocess.run", side_effect=fake_run) as run:
                result = install.fix_venv(check, self._ctx(root))
            self.assertTrue(result.get("ok"))
            run.assert_called_once()

    def test_pip_missing_installs(self):
        import tempfile
        from unittest.mock import patch as _patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "pip").touch()
            (root / "ctl").mkdir()
            (root / "ctl" / "requirements.txt").touch()
            with _patch("subprocess.run") as run:
                run.return_value.returncode = 1  # import fastapi fails…
                run.return_value.stdout = ""
                run.return_value.stderr = ""
                check = install._pip_check(root)
                self.assertEqual(check["state"], "missing")
                run.return_value.returncode = 0  # …but pip install works
                result = install.fix_pip_deps(check, self._ctx(root))
            self.assertTrue(result.get("ok"))

    def test_build_stale_rebuilds(self):
        import tempfile
        import time
        from unittest.mock import patch as _patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dash = root / "dashboard"
            (dash / "src").mkdir(parents=True)
            (dash / "src" / "a.tsx").touch()
            (dash / "dist").mkdir(parents=True)
            (dash / "dist" / "index.html").touch()
            # Make src NEWER than dist → stale.
            now = time.time()
            import os as _os
            _os.utime(dash / "dist" / "index.html", (now - 100, now - 100))
            check = install._build_check(root)
            self.assertEqual(check["state"], "stale")
            (dash / "node_modules").mkdir()  # skip npm ci, test build only
            with _patch("subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "built"
                run.return_value.stderr = ""
                result = install.fix_dashboard_build(check, self._ctx(root))
            self.assertTrue(result.get("ok"))
            run.assert_called_once()  # build only, no npm ci

    def test_src_missing_fails_plainly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            check = install._src_check(Path(tmp))
            self.assertEqual(check["status"], "fail")
            result = install.fix_dashboard_src(check, self._ctx(Path(tmp)))
            self.assertFalse(result.get("ok"))
    def test_step_order(self):
        # A real login refresh is mandatory before Docker-backed work; tailnet
        # connection is established before runtime, ingress, and sharing.
        ids = [m["id"] for m in install.STEPS]
        self.assertLess(ids.index("docker"), ids.index("docker_session"))
        self.assertLess(ids.index("docker_session"), ids.index("tailscale_join"))
        self.assertLess(ids.index("tailscale_join"), ids.index("serve"))
        self.assertLess(ids.index("tailscale_join"), ids.index("docker_networks"))
        self.assertLess(ids.index("docker_networks"), ids.index("caddy"))

    def test_pkg_verify_tolerates_unjoined(self):
        # Installing must not flunk itself on the NEXT step's job.
        meta = next(m for m in install.STEPS if m["id"] == "tailscale_pkg")
        self.assertIn("unjoined", meta["verify_ok_states"])
        meta = next(m for m in install.STEPS if m["id"] == "docker")
        self.assertIn("no_access", meta["verify_ok_states"])

    def test_join_prompt_guides(self):
        prompt = install._join_prompt("https://login.example/abc")
        self.assertEqual(prompt["kind"], "tailscale_login")
        for needle in ("Tailscale web login", "https://login.example/abc",
                       "open_tailscale_login.sh"):
            self.assertIn(needle, prompt["body"] + prompt.get("login_url", "")
                          + prompt.get("terminal_command", ""))
        self.assertNotIn("keys_url", prompt)
        self.assertEqual(prompt["login_url"], "https://login.example/abc")

    def test_join_prompt_keeps_copyable_fallback_command(self):
        prompt = install._join_prompt("")
        self.assertFalse(prompt["login_url"])
        self.assertEqual(prompt["terminal_command"],
                         "./tools/open_tailscale_login.sh")

    def test_tailscale_join_uses_elevation_worker_and_opens_url(self):
        with patch("ctl.install.privilege.run_privileged", return_value={
                "ok": True,
                "output": "To authenticate, visit: https://login.tailscale.com/a/abc123"}) as run, \
             patch("ctl.install.webbrowser.open", return_value=True) as opened:
            result = install.fix_tailscale_join(
                {"state": "unjoined"}, self._ctx(Path("/nonexistent")))
        run.assert_called_once_with(
            ["tailscale", "up", "--hostname=mu3lab", "--timeout=120s"],
            unittest.mock.ANY, timeout=130)
        opened.assert_called_once_with("https://login.tailscale.com/a/abc123", new=2)
        self.assertTrue(result["waiting"])
        self.assertEqual(result["prompt"]["login_url"],
                         "https://login.tailscale.com/a/abc123")

    def test_tailscale_join_reads_pending_url_from_local_status(self):
        status = type("Proc", (), {
            "returncode": 0,
            "stdout": '{"AuthURL":"https://login.tailscale.com/a/from-status"}',
        })()
        with patch("ctl.install.privilege.run_privileged", return_value={
                "ok": False, "output": "timeout waiting"}), \
             patch("ctl.install.subprocess.run", return_value=status) as status_run, \
             patch("ctl.install.webbrowser.open", return_value=True):
            result = install.fix_tailscale_join(
                {"state": "unjoined"}, self._ctx(Path("/nonexistent")))
        status_run.assert_called_once_with(["tailscale", "status", "--json"],
                                           capture_output=True, text=True, timeout=10)
        self.assertEqual(result["prompt"]["login_url"],
                         "https://login.tailscale.com/a/from-status")

    def test_tailscale_key_url_shape(self):
        # Slash-separated or it 404s (verified live against pkgs.tailscale.com
        # after the dotted form failed a real install). Never trust memory.
        self.assertEqual(
            install.tailscale_key_url("ubuntu", "noble"),
            "https://pkgs.tailscale.com/stable/ubuntu/noble.gpg")
        self.assertEqual(
            install.tailscale_key_url("linuxmint", "noble"),
            "https://pkgs.tailscale.com/stable/ubuntu/noble.gpg")
        self.assertEqual(
            install.tailscale_key_url("debian", "bookworm"),
            "https://pkgs.tailscale.com/stable/debian/bookworm.gpg")


class DockerSessionTests(unittest.TestCase):
    """The user-visible Docker boundary requires a real re-login."""
    def _ctx(self):
        return {"root": Path("/nonexistent"),
                "log_fn": lambda step: lambda line: None,
                "inputs": {}, "wait_input": lambda step: {},
                "stopped": lambda: False}

    def test_relogin_checkpoint_exists(self):
        ids = [m["id"] for m in install.STEPS]
        self.assertIn("docker_session", ids)

    def test_missing_session_waits_without_running_docker(self):
        result = install.fix_docker_session(
            {"state": "relogin_required"}, self._ctx())
        self.assertTrue(result.get("waiting"))
        self.assertEqual(result["prompt"]["kind"], "docker_relogin")
        self.assertIn("Log out and back in", result["prompt"]["body"])

    def test_networks_denied_proceeds(self):
        # denied probes no longer fail: fix_networks runs through docker_cmd
        # (sg when needed). Only a genuinely dead daemon fails.
        # (sg when needed). Only a genuinely dead daemon fails.
        check = {"name": "docker_networks", "status": "missing",
                 "detail": "x", "action": "y", "state": "denied",
                 "blocking": False}
        created: list[str] = []
        def fake_cmd(argv, log, timeout=300):
            created.append(" ".join(argv))
            return 0, "created"
        with unittest.mock.patch("ctl.install.actions.docker_cmd",
                                 side_effect=fake_cmd), \
             unittest.mock.patch("ctl.install.preflight") as _pre:
            _pre.MU3LAB_NETWORKS = ["mu3lab_backend"]
            result = install.fix_networks_router(check, self._ctx())
        self.assertTrue(result.get("ok"))
        self.assertTrue(any("mu3lab_backend" in cmd for cmd in created))

    def test_verify_uses_sg_path(self):
        # REGRESSION (the incident): fix ran through sg successfully while
        # verify probed the raw socket → red on existing networks. The check
        # must use docker_cmd (sg-aware) like the fix does.
        def fake_cmd(argv, log=None, timeout=300):
            joined = " ".join(argv)
            if "network inspect" in joined:
                return 0, "exists"  # all present via sg
            if argv[:2] == ["docker", "info"]:
                return 0, "ok"
            return 0, ""
        with unittest.mock.patch("ctl.install.actions.docker_cmd",
                                 side_effect=fake_cmd):
            check = install._networks_check(self._ctx())
        self.assertEqual((check["status"], check["state"]), ("ok", "ready"))

    def test_full_dispatch_coverage(self):        # Every state any step check can emit must map to a real fix.
        states = {
            "host_base": ["missing", "ready"],
            "node": ["absent", "old", "ready"],
            "venv": ["no_venv", "ready"],
            "pip_deps": ["missing", "ready"],
            "dashboard_src": ["missing", "ready"],
            "dashboard_build": ["stale", "ready"],
            "root_env": ["missing", "ready"],
            "runtime_layout": ["missing", "ready"],
            "service": ["no_unit", "inactive", "unhealthy", "ready"],
            "docker": ["absent", "daemon_down", "unverified", "old_engine",
                       "no_compose", "no_access", "stale_login", "no_group",
                       "no_networks", "ready"],
            "docker_session": ["relogin_required", "ready"],
            "tailscale_pkg": ["absent", "daemon_down", "unjoined", "ready"],
            "tailscale_join": ["unjoined", "ready"],
            "serve": ["unshared", "ready"],
            "docker_networks": ["missing", "denied", "ready"],
            "caddy": ["down", "ready"],
        }
        step_ids = {m["id"] for m in install.STEPS}
        self.assertEqual(set(states), step_ids)
        for step, lst in states.items():
            for state in lst:
                self.assertNotEqual(
                    install.fix_for_state(step, state), "unknown",
                    f"{step}/{state}")


class PropagateTests(unittest.TestCase):
    def test_collect_script(self):
        job = {"steps": [
            {"id": "a", "log": ["$ sudo apt-get update", "plain noise",
                                "$ sudo apt-get install -y docker-ce",
                                "$ sudo apt-get update"]},
            {"id": "b", "log": ["$ docker network create foo"]},
        ]}
        script = install.collect_privileged_script(job)
        # Deduped, sudo-prefixed lines only, runnable header present.
        self.assertIn("set -e", script.splitlines()[3])
        self.assertEqual(script.count("apt-get update"), 2)  # echo + command
        self.assertNotIn("plain noise", script)
        self.assertNotIn("docker network create", script)

    def test_collect_empty(self):
        self.assertEqual(install.collect_privileged_script({"steps": []}), "")

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

    def test_retry_clears_stale_state(self):
        # REGRESSION (the Caddy contradiction): a failed attempt's error text
        # rendered forever under a later success. Every terminal state must
        # write ALL of status/error/prompt/detail; only logs accumulate.
        events: list[dict] = []
        ctx = {"emit": events.append}
        step = {"id": "caddy", "label": "Caddy", "status": "pending",
                "log": ["old line"], "prompt": None, "error": "",
                "detail": ""}
        job = {"status": "running", "steps": [step], "events": []}
        install._finish_step(job, ctx, step,
                             {"ok": False, "error": "port never answered"})
        self.assertEqual(step["status"], "failed")
        self.assertTrue(step["error"])
        install._finish_step(job, ctx, step, {"ok": True, "skipped": True})
        self.assertEqual(step["status"], "ready")
        self.assertEqual(step["error"], "")
        self.assertIsNone(step["prompt"])
        self.assertIn("skipped", step["detail"])
        self.assertEqual(step["log"], ["old line"])


if __name__ == "__main__":
    unittest.main()
