"""Entry-script invariants: one safe install command that anyone can re-run."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EntryScriptTests(unittest.TestCase):
    def test_scripts_exist_and_parse(self):
        for name in ("install.sh", "start.sh", "tools/vm/fresh-vm.sh", "tools/open_tailscale_login.sh"):
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

    def test_install_hands_off_to_the_bootstrap_server(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("-m ctl.bootstrap.server", text)

    def test_help_exits_without_side_effects(self):
        result = subprocess.run([str(ROOT / "install.sh"), "--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn("safe to run again", result.stdout)


if __name__ == "__main__":
    unittest.main()
