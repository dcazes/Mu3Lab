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
    def test_response_headers_no_store(self):
        # A cached page against a newer server once produced a "mixed"
        # dashboard that blamed the user for a version skew. Every response
        # carries no-store; asserted here, not by clicking around.
        import check_server
        headers = check_server.response_headers("text/html", 10)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Content-Length"], "10")
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


class WorkerLivenessTests(unittest.TestCase):
    def test_worker_completes_without_wedging_lock(self):
        # REGRESSION: save_progress() once ran INSIDE `with state.lock`
        # while taking the same non-reentrant lock → the worker froze at
        # job end holding it, and EVERY endpoint hung forever ("step 1
        # stalls indefinitely"). This runs the real worker tail (buffering,
        # summary detection, save, lock release) against a 3-line FAKE
        # runner — never the real suite (a self-hosted full run fork-bombs:
        # every nested level spawns another level).
        import json as _json
        import os as _os
        import threading
        import check_server
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake_runner.py"
            fake.write_text(
                "import json\n"
                "print(json.dumps({'type': 'summary', 'phase': 'start', 'total': 1}))\n"
                "print(json.dumps({'type': 'test', 'id': 'x.Y.z', 'outcome': 'pass', 'detail': ''}))\n"
                "print(json.dumps({'type': 'summary', 'phase': 'done', 'ran': 1, 'ok': 1, 'failed': 0, 'errored': 0, 'skipped': 0}))\n",
                encoding="utf-8")
            env = dict(_os.environ, MU3LAB_TEST_RUNNER=str(fake))
            fake_file = Path(tmp) / "progress.json"
            state = State()
            state.test_run = {"status": "running"}
            with unittest.mock.patch.object(check_server, "STATE_FILE",
                                            fake_file), \
                 unittest.mock.patch.dict(_os.environ, {"MU3LAB_TEST_RUNNER": str(fake)}):
                thread = threading.Thread(
                    target=check_server._test_worker,
                    args=(state, sys.executable), daemon=True)
                thread.start()
                thread.join(timeout=30)
            self.assertFalse(thread.is_alive(),
                             "worker hung — suspected lock self-deadlock")
        acquired = state.lock.acquire(timeout=5)
        try:
            self.assertTrue(acquired, "state.lock still held after worker end")
        finally:
            if acquired:
                state.lock.release()
        self.assertTrue(state.tests_green)
        self.assertIsNone(state.test_run)


if __name__ == "__main__":
    unittest.main()
