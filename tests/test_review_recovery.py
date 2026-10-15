"""Regression evidence for the stacked milestone merge review; disposable files only."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ctl import platform_backup, rebuild
from ctl.jobs import JobStore
from ctl.runtime import RuntimePaths
from ctl.store import db
from ctl.store.secrets import SecretStore
from gateway_authority import Authority


class RecoveryReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="mu3lab-review-"))
        self.paths = RuntimePaths(Path(self.temporary))
        self.paths.state.mkdir()
        self.paths.data.mkdir()
        self.jobs = JobStore(self.paths.state / "mu3lab.db")

    def test_new_host_retires_old_jobs_and_batches(self):
        db.connect(self.jobs.database).close()  # every migration, batches included
        with sqlite3.connect(self.jobs.database) as conn:
            conn.execute(
                "INSERT INTO install_batches (id,actor,owner_uid,state,created_at,updated_at) VALUES ('batch','owner','owner','running','','')"
            )
            conn.execute(
                "INSERT INTO install_batch_items (batch_id,service_id,ordinal,explicitly_selected,state,download_state) VALUES ('batch','mealie',0,1,'pending','ready')"
            )
        rebuild.prepare_database(self.jobs.database)
        self.assertEqual(self.jobs.active_jobs(), [])
        with sqlite3.connect(self.jobs.database) as conn:
            self.assertEqual(conn.execute("SELECT state FROM install_batches").fetchone()[0], "cancelled")
            self.assertEqual(
                conn.execute("SELECT state,download_state FROM install_batch_items").fetchone(), ("cancelled", "")
            )

    def test_control_export_and_restore_preserve_request_identity_key(self):
        SecretStore(self.paths).put("platform", "backup-password", "test-only")
        self.jobs.create(kind="lifecycle", service_id="demo", action="start", actor="owner", idempotency_key="request")
        original = (self.paths.state / "mu3lab.request-key").read_bytes()
        exported = platform_backup.export_control_plane(self.paths)
        self.assertEqual((exported / "mu3lab.request-key").read_bytes(), original)
        restored = RuntimePaths(self.paths.root / "restored")
        restored.state.mkdir(parents=True)
        rebuild._place_control(exported, restored)
        self.assertEqual((restored.state / "mu3lab.request-key").read_bytes(), original)
        again = JobStore(restored.state / "mu3lab.db")
        again.create(kind="lifecycle", service_id="demo", action="start", actor="owner", idempotency_key="request")

    def test_control_export_restores_matching_gateway_key_and_retires_authority(self):
        SecretStore(self.paths).put("platform", "backup-password", "test-only")
        authority = Authority(self.paths.projects / "mcp-gateway" / "authority")
        authority.grant_subject("operator", operator=True)
        old_token = authority.credential("operator", "demo")
        exported = platform_backup.export_control_plane(self.paths)
        self.assertEqual((exported / "gateway-arguments.key").read_bytes(), authority.key.read_bytes())
        restored = RuntimePaths(self.paths.root / "restored")
        restored.state.mkdir(parents=True)
        target = restored.projects / "mcp-gateway" / "authority"
        target.mkdir(parents=True)
        (target / "authority.db").write_bytes(b"inconsistent raw project copy")
        (target / "authority.db-wal").write_bytes(b"old WAL")
        (target / "authority.db-shm").write_bytes(b"old shared memory")
        rebuild._place_control(exported, restored)
        recovered = Authority(target, initialize=False)
        self.assertEqual(recovered.key.read_bytes(), authority.key.read_bytes())
        with self.assertRaises(ValueError):
            recovered.authenticate(old_token, "demo")
        self.assertFalse((target / "authority.db-wal").exists())
