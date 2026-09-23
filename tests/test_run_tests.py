"""Mu3Lab :: tests/test_run_tests.py

WHAT: Tests for tools/run_tests.py WITHOUT spawning subprocesses: the wire
      format parser (parse_line) plus the JsonResult outcome mapping fed with
      synthetic results.
WHY:  The check dashboard depends on exact JSON shapes. If the runner's
      output drifts, card 1 breaks silently. These tests pin the contract.
      Subprocess-based tests are intentionally avoided here: check.sh's whole
      point is zero-assumption environments, and unit tests must not need to
      launch anything.
RUN:  `.venv/bin/python -m unittest tests.test_run_tests -v`.
DEBUG: parse_line raises ValueError with a message naming the bad field.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import run_tests


class ParseLineTests(unittest.TestCase):
    def test_ok(self):
        line = json.dumps({"type": "test", "id": "a.B.c", "outcome": "pass",
                           "detail": ""})
        self.assertEqual(run_tests.parse_line(line)["outcome"], "pass")

    def test_summary(self):
        line = json.dumps({"type": "summary", "ran": 3, "ok": 3, "failed": 0,
                           "errored": 0, "skipped": 0})
        self.assertEqual(run_tests.parse_line(line)["ran"], 3)

    def test_bad_outcome(self):
        line = json.dumps({"type": "test", "id": "a", "outcome": "maybe"})
        with self.assertRaises(ValueError):
            run_tests.parse_line(line)

    def test_no_id(self):
        line = json.dumps({"type": "test", "outcome": "pass"})
        with self.assertRaises(ValueError):
            run_tests.parse_line(line)

    def test_garbage(self):
        with self.assertRaises(ValueError):
            run_tests.parse_line("not json at all{")

    def test_bootstrap_defers_dependency_backed_registry_module(self):
        self.assertEqual(run_tests.DEFERRED_BOOTSTRAP_MODULES,
                         ("tests.test_app_security", "tests.test_calendar",
                          "tests.test_connections_mvp", "tests.test_identity",
                          "tests.test_mcp_registry", "tests.test_mvp_completion",
                          "tests.test_mvp_control", "tests.test_mvp_runtime",
                          "tests.test_provisioning", "tests.test_provider_ops",
                          "tests.test_registry", "tests.test_service_ops"))


class JsonResultTests(unittest.TestCase):
    def _run_one(self, test):
        """Run a single TestCase through JsonResult, capturing stdout lines."""
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        result = run_tests.JsonResult()
        suite = unittest.TestSuite([test])
        with redirect_stdout(buffer):
            suite.run(result)
        return [json.loads(line) for line in buffer.getvalue().splitlines()]

    def test_pass(self):
        class T(unittest.TestCase):
            def runTest(self):
                pass
        (line,) = self._run_one(T())
        self.assertEqual(line["outcome"], "pass")
        self.assertTrue(line["id"].endswith("runTest"))

    def test_fail(self):
        class T(unittest.TestCase):
            def runTest(self):
                self.assertTrue(False, "boom")
        (line,) = self._run_one(T())
        self.assertEqual(line["outcome"], "fail")
        self.assertIn("boom", line["detail"])

    def test_skipped(self):
        # NOTE: must be a real subclass with a runTest method — assigning
        # runTest onto a bare TestCase() instance breaks unittest's suite
        # machinery (_tearDownPreviousClass needs class-level attributes).
        class SkippedCase(unittest.TestCase):
            def runTest(self):  # noqa: N802 (unittest hook name, keep it)
                raise unittest.SkipTest("not today")
        (line,) = self._run_one(SkippedCase())
        self.assertEqual(line["outcome"], "skipped")
        self.assertIn("not today", line["detail"])


if __name__ == "__main__":
    unittest.main()
