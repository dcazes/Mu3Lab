"""Entry-script invariants: one safe install command that anyone can re-run."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EntryScriptTests(unittest.TestCase):
    def test_scripts_exist_and_parse(self):
        for name in ("install.sh", "uninstall.sh", "start.sh", "tools/vm/fresh-vm.sh", "tools/open_tailscale_login.sh"):
            with self.subTest(name=name):
                path = ROOT / name
                self.assertTrue(path.is_file())
                result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_install_refuses_root_and_asks_for_sudo_once(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('[[ "$(id -u)" -ne 0 ]]', text)
        self.assertEqual(text.count("sudo -v"), 1)
        self.assertIn("do sudo -n true", text)  # keep-alive never prompts

    def test_install_uses_the_same_requirements_stamp_as_the_engine(self):
        from ctl.bootstrap import stamps

        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn(str(stamps.REQUIREMENTS_STAMP), text)

    def test_install_runs_the_terminal_installer(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("-m ctl.bootstrap.terminal", text)

    def test_uninstall_dry_run_changes_nothing_and_never_touches_base_packages(self):
        result = subprocess.run(
            [str(ROOT / "uninstall.sh"), "--dry-run", "--everything"], capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing was changed", result.stdout)
        text = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
        for protected in ("python3", "autoremove", "nvidia-driver", " curl", " git"):
            self.assertNotIn(f"purge {protected}", text)
        self.assertNotIn("apt-get autoremove", text)

    def test_help_exits_without_side_effects(self):
        result = subprocess.run([str(ROOT / "install.sh"), "--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn("safe to run again", result.stdout)


if __name__ == "__main__":
    unittest.main()
