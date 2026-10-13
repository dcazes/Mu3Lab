"""Regression evidence for the stacked milestone merge review; disposable files only."""

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import backups, platform_backup, rebuild
from ctl.jobs import JobStore
from ctl.lifecycle import maintenance
from ctl.lifecycle.app_releases import Release
from ctl.operations import OperationStore
from ctl.runtime import RuntimePaths
from ctl.store.secrets import SecretStore
from gateway_authority import Authority


class RecoveryReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="mu3lab-review-"))
        self.paths = RuntimePaths(Path(self.temporary))
        self.paths.state.mkdir()
        self.paths.data.mkdir()
        self.jobs = JobStore(self.paths.state / "mu3lab.db")

    def operation(self, release=None):
        job = self.jobs.create(kind="lifecycle", service_id="demo", action="restore", actor="owner")
        journal = OperationStore(self.jobs.database)
        operation, _ = journal.begin(
            job["id"],
            "demo",
            "restore",
            was_running=True,
            previous_release=release,
            chosen_snapshot="a" * 32,
        )
        return journal, operation

    def test_journal_retains_complete_release_identity(self):
        release = Release("v1", {"db": "db@sha256:123"}, "nvidia", "definition-hash")
        journal, operation = self.operation(release)
        self.assertEqual(journal.get(operation.id).previous_release.id, release.id)

    def test_new_host_retires_old_operations_and_batches_before_reconciliation(self):
        journal, operation = self.operation()
        journal.save_plan(operation.id, "demo", {"roots": {"demo": "swapped"}, "tag": "old-host"})
        with sqlite3.connect(self.jobs.database) as conn:
            conn.execute(
                "INSERT INTO install_batches (id,actor,owner_uid,state,created_at,updated_at) VALUES ('batch','owner','owner','running','','')"
            )
            conn.execute(
                "INSERT INTO install_batch_items (batch_id,service_id,ordinal,explicitly_selected,state,download_state) VALUES ('batch','mealie',0,1,'pending','ready')"
            )
        rebuild.prepare_database(self.jobs.database)
        self.assertEqual(maintenance.reconcile(self.jobs, print), 0)
        self.assertEqual(journal.get(operation.id).state, "cancelled")
        self.assertEqual(journal.get(operation.id).restore_plans, {})
        self.assertEqual(self.jobs.active_jobs(), [])
        with sqlite3.connect(self.jobs.database) as conn:
            self.assertEqual(conn.execute("SELECT state FROM install_batches").fetchone()[0], "cancelled")
            self.assertEqual(
                conn.execute("SELECT state,download_state FROM install_batch_items").fetchone(), ("cancelled", "")
            )
            self.assertTrue(conn.execute("SELECT 1 FROM operation_steps WHERE event='finished'").fetchone())

    def test_every_retention_caller_preserves_unfinished_recovery(self):
        self.operation()
        listing = [{"id": "a" * 32, "time": "2026-10-08T00:00:00Z"}]
        listing += [{"id": f"{index:032x}", "time": f"2026-10-08T12:00:0{index}Z"} for index in range(5)]
        with (
            patch.object(backups, "snapshots", return_value=listing),
            patch.object(backups, "_kopia", return_value=(0, "")) as run,
        ):
            backups.prune("demo", lambda _: None, paths=self.paths)
        for call in run.call_args_list:
            self.assertNotIn("a" * 32, call.args[0])

    def test_existing_repository_reestablishes_policy_after_interrupted_creation(self):
        repository = self.paths.backups / "kopia"
        repository.mkdir(parents=True)
        (repository / backups.REPOSITORY_MARKER).touch()
        with (
            patch.object(backups, "_connect"),
            patch.object(backups, "_kopia", return_value=(1, "policy failed")),
            self.assertRaises(backups.BackupError),
        ):
            backups.ensure_repository(lambda _: None, self.paths)

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

    def test_new_host_retires_needs_attention_operations_too(self):
        journal, operation = self.operation()
        journal.finish(operation.id, "needs_attention", "Old host undo failed")
        rebuild.prepare_database(self.jobs.database)
        self.assertIsNone(journal.needing_attention("demo"))
        self.assertEqual(journal.get(operation.id).state, "cancelled")

    def test_swap_recovers_crash_after_rename_before_journal_commit(self):
        for absent in (False, True):
            with self.subTest(absent=absent):
                name = "absent" if absent else "existing"
                live = self.paths.data / name
                if not absent:
                    live.mkdir()
                    (live / "value").write_text("old")
                staged = backups._place(name, backups.STAGED, "test-tag", self.paths)
                staged.mkdir()
                (staged / "value").write_text("new")
                plan = {"tag": "test-tag", "roots": {name: "staged"}, "absent": [name] if absent else []}
                persisted = copy.deepcopy(plan)
                with self.assertRaisesRegex(RuntimeError, "crash"):
                    backups.swap(plan, lambda _: (_ for _ in ()).throw(RuntimeError("crash")), paths=self.paths)
                result = backups.swap(persisted, lambda _: None, paths=self.paths)
                self.assertEqual((live / "value").read_text(), "new")
                backups.undo(result, lambda _: None, paths=self.paths)
                if absent:
                    self.assertFalse(live.exists())
                else:
                    self.assertEqual((live / "value").read_text(), "old")
