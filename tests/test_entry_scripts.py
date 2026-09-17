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
