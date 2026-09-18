"""Control-plane job records are durable and never retain simple secret assignments."""

from __future__ import annotations

import sys
import tempfile
import unittest
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl.jobs import JobStore, redact


class JobStoreTests(unittest.TestCase):
    def test_redaction_removes_common_secret_assignments(self):
        value = redact("API_KEY=abc123 password: hunter2 person@example.com /home/person/private.log host.ts.net plain text")
        self.assertNotIn("abc123", value)
        self.assertNotIn("hunter2", value)
        self.assertIn("[redacted]", value)
        self.assertNotIn("person@example.com", value)
        self.assertNotIn("/home/person", value)
        self.assertNotIn("host.ts.net", value)

    def test_job_and_audit_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "runtime" / "control-plane.sqlite3")
            job = store.create(kind="backup", service_id="vaultwarden", action="snapshot",
                               actor="operator", detail="token=not-for-storage")
            store.transition(job["id"], "waiting_for_confirmation", actor="operator",
                             detail="restore password: also-not-for-storage")
            jobs = store.jobs()
            events = store.audit()
        self.assertEqual(jobs[0]["state"], "waiting_for_confirmation")
        self.assertNotIn("not-for-storage", jobs[0]["detail"])
        self.assertEqual(len(events), 2)

    def test_invalid_job_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "db.sqlite3")
            with self.assertRaises(ValueError):
                store.create(kind="shell", service_id="x", action="rm", actor="operator")

    def test_idempotency_key_returns_the_original_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "db.sqlite3")
            first = store.create(kind="lifecycle", service_id="core-suite", action="install",
                                 actor="operator", idempotency_key="same-request")
            second = store.create(kind="lifecycle", service_id="core-suite", action="install",
                                  actor="operator", idempotency_key="same-request")
        self.assertEqual(first["id"], second["id"])

    def test_worker_reclaims_an_expired_lease_and_records_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "db.sqlite3"
            store = JobStore(database)
            job = store.create(kind="lifecycle", service_id="core-suite", action="install",
                               actor="operator")
            claimed = store.claim("worker-one")
            self.assertEqual(claimed["id"], job["id"])
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                             (job["id"],))
            reclaimed = store.claim("worker-two")
            self.assertEqual(reclaimed["lease_owner"], "worker-two")
            store.append_event(job["id"], "step.started", "password=never-store-this")
            self.assertNotIn("never-store-this", store.events(job["id"])[0]["detail"])

    def test_terminal_jobs_cannot_return_to_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "db.sqlite3")
            job = store.create(kind="backup", service_id="vaultwarden", action="snapshot",
                               actor="operator")
            store.transition(job["id"], "failed", actor="worker", detail="safe failure")
            with self.assertRaises(ValueError):
                store.transition(job["id"], "running", actor="worker")

    def test_only_one_active_operation_exists_per_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "db.sqlite3")
            first = store.create(kind="lifecycle", service_id="core-suite", action="install",
                                 actor="operator")
            second = store.create(kind="lifecycle", service_id="core-suite", action="install",
                                  actor="operator")
        self.assertEqual(first["id"], second["id"])

    def test_failed_job_can_be_retried_as_a_new_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "db.sqlite3")
            first = store.create(kind="lifecycle", service_id="core-suite", action="install",
                                 actor="operator")
            store.transition(first["id"], "failed", actor="worker")
            retried = store.retry(first["id"], actor="operator", idempotency_key="retry-one")
        self.assertNotEqual(first["id"], retried["id"])
        self.assertEqual(retried["state"], "queued")
