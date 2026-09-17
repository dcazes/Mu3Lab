"""Control-plane job records are durable and never retain simple secret assignments."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl.jobs import JobStore, redact


class JobStoreTests(unittest.TestCase):
    def test_redaction_removes_common_secret_assignments(self):
        value = redact("API_KEY=abc123 password: hunter2 plain text")
        self.assertNotIn("abc123", value)
        self.assertNotIn("hunter2", value)
        self.assertIn("[redacted]", value)

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
