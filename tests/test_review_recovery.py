"""Regression evidence for the stacked milestone merge review; disposable files only."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import backups, platform_backup, rebuild
from ctl.jobs import JobStore
from ctl.lifecycle.app_releases import Release
from ctl.operations import OperationStore
from ctl.runtime import RuntimePaths
from ctl.store.secrets import SecretStore


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
