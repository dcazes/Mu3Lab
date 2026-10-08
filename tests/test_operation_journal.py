"""R09: an interrupted update or restore resumes from its journal, or is put back safely."""

from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import job_guard
from ctl.api import create_app
from ctl.backups import BackupError
from ctl.jobs import JobStore, job_params
from ctl.lifecycle import maintenance
from ctl.operations import OperationStore
from tests import test_maintenance
from tests.test_maintenance import NEW, OLD, _Workflow

LEASE_LOST = job_guard.JobInterrupted(job_guard.LEASE_LOST)
SAFETY = "c" * 64
CHOSEN = "a" * 64


class _Journal(_Workflow):
    def setUp(self):
        super().setUp()
        self.journal = OperationStore(self.store.database)

    def script(self, name, outcomes):
        """Patch ``maintenance.<name>``; each call takes the next outcome (an exception is raised)."""
        queue = list(outcomes)
        calls: list[int] = []

        def fake(*_args, **_kwargs):
            calls.append(1)
            outcome = queue.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        self.stack.enter_context(patch.object(maintenance, name, side_effect=fake))
        return calls

    def queue(self, action, **params):
        job = self.store.create(kind="lifecycle", service_id="mealie", action=action, actor="owner", params=params)
        return job["id"]

    def attempt(self, job_id):
        """One worker attempt; a lost lease stops it, as a killed worker would."""
        try:
            maintenance.execute(self.store, None, self.store.get(job_id), self.service, self.root)
        except job_guard.JobInterrupted:
            return False
        return True

    def operation(self, job_id):
        operation = self.journal.for_job(job_id)
        assert operation is not None
        return operation


class InterruptedUpdateTests(_Journal):
    def test_a_healthy_release_is_kept_when_the_worker_restarts_while_it_starts(self):
        self.script("_start", [LEASE_LOST, (True, "")])
        job = self.queue("update", target_version=NEW.version)
        self.assertFalse(self.attempt(job))
        self.assertEqual(self.operation(job).phase, "apply_update")
        self.assertTrue(self.attempt(job))
        self.assertEqual(self.store.get(job)["state"], "succeeded", self.store.get(job)["detail"])
        self.assertEqual(self.installed(), NEW)
        # The backup from before the update is taken once, never of half-updated data.
        self.assertEqual(self.snapshot.call_count, 1)
        self.assertEqual((self.operation(job).state, self.operation(job).attempt), ("succeeded", 2))
        self.prune.assert_called_once()

    def test_an_unhealthy_release_found_after_a_restart_is_rolled_back(self):
        self.script("_start", [LEASE_LOST, (False, "migration crashed"), (True, "")])
        job = self.queue("update", target_version=NEW.version)
        self.attempt(job)
        self.attempt(job)
        record = self.store.get(job)
        self.assertEqual(record["error_code"], "update_rolled_back")
        self.assertEqual(self.restored, [SAFETY])
        self.assertEqual(self.installed(), OLD)
        self.assertEqual(self.snapshot.call_count, 1)
        self.assertEqual(self.operation(job).state, "rolled_back")

    def test_a_restart_while_putting_the_old_release_back_finishes_putting_it_back(self):
        self.script("_start", [(False, "boom"), (True, "")])
        restores = iter([LEASE_LOST, None])

        def restore(_service, snapshot, *_args, **_kwargs):
            outcome = next(restores)
            if outcome:
                raise outcome
            self.restored.append(snapshot)

        self.stack.enter_context(patch.object(maintenance.backups, "restore", side_effect=restore))
        job = self.queue("update", target_version=NEW.version)
        self.assertFalse(self.attempt(job))
        self.assertEqual(self.operation(job).phase, "rollback")
        self.attempt(job)
        self.assertEqual(self.store.get(job)["error_code"], "update_rolled_back")
        self.assertEqual(self.restored, [SAFETY])
        self.assertEqual(self.installed(), OLD)
        self.assertEqual(self.snapshot.call_count, 1)

    def test_a_restart_before_anything_changed_starts_over(self):
        self.script("_start", [(True, "")])
        self.snapshot.side_effect = [LEASE_LOST, {"snapshot_id": SAFETY, "files": 1, "bytes": 1}]
        job = self.queue("update", target_version=NEW.version)
        self.assertFalse(self.attempt(job))
        self.assertEqual(self.installed(), OLD)
        self.assertTrue(self.attempt(job))
        self.assertEqual(self.installed(), NEW)

    def test_a_failed_rollback_needs_attention_and_keeps_its_backup(self):
        self.script("_start", [(False, "boom")])
        self.stack.enter_context(patch.object(maintenance.backups, "restore", side_effect=BackupError("disk full")))
        job = self.queue("update", target_version=NEW.version)
        self.attempt(job)
        record = self.store.get(job)
        self.assertEqual(record["error_code"], "update_rollback_failed")
        self.assertIn("Backup cccccccc holds the data from before", record["detail"])
        blocking = self.journal.needing_attention("mealie")
        assert blocking is not None
        self.assertEqual(blocking.recovery_snapshot, SAFETY)
        self.assertEqual(self.journal.protected("mealie"), {SAFETY})
        self.prune.assert_not_called()

    def test_retrying_recovery_resolves_the_block(self):
        self.script("_start", [(False, "boom"), (True, "")])
        restores = [BackupError("disk full"), None]

        def restore(_service, snapshot, *_args, **_kwargs):
            outcome = restores.pop(0)
            if outcome:
                raise outcome
            self.restored.append(snapshot)

        self.stack.enter_context(patch.object(maintenance.backups, "restore", side_effect=restore))
        update = self.queue("update", target_version=NEW.version)
        self.attempt(update)
        blocking = self.journal.needing_attention("mealie")
        assert blocking is not None
        recover = self.queue("recover", operation_id=blocking.id)
        self.assertTrue(self.attempt(recover))
        self.assertEqual(self.store.get(recover)["state"], "succeeded", self.store.get(recover)["detail"])
        self.assertEqual(self.restored, [SAFETY])
        self.assertEqual(self.installed(), OLD)
        self.assertIsNone(self.journal.needing_attention("mealie"))
        self.assertEqual(self.operation(update).state, "resolved")


