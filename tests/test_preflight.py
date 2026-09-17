"""Mu3Lab :: tests/test_preflight.py

WHAT: Unit tests for ctl/preflight.py. Every test feeds FIXTURES (fake
      os-release text, fake version strings, stub connect functions) — no test
      touches the real host, needs root, or needs Docker/Node installed.
WHY:  Preflight is the gate for everything downstream. Fixtures pin the
      contract: given this host description, expect exactly this verdict.
      Names stay short on purpose (test_ok / test_old / test_unknown…);
      fixtures carry the detail via subTest.
RUN:  `.venv/bin/python -m unittest tests.test_preflight -v` (or `make test`).
DEBUG: A failing test prints the check dict; compare `status`/`blocking`/
      `action` against BUILD_ORDER Phase 2.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl import preflight


class OsTests(unittest.TestCase):
    def test_ok(self):
        good = [
            ('ID=ubuntu\nVERSION_ID="22.04"', ""),
            ('ID=ubuntu\nVERSION_ID="24.04"', ""),
            ('ID=debian\nVERSION_ID="12"', ""),
            # Generic derivative: ID_LIKE + codename, no name special-cased.
            ('ID=pop\nID_LIKE=ubuntu\nVERSION_ID="22.04"\nUBUNTU_CODENAME=jammy', ""),
            ('ID=linuxmint\nID_LIKE="ubuntu debian"\nVERSION_ID="22.3"\n'
             'UBUNTU_CODENAME=noble', ""),
        ]
        for text, kernel in good:
            with self.subTest(text=text.splitlines()[0]):
                result = preflight.check_os(text, kernel_release=kernel)
                self.assertEqual(result["status"], "ok")

    def test_old(self):
        for text in ('ID=debian\nVERSION_ID="11"',
                     'ID=ubuntu\nVERSION_ID="20.04"'):
            with self.subTest(text=text.splitlines()[1]):
                result = preflight.check_os(text)
                self.assertEqual(result["status"], "fail")
                self.assertTrue(result["action"])

    def test_unknown(self):
        result = preflight.check_os('ID=arch\nVERSION_ID="1"\n')
        self.assertEqual(result["status"], "fail")

    def test_wsl(self):
        # WSL passes but must warn: systemd, polkit and Docker all differ.
        text = 'ID=ubuntu\nVERSION_ID="22.04"'
        result = preflight.check_os(text, kernel_release="5.15.0-microsoft-standard")
        self.assertEqual(result["status"], "ok")
        self.assertIn("WSL", result["detail"])
        self.assertIn("systemd", result["detail"])


class ArchTests(unittest.TestCase):
    def test_ok(self):
        for machine in ("x86_64", "aarch64", "arm64"):
            with self.subTest(machine=machine):
                self.assertEqual(preflight.check_arch(machine)["status"], "ok")

    def test_rejected(self):
        self.assertEqual(preflight.check_arch("i686")["status"], "fail")


class PythonNodeTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(preflight.check_python((3, 12, 3))["status"], "ok")
        self.assertEqual(preflight.check_node("v20.11.0")["status"], "ok")

    def test_newer_ok(self):
        # Witness values only: ANY version above minimum passes, nothing pins.
        self.assertEqual(preflight.check_python((3, 13, 0))["status"], "ok")
        self.assertEqual(preflight.check_node("v22.3.0")["status"], "ok")

    def test_old(self):
        self.assertEqual(preflight.check_python((3, 9, 18))["status"], "fail")
        # Old node is "missing", not "fail": step ③ upgrades it.
        result = preflight.check_node("v18.19.0")
        self.assertEqual(result["status"], "missing")
        self.assertIn("step 3", result["action"])

    def test_missing(self):
        result = preflight.check_node("")
        self.assertEqual(result["status"], "missing")
        self.assertIn("step 3", result["action"])


class PrivilegeTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(
            preflight.check_privilege(True, False)["status"], "ok")
        result = preflight.check_privilege(False, True)
        self.assertEqual(result["status"], "ok")
        self.assertIn("pkexec", result["detail"])

    def test_headless(self):
        result = preflight.check_privilege(False, False)
        self.assertEqual(result["status"], "missing")
        self.assertIn("terminal", result["action"])


class DockerTests(unittest.TestCase):
    def _base(self, **over):
        args = {"docker_info_rc": 0, "group_names": ["docker"],
                "networks_present": list(preflight.MU3LAB_NETWORKS),
                "engine_version": "25.0.3", "compose_present": True,
                "binary_present": True, "db_has_group": True}
        args.update(over)
        return preflight.check_docker(**args)

    def test_ok(self):
        result = self._base()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["state"], "ready")

    def test_absent(self):
        # No binary at all: install (not start, not fix) is the remedy.
        result = self._base(binary_present=False)
        self.assertEqual(result["state"], "absent")
        self.assertIn("not installed", result["detail"])

    def test_down(self):
        # Installed but silent: start it, never reinstall.
        result = self._base(docker_info_rc=1)
        self.assertEqual(result["state"], "daemon_down")
        self.assertIn("no reinstall", result["action"])

    def test_denied_is_not_down(self):
        # Permission-denied looks identical by return code: the stderr flag
        # plus a live daemon must route to the group path, never install.
        # (db_has_group=False here: with a real membership this box would
        # correctly report stale_login instead.)
        result = self._base(docker_info_rc=1, permission_denied=True,
                            daemon_active=True, group_names=["dak"],
                            db_has_group=False)
        self.assertEqual(result["state"], "no_access")
        self.assertIn("authorized", result["detail"])

    def test_denied_dead_daemon_starts(self):
        # Denied text but daemon actually down: starting is still correct.
        result = self._base(docker_info_rc=1, permission_denied=True,
                            daemon_active=False)
        self.assertEqual(result["state"], "daemon_down")

    def test_unverified(self):
        result = self._base(engine_version="")
        self.assertEqual(result["state"], "unverified")

    def test_old(self):
        result = self._base(engine_version="20.10.24")
        self.assertEqual(result["state"], "old_engine")
        self.assertIn("step 3", result["action"])

    def test_no_compose(self):
        result = self._base(compose_present=False)
        self.assertEqual(result["state"], "no_compose")

    def test_group(self):
        # Live lacks, DB lacks: installer must add, then fresh login.
        result = self._base(group_names=["dak", "sudo"], db_has_group=False)
        self.assertEqual(result["state"], "no_group")
        self.assertIn("checkpoint", result["action"])

    def test_stale_login(self):
        # Live lacks, DB HAS: the checker (not the login) is stale — restart
        # it, don't log out again. This is the exact post-relogin trap.
        result = self._base(group_names=["dak", "sudo"], db_has_group=True)
        self.assertEqual(result["state"], "stale_login")
        self.assertIn("./check.sh", result["action"])

    def test_denied_with_db_routes_to_restart(self):
        result = self._base(docker_info_rc=1, permission_denied=True,
                            daemon_active=True, group_names=["dak"],
                            db_has_group=True)
        self.assertEqual(result["state"], "stale_login")

    def test_networks(self):
        result = self._base(networks_present=["mu3lab_frontend"])
        self.assertEqual(result["state"], "no_networks")
        self.assertIn("mu3lab_backend", result["detail"])

    def test_never_blocks(self):
        # No docker shortfall may ever report "fail": all of it is step ③.
        cases = [self._base(binary_present=False),
                 self._base(docker_info_rc=1),
                 self._base(engine_version=""),
                 self._base(engine_version="20.10.0"),
                 self._base(compose_present=False),
                 self._base(group_names=[]),
                 self._base(networks_present=[])]
        for result in cases:
            self.assertNotEqual(result["status"], "fail")


class TailscaleTests(unittest.TestCase):
    def test_ok(self):
        result = preflight.check_tailscale(True, True, True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["state"], "ready")

    def test_absent(self):
        result = preflight.check_tailscale(False, False, False)
        self.assertEqual(result["state"], "absent")
        self.assertIn("not installed", result["detail"])

    def test_down(self):
        result = preflight.check_tailscale(True, False, False)
        self.assertEqual(result["state"], "daemon_down")
        self.assertIn("no reinstall", result["action"])

    def test_join(self):
        # Installed + running but unconnected: step ③ guides the join.
        result = preflight.check_tailscale(True, True, False)
        self.assertEqual(result["state"], "unjoined")


class PortTests(unittest.TestCase):
    def test_ok(self):
        result = preflight.check_ports(connect_fn=lambda port: False)
        self.assertEqual(result["status"], "ok")

    def test_busy(self):
        # Deterministic: stub ss so the box's real listeners can't flip it.
        from unittest.mock import patch as _patch
        with _patch("ctl.preflight._run", return_value=(1, "")):
            result = preflight.check_ports(connect_fn=lambda port: port == 8787)
        self.assertEqual(result["status"], "fail")
        self.assertIn("8787", result["detail"])

    def test_ours_is_info_not_block(self):
        # Our autostarted dashboard holding :8787 is EXPECTED post-install:
        # info row, never a blocker (this kills the stop-and-rerun loop).
        from unittest.mock import MagicMock, patch as _patch
        ss_out = ('State Recv-Q Local Address:Port Process\n'
                  'LISTEN 0 128 127.0.0.1:8787 '
                  'users:(("uvicorn",pid=4242,fd=13))')
        fake_path = MagicMock()
        fake_path.return_value.read_bytes.return_value = (
            b"/home/dak/Desktop/Mu3Lab/.venv/bin/python ctl.app:app")
        with _patch("ctl.preflight._run", return_value=(0, ss_out)), \
             _patch("ctl.preflight.Path", fake_path), \
             _patch("ctl.preflight.ROOT", Path("/home/dak/Desktop/Mu3Lab")):
            result = preflight.check_ports(
                connect_fn=lambda port: port == 8787)
        self.assertEqual(result["status"], "ok")
        self.assertIn("autostart", result["detail"])

    def test_busy_names_owner(self):
        # Busy ports carry an owners map so the UI can offer Stop for OURS.
        # /proc reads are stubbed: fixture PIDs must never depend on which
        # real processes happen to exist on the test box.
        from unittest.mock import MagicMock, patch as _patch
        ss_out = ('State Recv-Q Local Address:Port Process\n'
                  'LISTEN 0 128 127.0.0.1:8787 '
                  'users:(("uvicorn",pid=4242,fd=13))')
        fake_path = MagicMock()
        fake_path.return_value.read_bytes.return_value = (
            b"/home/dak/Desktop/Mu3Lab/.venv/bin/python ctl.app:app")
        with _patch("ctl.preflight._run", return_value=(0, ss_out)), \
             _patch("ctl.preflight.Path", fake_path):
            with _patch("ctl.preflight.ROOT", Path("/home/dak/Desktop/Mu3Lab")):
                result = preflight.check_ports(
                    connect_fn=lambda port: port == 8787)
        owner = result["owners"]["8787"]
        self.assertEqual(owner["pid"], 4242)
        self.assertTrue(owner["ours"])

    def test_foreign_owner_not_ours(self):
        from unittest.mock import MagicMock, patch as _patch
        ss_out = ('State Recv-Q Local Address:Port Process\n'
                  'LISTEN 0 128 127.0.0.1:8787 '
                  'users:(("something",pid=4243,fd=3))')
        fake_path = MagicMock()
        fake_path.return_value.read_bytes.side_effect = OSError("denied")
        with _patch("ctl.preflight._run", return_value=(0, ss_out)), \
             _patch("ctl.preflight.Path", fake_path):
            result = preflight.check_ports(
                connect_fn=lambda port: port == 8787)
        owner = result["owners"]["8787"]
        self.assertEqual(owner["pid"], 4243)
        self.assertFalse(owner["ours"])

    def test_ss_missing_still_reports(self):
        from unittest.mock import patch as _patch
        with _patch("ctl.preflight._run", return_value=(127, "no ss")):
            result = preflight.check_ports(connect_fn=lambda port: True)
        self.assertEqual(result["status"], "fail")
        for owner in result["owners"].values():
            self.assertIsNone(owner)

    def test_future_untouched(self):
        # 4000 (LiteLLM, later) must NOT be probed even when busy.
        seen: list[int] = []
        preflight.check_ports(connect_fn=lambda port: seen.append(port) or False)
        self.assertNotIn(4000, seen)
        self.assertEqual(set(seen), set(preflight.CHECK_PORTS))


class BundleTests(unittest.TestCase):
    def test_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = preflight.check_bundle(root=Path(tmp))
            self.assertEqual(result["status"], "missing")
            self.assertEqual(result["state"], "no_venv")

    def test_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "python").touch()
            dist = root / "dashboard" / "dist" / "assets"
            dist.mkdir(parents=True)
            (dist / "app.js").touch()
            (root / "dashboard" / "dist" / "index.html").write_text(
                '<script src="/assets/app.js"></script>', encoding="utf-8")
            self.assertEqual(preflight.check_bundle(root=root)["status"], "ok")

    def test_dangling_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "python").touch()
            (root / "dashboard" / "dist").mkdir(parents=True)
            (root / "dashboard" / "dist" / "index.html").write_text(
                '<script src="/assets/gone.js"></script>', encoding="utf-8")
            result = preflight.check_bundle(root=root)
            self.assertEqual(result["status"], "missing")
            self.assertEqual(result["state"], "no_build")
            self.assertIn("gone.js", result["detail"])


class AggregateTests(unittest.TestCase):
    def test_shape(self):
        # Live run on THIS box: shape asserted, verdict not (mid-build boxes
        # are TODO-heavy by definition).
        report = preflight.run_all()
        self.assertIn("install_ready", report)
        self.assertEqual(len(report["checks"]), 10)
        for check in report["checks"]:
            self.assertIn(check["status"], ("ok", "missing", "fail"))
            self.assertIn("blocking", check)
            self.assertIn("state", check)
            self.assertTrue(check["detail"])
        self.assertNotIn("4000", str(report))  # no future-port leakage

    def test_ready_with_todos(self):
        # Fresh-box shape: only install-provided items missing → ready.
        report = {"checks": [
            {"name": "os", "status": "ok", "blocking": True},
            {"name": "docker", "status": "missing", "blocking": False},
            {"name": "tailscale", "status": "missing", "blocking": False},
        ]}
        ready = not any(c["status"] == "fail" and c["blocking"]
                        for c in report["checks"])
        self.assertTrue(ready)

    def test_blocked_on_fail(self):
        report = {"checks": [
            {"name": "os", "status": "ok", "blocking": True},
            {"name": "ports", "status": "fail", "blocking": True},
            {"name": "docker", "status": "missing", "blocking": False},
        ]}
        ready = not any(c["status"] == "fail" and c["blocking"]
                        for c in report["checks"])
        self.assertFalse(ready)


if __name__ == "__main__":
    unittest.main()
