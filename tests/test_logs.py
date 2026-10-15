"""Long-running services log with levels, and routine polling does not bury the rest (reviews A-L7, B-L6)."""

from __future__ import annotations

import io
import logging
import unittest
from unittest.mock import patch

from ctl import logs


def _access(path: str, status: int) -> logging.LogRecord:
    """A record shaped like uvicorn's access log."""
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5", "GET", path, "1.1", status),
        None,
    )


class LogTests(unittest.TestCase):
    def tearDown(self):
        logging.getLogger().handlers = []

    def test_successful_dashboard_polling_is_left_out_but_failures_are_kept(self):
        quiet = logs.QuietPolling()
        self.assertFalse(quiet.filter(_access("/api/v1/snapshot", 200)))
        self.assertFalse(quiet.filter(_access("/api/health", 200)))
        self.assertTrue(quiet.filter(_access("/api/v1/snapshot", 503)))
        self.assertTrue(quiet.filter(_access("/api/v1/services/mealie/actions", 200)))

    def write(self, *, journal: bool) -> str:
        stream = io.StringIO()
        with patch.dict("os.environ", {"JOURNAL_STREAM": "8:1"} if journal else {}, clear=True):
            logs.configure()
        logging.getLogger().handlers[0].setStream(stream)
        logging.getLogger("mu3lab.worker").warning("Mu3Lab vault saving deferred safely: locked")
        logging.getLogger("mu3lab.worker").info("ready")
        return stream.getvalue()

    def test_under_systemd_each_line_carries_its_level_for_journald(self):
        self.assertEqual(
            self.write(journal=True).splitlines(),
            ["<4>mu3lab.worker: Mu3Lab vault saving deferred safely: locked", "<6>mu3lab.worker: ready"],
        )

    def test_run_by_hand_lines_are_plain_with_the_time_and_level(self):
        first = self.write(journal=False).splitlines()[0]
        self.assertRegex(first, r"^\d{4}-\d\d-\d\d .* WARNING mu3lab\.worker: Mu3Lab vault saving deferred")


if __name__ == "__main__":
    unittest.main()
