"""How often the worker checks app statuses."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ctl.jobs import JobStore
from ctl.runtime import RuntimePaths
from ctl.status import reconciler, signals


class PacingTests(unittest.TestCase):
    def test_idle_checks_every_five_minutes(self):
        self.assertTrue(reconciler.full_check_due(None, 0, 1000))
        self.assertFalse(reconciler.full_check_due(1000, 0, 1000 + 299))
        self.assertTrue(reconciler.full_check_due(1000, 0, 1000 + 300))

    def test_status_page_asks_at_most_once_a_minute(self):
        # A page asked after the last check: checked again once that check is a minute old.
        self.assertFalse(reconciler.full_check_due(1000, 1030, 1059))
        self.assertTrue(reconciler.full_check_due(1000, 1030, 1060))
        # Nobody asked since: the five-minute pace holds.
        self.assertFalse(reconciler.full_check_due(1000, 990, 1100))


class SignalTests(unittest.TestCase):
    def test_status_wanted_marks_the_time(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths(Path(directory))
            paths.state.mkdir(parents=True)
            self.assertEqual(signals.status_wanted_at(paths), 0)
            signals.status_wanted(paths)
            self.assertGreater(signals.status_wanted_at(paths), 0)


class BusyAppsTests(unittest.TestCase):
    def test_only_actions_that_change_running_apps_count(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths(Path(directory))
            paths.runtime.mkdir(parents=True, exist_ok=True)
            store = JobStore.runtime(paths)
            assert store is not None
            store.create(kind="lifecycle", service_id="immich", action="start", actor="test")
            store.create(kind="backup", service_id="mealie", action="backup", actor="test")
            self.assertEqual(reconciler.busy_apps(paths), {"immich"})


if __name__ == "__main__":
    unittest.main()