class InterruptedRestoreTests(_Journal):
    def setUp(self):
        super().setUp()
        listing = [{"id": CHOSEN, "time": "2026-10-01T12:00:00Z", "version": OLD.version, "paths": []}]
        self.stack.enter_context(patch.object(maintenance.backups, "snapshots", return_value=listing))
        app_releases_write = maintenance.app_releases.write
        app_releases_write("mealie", NEW)

    def test_a_restart_while_swapping_data_finishes_the_restore_with_the_same_safety_backup(self):
        self.script("_start", [(True, "")])
        outcomes = [LEASE_LOST, None]

        def restore(_service, snapshot, *_args, **_kwargs):
            outcome = outcomes.pop(0)
            if outcome:
                raise outcome
            self.restored.append(snapshot)

        self.stack.enter_context(patch.object(maintenance.backups, "restore", side_effect=restore))
        job = self.queue("restore", snapshot_id=CHOSEN)
        self.assertFalse(self.attempt(job))
        self.assertEqual(self.operation(job).phase, "restore")
        self.assertTrue(self.attempt(job))
        self.assertEqual(self.store.get(job)["state"], "succeeded", self.store.get(job)["detail"])
        self.assertEqual(self.restored, [CHOSEN])
        self.assertEqual(self.installed(), OLD)
        self.assertEqual(self.snapshot.call_count, 1)
        self.assertIn("kept as backup cccccccc", self.store.get(job)["detail"])

    def test_a_restore_whose_undo_fails_blocks_the_app_until_a_restore_works(self):
        self.script("_start", [(True, ""), (True, "")])
        outcomes = [BackupError("unreadable"), BackupError("disk full"), None]

        def restore(_service, snapshot, *_args, **_kwargs):
            outcome = outcomes.pop(0)
            if outcome:
                raise outcome
            self.restored.append(snapshot)

        self.stack.enter_context(patch.object(maintenance.backups, "restore", side_effect=restore))
        first = self.queue("restore", snapshot_id=CHOSEN)
        self.attempt(first)
        self.assertEqual(self.store.get(first)["error_code"], "restore_rollback_failed")
        self.assertIsNotNone(self.journal.needing_attention("mealie"))
        second = self.queue("restore", snapshot_id=SAFETY)
        self.stack.enter_context(
            patch.object(
                maintenance.backups,
                "snapshots",
                return_value=[{"id": SAFETY, "time": "2026-10-02T12:00:00Z", "version": NEW.version, "paths": []}],
            )
        )
        self.assertTrue(self.attempt(second))
        self.assertEqual(self.store.get(second)["state"], "succeeded", self.store.get(second)["detail"])
        self.assertIsNone(self.journal.needing_attention("mealie"))


