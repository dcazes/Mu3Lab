"""Mu3Lab :: tools/run_tests.py

WHAT: Discovers tests/ and runs them, printing ONE JSON object per line:
      {"type": "test", "id": "...", "outcome": "pass|fail|error|skipped",
       "detail": "..."} plus a final {"type": "summary", ...}.
WHY:  The check dashboard streams progress (card 1). Parsing `unittest -v`
      text with regex is brittle across versions; a custom TestResult is
      exact. The bootstrap profile is stdlib-only; dependency-backed modules
      are explicitly deferred until card ③ installs control-plane packages.
RUN:  `python3 tools/run_tests.py` from the repo root. Exit 0 iff all green.
      (check_server.py spawns exactly this command; see its fixed argv.)
DEBUG: Each line is self-contained JSON (json.loads per line). `detail` holds
      the traceback (fail/error) or skip reason, else "".
"""

from __future__ import annotations

import io
import json
import os
import sys
import traceback
import unittest
from pathlib import Path

# Repo root = parent of tools/. Suite imports work because tests/ inserts it
# into sys.path itself (see tests/test_preflight.py); belt-and-braces here too.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEFERRED_BOOTSTRAP_MODULES = ("tests.test_registry",)


class JsonResult(unittest.TestResult):
    """A TestResult that emits one JSON line per finished test to stdout.

    Outcome mapping (unittest has no single status field, so we derive it):
    - test listed in skipped      -> "skipped" (reason in detail)
    - test in failures            -> "fail"    (assertion text in detail)
    - test in errors              -> "error"   (traceback in detail)
    - otherwise                   -> "pass"
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current: unittest.TestCase | None = None

    def startTest(self, test):  # noqa: N802 (unittest hook name, keep it)
        self._current = test
        super().startTest(test)

    def stopTest(self, test):  # noqa: N802 (unittest hook name, keep it)
        super().stopTest(test)
        test_id = test.id()
        outcome, detail = "pass", ""
        # NOTE: errors/failures/skipped are lists of (test, formatted) tuples.
        for skipped_test, reason in self.skipped:
            if skipped_test is test:
                outcome, detail = "skipped", reason
                break
        for failed_test, formatted in self.failures:
            if failed_test is test:
                outcome, detail = "fail", formatted
                break
        for errored_test, formatted in self.errors:
            if errored_test is test:
                outcome, detail = "error", formatted
                break
        print(json.dumps({"type": "test", "id": test_id,
                          "outcome": outcome, "detail": detail}),
              flush=True)


def parse_line(line: str) -> dict:
    """Parse one runner output line. Raises ValueError on malformed input.

    Factored (not inlined in main) so tests/test_run_tests.py can assert the
    wire format without spawning a subprocess.
    """
    obj = json.loads(line)
    if obj.get("type") == "test":
        if obj.get("outcome") not in ("pass", "fail", "error", "skipped"):
            raise ValueError(f"unknown outcome: {obj.get('outcome')!r}")
        if "id" not in obj:
            raise ValueError("test line missing 'id'")
    elif obj.get("type") == "summary":
        for key in ("ran", "ok", "failed", "errored", "skipped"):
            if key not in obj:
                raise ValueError(f"summary line missing {key!r}")
    else:
        raise ValueError(f"unknown line type: {obj.get('type')!r}")
    return obj


def main() -> int:
    """Discover tests/, run with JsonResult, print summary. Returns exit code."""
    loader = unittest.TestLoader()
    bootstrap_mode = os.environ.get("MU3LAB_BOOTSTRAP_TESTS") == "1"
    if bootstrap_mode:
        # A fresh checkout cannot import PyYAML yet. Load modules separately
        # so the registry module is deferred instead of becoming a FailedTest.
        modules = sorted(path.stem for path in (ROOT / "tests").glob("test_*.py")
                         if f"tests.{path.stem}" not in DEFERRED_BOOTSTRAP_MODULES)
        suite = unittest.TestSuite(
            loader.loadTestsFromName(f"tests.{module}") for module in modules
        )
    else:
        # start_dir tests/, top_level_dir root: test ids look like
        # "test_preflight.OsTests.test_ubuntu_2204_accepted".
        suite = loader.discover(start_dir=str(ROOT / "tests"),
                                top_level_dir=str(ROOT))
    total = suite.countTestCases()
    print(json.dumps({"type": "summary", "phase": "start",
                      "total": total,
                      "deferred": list(DEFERRED_BOOTSTRAP_MODULES) if bootstrap_mode else []}),
          flush=True)
    # Silence per-test stderr noise (dots/tracebacks unittest prints by
    # default); our JSON lines are the only output. Tracebacks survive inside
    # the `detail` field of fail/error lines.
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=0,
                                     resultclass=JsonResult)
    result = runner.run(suite)
    summary = {"type": "summary", "phase": "done", "ran": result.testsRun,
               "ok": result.testsRun - len(result.failures) - len(result.errors),
               "failed": len(result.failures), "errored": len(result.errors),
               "skipped": len(result.skipped),
               "deferred": list(DEFERRED_BOOTSTRAP_MODULES) if bootstrap_mode else []}
    print(json.dumps(summary), flush=True)
    # Exit 0 only when everything ran and nothing failed/errored. Skips are
    # tolerated (they are explicit, not breakage).
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # last-resort: a crashed runner must still emit JSON
        print(json.dumps({"type": "summary", "phase": "crashed",
                          "detail": traceback.format_exc()}), flush=True)
        raise SystemExit(2)
