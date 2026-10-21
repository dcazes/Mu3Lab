"""Mu3Lab's own history is trimmed, never what a page or an unfinished job still needs (review R28)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ctl import history
from ctl.jobs import JobStore
from ctl.store import db

NOW = datetime.now(UTC)


def _ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


class PruneTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database = Path(temp.name) / "mu3lab.db"
        self.store = JobStore(self.database)

    def job(self, action: str, state: str, days: int, service: str = "mealie") -> str:
        job = self.store.create(kind="lifecycle", service_id=service, action=action, actor="owner")
        self.store.append_event(job["id"], "log", f"{action} line")
        with db.connect(self.database) as connection:
            connection.execute(
                "UPDATE jobs SET state = ?, created_at = ?, updated_at = ? WHERE id = ?",
                (state, _ago(days), _ago(days), job["id"]),
            )
            connection.execute("UPDATE job_events SET created_at = ? WHERE job_id = ?", (_ago(days), job["id"]))
            connection.execute("UPDATE audit SET created_at = ? WHERE job_id = ?", (_ago(days), job["id"]))
        return job["id"]

    def logs(self, job_id: str) -> int:
        return len(self.store.events(job_id))

    def test_old_logs_go_but_each_apps_newest_and_unfinished_jobs_keep_theirs(self):
        old_update = self.job("update", "succeeded", 200)
        newest_update = self.job("update", "failed", 150)  # still what the app's page shows
        stuck = self.job("restore", "running", 200, service="paperless-ngx")  # a running job blocks its app
        recent = self.job("start", "succeeded", 10)
        older_start = self.job("start", "succeeded", 120)
        removed = history.prune(self.database, NOW)
        self.assertEqual(self.logs(old_update), 0)
        self.assertEqual(self.logs(older_start), 0)
        for kept in (newest_update, stuck, recent):
            self.assertGreater(self.logs(kept), 0, kept)
        self.assertEqual(removed["job_logs"], 2)
        # Job records themselves stay.
        self.assertIsNotNone(self.store.get(old_update))

    def test_audit_is_kept_a_year_and_never_for_an_unfinished_job(self):
        finished = self.job("update", "succeeded", 400)
        waiting = self.job("restore", "running", 400, service="paperless-ngx")
        history.prune(self.database, NOW)
        with db.connect(self.database) as connection:
            left = {row[0] for row in connection.execute("SELECT DISTINCT job_id FROM audit")}
        self.assertNotIn(finished, left)
        self.assertIn(waiting, left)


if __name__ == "__main__":
    unittest.main()