class OrphanedOperationTests(_Journal):
    def test_an_update_whose_job_ended_mid_apply_is_recovered_by_a_queued_job(self):
        self.script("_start", [LEASE_LOST, (False, "still broken"), (True, "")])
        update = self.queue("update", target_version=NEW.version)
        self.attempt(update)
        # The worker died and the job was finished without it (e.g. cancelled meanwhile).
        self.store.transition(update, "failed", actor="worker", detail="Worker stopped.")
        lines: list[str] = []
        self.assertEqual(maintenance.reconcile(self.store, lines.append), 1)
        recover = next(job for job in self.store.active_jobs() if job["action"] == "recover")
        self.assertEqual(recover["actor"], maintenance.RECOVERY_ACTOR)
        # Queued once: a second pass while it waits adds nothing.
        self.assertEqual(maintenance.reconcile(self.store, lines.append), 0)
        self.assertTrue(self.attempt(recover["id"]))
        self.assertEqual(self.operation(update).state, "rolled_back")
        self.assertEqual(self.installed(), OLD)
        self.assertEqual(self.restored, [SAFETY])

    def test_an_operation_stopped_during_its_download_is_closed_without_touching_the_app(self):
        starts = self.script("_start", [])
        self.download.side_effect = LEASE_LOST
        update = self.queue("update", target_version=NEW.version)
        self.attempt(update)
        self.store.transition(update, "cancelled", actor="worker", detail="Cancelled.")
        maintenance.reconcile(self.store, lambda _line: None)
        recover = next(job for job in self.store.active_jobs() if job["action"] == "recover")
        self.attempt(recover["id"])
        self.assertEqual(self.operation(update).state, "cancelled")
        self.assertEqual(starts, [])
        self.assertEqual(self.events, [])

    def test_recovery_that_never_finishes_needs_attention(self):
        self.script("_start", [LEASE_LOST])
        update = self.queue("update", target_version=NEW.version)
        self.attempt(update)
        self.store.transition(update, "failed", actor="worker", detail="Worker stopped.")
        for _ in range(maintenance.MAX_ATTEMPTS + 1):
            maintenance.reconcile(self.store, lambda _line: None)
            for job in self.store.active_jobs():
                self.store.transition(job["id"], "running", actor="worker")
                self.store.transition(job["id"], "failed", actor="worker", detail="Crashed.")
        self.assertEqual(self.operation(update).state, "needs_attention")


class CancelTests(_Journal):
    def test_cancelling_during_the_download_records_that_nothing_changed(self):
        self.download.side_effect = job_guard.JobInterrupted(job_guard.CANCELLED)
        update = self.queue("update", target_version=NEW.version)
        self.attempt(update)
        self.assertEqual(self.operation(update).state, "cancelled")
        self.assertEqual(self.installed(), OLD)


class RecoveryApiTests(unittest.TestCase):
    headers = test_maintenance.MaintenanceApiTests.headers

    def setUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(patch("ctl.api.security.ingress_token", return_value="token"))
        self.stack.enter_context(patch("ctl.api.security.csrf_token", return_value="bound"))
        self.stack.enter_context(patch("ctl.api.routes.services._effective_state", return_value="ready"))
        self.client = TestClient(create_app())
        store = JobStore.runtime()
        journal = OperationStore.runtime()
        assert store is not None and journal is not None
        self.store, self.journal = store, journal
        failed = store.create(kind="lifecycle", service_id="mealie", action="update", actor="owner")["id"]
        store.transition(failed, "failed", actor="worker", detail="Rollback failed.")
        op, _ = journal.begin(failed, "mealie", "update", was_running=True, previous_release=OLD, target_release=NEW)
        journal.finish(op.id, "needs_attention", "Putting back v3.22.0 failed (disk full).")
        self.addCleanup(journal.resolve, "mealie", "test cleanup")
        self.addCleanup(self.stack.close)

    def post(self, body):
        return self.client.post("/api/v1/services/mealie/actions", headers=self.headers, json=body)

    def test_other_actions_wait_until_recovery_is_settled(self):
        for body in ({"action": "stop"}, {"action": "update"}, {"action": "backup"}):
            with self.subTest(body=body):
                response = self.post(body)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("recovery_required", response.text)

    def test_retrying_recovery_names_the_blocked_operation(self):
        response = self.post({"action": "recover"})
        self.assertEqual(response.status_code, 200, response.text)
        job = self.store.get(response.json()["job"]["id"])
        assert job is not None
        blocking = self.journal.needing_attention("mealie")
        assert blocking is not None
        self.assertEqual(job_params(job), {"operation_id": blocking.id})
        self.store.transition(job["id"], "cancelled", actor="test")
