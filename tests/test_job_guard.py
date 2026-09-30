"""A cancelled job, or one whose worker lost its lease, stops making changes."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import job_guard
from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.worker import run_job


class CancellationTests(unittest.TestCase):
    def _restart(self, tmp: str, during_compose):
        store = JobStore(Path(tmp) / "jobs.sqlite3")
        state = ControlState(Path(tmp) / "control.sqlite3")
        state.set_installation("ollama", "stopped")
        created = store.create(kind="lifecycle", service_id="ollama", action="restart", actor="owner")
        claimed = store.claim("worker")
        assert claimed is not None

        def compose(*_args, **_kwargs):
            during_compose(store, str(created["id"]))
            return 0, "restarted"

        with (
            patch("ctl.service_ops.ControlState.runtime", return_value=state),
            patch("ctl.service_ops.actions.compose_action", side_effect=compose),
            patch("ctl.service_ops.wait_healthy", return_value=(True, "HTTP 200")),
            patch("ctl.mcp_ops.sync_application") as sync,
        ):
            run_job(store, claimed, "worker")
        return store.get(str(created["id"])), state.installation("ollama"), sync

    def test_cancelling_mid_command_stops_every_later_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, installation, sync = self._restart(tmp, lambda store, job_id: store.cancel(job_id, actor="owner"))
        self.assertEqual(job["state"], "cancelled")
        sync.assert_not_called()
        self.assertEqual(installation["state"], "stopped")

    def test_a_worker_that_lost_its_lease_changes_nothing_more(self):
        def reclaimed(store, job_id):
            with sqlite3.connect(store.database) as conn:
                conn.execute("UPDATE jobs SET lease_owner = 'other-worker' WHERE id = ?", (job_id,))

        with tempfile.TemporaryDirectory() as tmp:
            job, installation, sync = self._restart(tmp, reclaimed)
        # The other worker owns it now; this one neither finished nor failed it.
        self.assertEqual((job["state"], job["lease_owner"]), ("running", "other-worker"))
        sync.assert_not_called()
        self.assertEqual(installation["state"], "stopped")

    def test_a_queued_job_cancels_at_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            job = store.create(kind="lifecycle", service_id="ollama", action="start", actor="owner")
            self.assertEqual(store.cancel(str(job["id"]), actor="owner"), "cancelled")

    def test_a_running_job_is_cancelling_until_its_worker_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            job = store.create(kind="lifecycle", service_id="ollama", action="start", actor="owner")
            store.claim("worker")
            self.assertEqual(store.cancel(str(job["id"]), actor="owner"), "cancelling")
            self.assertEqual(store.get(str(job["id"]))["state"], "running")
            self.assertEqual(store.interruption(str(job["id"]), "worker"), job_guard.CANCELLED)

    def test_a_cancelled_job_whose_worker_died_is_not_started_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            job = store.create(kind="lifecycle", service_id="ollama", action="start", actor="owner")
            store.claim("dead-worker")
            store.cancel(str(job["id"]), actor="owner")
            with sqlite3.connect(store.database) as conn:
                conn.execute("UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00'")
            self.assertIsNone(store.claim("new-worker"))
            self.assertEqual(store.get(str(job["id"]))["state"], "cancelled")

    def test_ordinary_error_handlers_cannot_swallow_an_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            job = store.create(kind="lifecycle", service_id="ollama", action="start", actor="owner")
            store.claim("worker")
            store.cancel(str(job["id"]), actor="owner")
            with job_guard.executing(store, str(job["id"]), "worker"), self.assertRaises(job_guard.JobInterrupted):
                try:
                    store.append_event(str(job["id"]), "log", "next step")
                except Exception:
                    self.fail("JobInterrupted must not be an Exception")


if __name__ == "__main__":
    unittest.main()
