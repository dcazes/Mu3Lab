"""Entry-script invariants: one safe bootstrap command and no missing Make targets."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class EntryScriptTests(unittest.TestCase):
    def test_scripts_exist_and_parse(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("install.sh", "start.sh", "check.sh"):
            with self.subTest(name=name):
                path = root / name
                self.assertTrue(path.is_file())
                self.assertEqual(subprocess.run(["bash", "-n", str(path)]).returncode, 0)

    def test_install_delegates_to_the_gated_bootstrapper(self):
        text = (Path(__file__).resolve().parents[1] / "install.sh").read_text(encoding="utf-8")
        self.assertIn('exec "$ROOT_DIR/check.sh"', text)

    def test_tailscale_helper_is_shell_valid_and_documented(self):
        root = Path(__file__).resolve().parents[1]
        helper = root / "tools" / "open_tailscale_login.sh"
        self.assertTrue(helper.is_file())
        result = subprocess.run(["bash", "-n", str(helper)], capture_output=True,
                                text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("xdg-open", helper.read_text(encoding="utf-8"))

    def test_ready_preflight_keeps_install_action_available(self):
        page = (Path(__file__).resolve().parents[1] / "tools" / "check_page.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Host prerequisites are ready.", page)
        self.assertIn("btn-inst').disabled = false", page)
        self.assertNotIn("Everything above is already installed — nothing to do.", page)
