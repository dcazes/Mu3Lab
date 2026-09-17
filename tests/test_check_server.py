"""Mu3Lab :: tests/test_check_server.py

WHAT: Tests for check_server.py WITHOUT binding ports or spawning anything:
      the pure card-gating predicates (can_run_preflight, can_open_install).
WHY:  Card order (tests → preflight → install) is locked. If the gates drift,
      users run preflight on a red suite and get nonsense. Names stay short.
RUN:  `.venv/bin/python -m unittest tests.test_check_server -v`.
DEBUG: State() is in-memory; construct one per test, flip flags directly.
"""

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_server import (State, can_open_install, can_run_preflight,
                          load_progress, save_progress)


class GateTests(unittest.TestCase):
    def test_locked(self):
        ok, reason = can_run_preflight(State())
        self.assertFalse(ok)
        self.assertIn("card", reason)

    def test_locked_running(self):
        state = State()
        state.tests_green = True
        state.test_run = {"status": "running"}
        ok, _ = can_run_preflight(state)
        self.assertFalse(ok)

    def test_open(self):
        state = State()
        state.tests_green = True
        ok, _ = can_run_preflight(state)
        self.assertTrue(ok)

    def test_install_locked(self):
        state = State()
        state.tests_green = True
        ok, reason = can_open_install(state)
        self.assertFalse(ok)
        self.assertIn("card", reason)

    def test_install_open(self):
        state = State()
        state.preflight_passed = True
        ok, _ = can_open_install(state)
        self.assertTrue(ok)

    def test_reset(self):
        # Mirrors the server: a new test run clears downstream flags, so the
        # chain re-locks instead of coasting on stale green.
        state = State()
        state.tests_green = True
        state.preflight_passed = True
        state.tests_green = False  # what POST /api/tests/run does first
        ok, _ = can_run_preflight(state)
        self.assertFalse(ok)


class ProgressTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "progress.json"
            src = State()
            src.tests_green = True
            src.tests_summary = {"ran": 1}
            src.preflight_passed = True
            src.preflight_report = {"ok": True}
            save_progress(src, path)
            dst = State()
            self.assertEqual(load_progress(dst, path), "restored")
            self.assertTrue(dst.tests_green)
            self.assertTrue(dst.preflight_passed)
            self.assertEqual(dst.preflight_report, {"ok": True})

    def test_missing_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_progress(State(), Path(tmp) / "nope.json"),
                             "none")

    def test_version_mismatch_is_stale(self):
        import check_server
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "progress.json"
            save_progress(State(), path)
            with unittest.mock.patch.object(check_server, "CODE_VERSION",
                                            9999):
                # State reads the patched constant at construction.
                self.assertEqual(load_progress(State(), path), "stale")

    def test_never_persists_secrets(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "progress.json"
            src = State()
            src.tests_green = True
            save_progress(src, path)
            text = path.read_text(encoding="utf-8")
            for needle in ("authkey", "password", "token", "SECRET"):
                self.assertNotIn(needle, text)
            # …and events/logs (potentially huge) are excluded by shape.
            self.assertNotIn("events", json.loads(text))


if __name__ == "__main__":
    unittest.main()
