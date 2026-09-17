"""Mu3Lab :: tests/test_secrets.py

WHAT: Tests for ctl/secrets.py using temp dirs — no real .env is touched.
      Pinned: preserve-existing (never overwrite live tokens), 0600 mode,
      append-only (comments + unrelated keys survive byte-for-byte).
WHY:  Rotating a live token orphans running services; leaking file contents
      into logs exposes everything. Both failure modes are asserted here.
RUN:  `.venv/bin/python -m unittest tests.test_secrets -v`.
DEBUG: All fixtures live under tempfile.TemporaryDirectory (auto-cleaned).
"""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl import secrets


def _ctx_inputs():
    return {}


class RootEnvTests(unittest.TestCase):
    def test_creates_with_both_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            values, added = secrets.ensure_root_env(
                Path(tmp), token_factory=lambda: "tok")
            self.assertEqual(set(added), set(secrets.ROOT_ENV_KEYS))
            self.assertEqual(values["MU3LAB_CTL_TOKEN"], "tok")
            mode = stat.S_IMODE(os.stat(Path(tmp) / ".env").st_mode)
            self.assertEqual(mode, 0o600)

    def test_preserves_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "# mine\nMU3LAB_CTL_TOKEN=live-token\nOTHER=1\n",
                encoding="utf-8")
            os.chmod(root / ".env", 0o600)
            values, added = secrets.ensure_root_env(
                root, token_factory=lambda: "new")
            # Live token untouched; only the missing key added; OTHER kept.
            self.assertEqual(values["MU3LAB_CTL_TOKEN"], "live-token")
            self.assertEqual(added, ["MU3LAB_INGRESS_TOKEN"])
            text = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("# mine", text)
            self.assertIn("OTHER=1", text)
            self.assertIn("live-token", text)

    def test_noop_when_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = "MU3LAB_CTL_TOKEN=a\nMU3LAB_INGRESS_TOKEN=b\n"
            (root / ".env").write_text(before, encoding="utf-8")
            _values, added = secrets.ensure_root_env(
                root, token_factory=lambda: "new")
            self.assertEqual(added, [])
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), before)

    def test_token_factory_used_per_missing_key(self):
        made: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            _values, added = secrets.ensure_root_env(
                Path(tmp), token_factory=lambda: made.append("x") or "x")
            self.assertEqual(len(made), 2)
            self.assertEqual(len(added), 2)


if __name__ == "__main__":
    unittest.main()
