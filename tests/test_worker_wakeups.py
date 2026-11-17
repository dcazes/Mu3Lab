"""The worker sleeps until there is something to do, and its upkeep asks other apps only when needed."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import people, vault_sync
from ctl.contextforge import ContextForge, ContextForgeError
from ctl.runtime import RuntimePaths
from ctl.status import reconciler, signals
from ctl.store import records


class NudgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.tmp.name))
        self.paths.state.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_nudge_from_another_process_wakes_every_sleeping_loop(self):
        jobs, downloads = signals.waiter(), signals.waiter()
        self.assertIsNotNone(signals.listen(self.paths))
        signals.nudge(self.paths)
        self.assertTrue(jobs.wait(5))
        self.assertTrue(downloads.wait(5))

    def test_a_nudge_with_no_worker_listening_does_nothing(self):
        signals.nudge(self.paths)  # no socket: nothing raised, nothing waits

    def test_queueing_a_job_nudges_the_worker(self):
        from ctl.jobs import JobStore
        from ctl.store import db

        store = JobStore(db.database(self.paths))
        with patch.object(signals, "nudge") as nudge:
            store.create(kind="lifecycle", service_id="photos", action="start", actor="test")
        nudge.assert_called_once()


class StatusPacingTests(unittest.TestCase):
    def test_an_app_being_changed_is_looked_at_every_few_seconds(self):
        self.assertEqual(reconciler.next_look(100.0, 98.0, True, 100.0), 3.0)

    def test_with_nothing_to_watch_it_sleeps_until_the_next_full_check_or_a_minute(self):
        with patch.object(signals, "status_wanted_at", return_value=0.0):
            self.assertEqual(reconciler.next_look(0.0, 0.0, False, 280.0), 20.0)
            self.assertEqual(reconciler.next_look(0.0, 0.0, False, 10.0), reconciler.IDLE_WAIT)

    def test_an_opened_status_page_brings_the_next_full_check_forward(self):
        with patch.object(signals, "status_wanted_at", return_value=50.0):
            self.assertEqual(reconciler.next_look(0.0, 0.0, False, 45.0), 15.0)


class HouseholdUpkeepTests(unittest.TestCase):
    def setUp(self):
        people._upkeep.update(at=0.0, people=None)
        self.addCleanup(people._upkeep.update, at=0.0, people=None)
        self.reads = 0

    def _list(self):
        self.reads += 1
        return [{"uid": "a", "email": "a@example.com"}]

    def test_one_reading_serves_upkeep_until_the_household_changes(self):
        with (
            patch.object(people, "list_people", side_effect=self._list),
            patch.object(signals, "every", return_value=3600),
            patch.object(signals, "household_changed_at", return_value=0.0) as changed,
        ):
            people.people_for_upkeep()
            people.people_for_upkeep()
            self.assertEqual(self.reads, 1)
            changed.return_value = time.time() + 1
            people.people_for_upkeep()
            self.assertEqual(self.reads, 2)

    def test_an_old_reading_is_read_again(self):
        with (
            patch.object(people, "list_people", side_effect=self._list),
            patch.object(signals, "every", return_value=300),
            patch.object(signals, "household_changed_at", return_value=0.0),
        ):
            people.people_for_upkeep()
            people._upkeep["at"] -= 301
            people.people_for_upkeep()
        self.assertEqual(self.reads, 2)


class VaultPacingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.tmp.name))
        os.makedirs(self.paths.state)

    def tearDown(self):
        self.tmp.cleanup()

    def _last(self, ok: bool, *states: str) -> None:
        records.put("vault", "organization", {"ok": ok, "people": [{"state": state} for state in states]}, self.paths)

    def test_saving_keeps_its_pace_only_while_someones_logins_wait(self):
        self.assertTrue(vault_sync.someone_waiting(self.paths))  # never saved yet
        self._last(True, "up_to_date", "skipped")
        self.assertFalse(vault_sync.someone_waiting(self.paths))
        self._last(True, "up_to_date", "waiting_for_account")
        self.assertTrue(vault_sync.someone_waiting(self.paths))
        self._last(False, "up_to_date")
        self.assertTrue(vault_sync.someone_waiting(self.paths))


class ConnectorToolsTests(unittest.TestCase):
    def test_restarting_a_connector_asks_contextforge_for_its_tools(self):
        api = ContextForge("http://contextforge.invalid", "admin@example.com", "x")
        sent: list[tuple[str, str]] = []

        def request(method, path, **_kwargs):
            sent.append((method, path))
            if path.endswith("/tools/refresh"):
                raise ContextForgeError("not now")
            return {"id": "g1"}

        with (
            patch.object(api, "gateways", return_value=[{"id": "g1", "name": "photos"}]),
            patch.object(api, "request", side_effect=request),
        ):
            api.ensure_gateway("photos", "http://photos-mcp:8080/mcp", "token", "Photos")
        self.assertEqual(
            sent,
            [
                ("PUT", "/v1/gateways/g1"),
                ("POST", "/v1/gateways/g1/state"),
                ("POST", "/v1/gateways/g1/tools/refresh"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
