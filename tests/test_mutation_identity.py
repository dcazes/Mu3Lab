"""R08: request identity never aliases another command or person's mutation."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl.api import create_app
from ctl.control_state import ControlState
from ctl.install_batches import InstallBatchStore
from ctl.jobs import JobStore
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.store import db
from ctl.store.secrets import SecretStore
from tests.test_provider_onboarding import OPERATOR_WITH_CSRF


class MutationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = JobStore(Path(self.tmp.name) / "db.sqlite3")
        self.args = dict(
            kind="lifecycle",
            service_id="example",
            action="start",
            actor="owner",
            actor_subject="subject-one",
            namespace="services.actions",
            idempotency_key="same",
        )

    def test_changed_request_with_same_key_conflicts(self):
        first = self.store.create(**self.args, request={"action": "start"})
        with self.assertRaisesRegex(RuntimeError, "idempotency_conflict"):
            self.store.create(**(self.args | {"service_id": "other"}), request={"action": "start"})
        with self.assertRaisesRegex(RuntimeError, "idempotency_conflict"):
            self.store.create(**self.args, request={"action": "stop"})
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertEqual(self.store.get(first["id"])["action"], "start")

    def test_subject_scopes_keys_and_survives_username_change(self):
        first = self.store.create(**self.args)
        self.store.transition(first["id"], "running", actor="worker")
        self.store.transition(first["id"], "succeeded", actor="worker")
        self.assertEqual(self.store.create(**(self.args | {"actor": "renamed"}))["id"], first["id"])
        second = self.store.create(**(self.args | {"actor_subject": "subject-two"}))
        self.assertNotEqual(second["id"], first["id"])

    def test_different_active_operation_is_busy_without_preparing(self):
        first = self.store.create(**self.args)
        prepared = []
        with self.assertRaisesRegex(RuntimeError, "resource_busy"):
            self.store.create(**(self.args | {"action": "stop", "idempotency_key": "stop"}), prepare=prepared.append)
        self.assertEqual(prepared, [])
        self.assertEqual(self.store.get(first["id"])["action"], "start")

    def test_equivalent_active_alias_key_remains_a_replay_after_completion(self):
        first = self.store.create(**self.args)
        other = self.args | {"idempotency_key": "alias"}
        self.assertEqual(self.store.create(**other)["id"], first["id"])
        self.store.transition(first["id"], "running", actor="worker")
        self.store.transition(first["id"], "succeeded", actor="worker")
        self.assertEqual(self.store.create(**other)["id"], first["id"])

    def test_fingerprint_compares_secret_changes_without_persisting_them(self):
        self.store.create(**self.args, request={"api_key": "secret-one"})
        with self.assertRaisesRegex(RuntimeError, "idempotency_conflict"):
            self.store.create(**self.args, request={"api_key": "secret-two"})
        self.assertNotIn("secret-one", self.store.database.read_bytes().decode(errors="ignore"))

    def test_desired_state_and_private_inputs_roll_back_with_rejected_preparation(self):
        state = ControlState(self.store.database)

        def commit(job_id):
            state.set_installation("example", "queued", job_id=job_id)
            raise ValueError("desired state failed")

        with self.assertRaises(ValueError):
            self.store.create(**self.args, commit_state=commit)
        self.assertIsNone(state.installation("example"))
        self.assertEqual(self.store.jobs(), [])
        self.assertEqual(self.store.create(**self.args)["state"], "queued")

    def test_ciphertext_rolls_back_with_failed_job_creation(self):
        paths = RuntimePaths(Path(self.tmp.name) / "runtime")
        store = JobStore(db.database(paths))
        secrets = SecretStore(paths)

        def prepare(job_id):
            secrets.put("job", job_id, {"email": "private@example.test"})

        def fail(_):
            raise ValueError("state failed")

        with self.assertRaises(ValueError):
            store.create(**self.args, prepare=prepare, commit_state=fail)
        self.assertEqual(store.jobs(), [])
        self.assertEqual(secrets.list_names("job"), [])
        accepted = store.create(**self.args, prepare=prepare)
        self.assertEqual(secrets.get("job", accepted["id"]), {"email": "private@example.test"})

    def test_missing_fingerprint_key_cannot_silently_replace_request_identity(self):
        first = self.store.create(**self.args)
        key_path = self.store.database.with_suffix(".request-key")
        original_key = key_path.read_bytes()
        key_path.unlink()
        with self.assertRaisesRegex(RuntimeError, "key is missing"):
            self.store.create(**self.args)
        self.assertFalse(key_path.exists())
        key_path.write_bytes(original_key)
        self.assertEqual(self.store.create(**self.args)["id"], first["id"])

    def test_parallel_identical_requests_prepare_once(self):
        prepared = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.store.create(**self.args, prepare=prepared.append), range(4)))
        self.assertEqual(len({job["id"] for job in results}), 1)
        self.assertEqual(len(prepared), 1)

    def test_waiting_retry_rolls_back_cancellation_if_preparation_fails(self):
        first = self.store.create(**self.args)
        self.store.transition(first["id"], "waiting_for_confirmation", actor="worker")

        def fail(_):
            raise ValueError("private inputs unavailable")

        with self.assertRaises(ValueError):
            self.store.retry(first["id"], actor="owner", actor_subject="subject-one", prepare=fail)
        self.assertEqual(self.store.get(first["id"])["state"], "waiting_for_confirmation")


class ServiceMutationApiTests(unittest.TestCase):
    def test_busy_and_completed_replay_do_not_change_desired_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = JobStore(Path(tmp) / "db.sqlite3")
            control = ControlState(jobs.database)
            with (
                patch("ctl.api.security.ingress_token", return_value="real-token"),
                patch("ctl.api.security.csrf_token", return_value="bound"),
                patch("ctl.api.runtime.job_store", return_value=jobs),
                patch("ctl.api.routes.services.ControlState.runtime", return_value=control),
                patch("ctl.api.routes.services._effective_state", return_value="stopped") as status,
                patch("ctl.api._reconcile_runtime_state"),
                TestClient(create_app()) as client,
            ):
                headers = OPERATOR_WITH_CSRF | {"idempotency-key": "start"}
                path = "/api/v1/services/mealie/actions"
                first = client.post(path, headers=headers, json={"action": "start"})
                self.assertEqual(first.status_code, 200, first.text)
                job_id = first.json()["job"]["id"]
                conflict = client.post(path, headers=headers | {"idempotency-key": "stop"}, json={"action": "stop"})
                self.assertEqual(conflict.status_code, 409, conflict.text)
                self.assertEqual(conflict.json()["code"], "resource_busy")
                self.assertEqual(conflict.json()["blocking_job_id"], job_id)
                self.assertEqual(control.installation("mealie")["state"], "queued")
                changed = client.post(path, headers=headers, json={"action": "stop"})
                self.assertEqual(changed.json()["code"], "idempotency_conflict")
                jobs.transition(job_id, "running", actor="worker")
                jobs.transition(job_id, "succeeded", actor="worker")
                control.set_installation("mealie", "running", job_id=job_id)
                status.return_value = "ready"
                replay = client.post(path, headers=headers, json={"action": "start"})
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertEqual(replay.json()["job"]["id"], job_id)
                self.assertEqual(control.installation("mealie")["state"], "running")


class MigrationAndBatchIdentityTests(unittest.TestCase):
    def test_legacy_key_is_not_reinterpreted_as_a_subject_bound_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            with sqlite3.connect(path) as connection:
                connection.executescript((db.MIGRATIONS / "0001_initial.sql").read_text())
                connection.executescript((db.MIGRATIONS / "0002_status_checklist.sql").read_text())
                connection.executescript(
                    "CREATE TABLE schema_version (version INTEGER PRIMARY KEY); INSERT INTO schema_version VALUES (1), (2);"
                )
                connection.execute(
                    "INSERT INTO jobs (id,kind,service_id,action,state,actor,created_at,updated_at,detail,idempotency_key) VALUES ('old','lifecycle','example','start','succeeded','owner','','','','old-key')"
                )
            store = JobStore(path)
            with self.assertRaisesRegex(RuntimeError, "idempotency_conflict"):
                store.create(
                    kind="lifecycle",
                    service_id="example",
                    action="start",
                    actor="owner",
                    actor_subject="subject",
                    idempotency_key="old-key",
                )
            self.assertEqual(len(store.jobs()), 1)
            self.assertEqual(store.get("old")["state"], "succeeded")

    def test_batch_enqueue_rolls_back_item_and_job_when_desired_state_fails(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("ctl.install_batches.workflow_secrets.save_job_identity"),
            patch("ctl.install_batches.workflow_secrets.job_identity", return_value=None),
        ):
            path = Path(tmp) / "db.sqlite3"
            jobs, batches, control = JobStore(path), InstallBatchStore(path), ControlState(path)
            batch = batches.create(
                load(),
                ["mealie"],
                actor="owner",
                owner_uid="subject",
                identity={},
                idempotency_key="batch",
                jobs=jobs,
                control=control,
            )
            with (
                patch("ctl.install_batches.ControlState.set_installation", side_effect=RuntimeError("state failed")),
                self.assertRaisesRegex(RuntimeError, "state failed"),
            ):
                batches._enqueue(batch["id"], 0, "owner", jobs)
            self.assertEqual(jobs.jobs(), [])
            restored = batches.get(batch["id"])
            self.assertEqual(restored, batch)

    def test_batch_key_is_scoped_and_binds_order_and_parallelism(self):
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.install_batches.workflow_secrets.save_job_identity"):
            path = Path(tmp) / "db.sqlite3"
            jobs, batches, control = JobStore(path), InstallBatchStore(path), ControlState(path)
            identity = {
                "owner_uid": "person-one",
                "username": "owner",
                "email": "o@example.test",
                "display_name": "Owner",
            }
            args = dict(
                actor="owner",
                owner_uid="person-one",
                identity=identity,
                idempotency_key="same",
                jobs=jobs,
                control=control,
                parallel_downloads=2,
            )
            first = batches.create(load(), ["mealie"], **args)
            self.assertEqual(batches.create(load(), ["mealie"], **args)["id"], first["id"])
            with self.assertRaisesRegex(RuntimeError, "idempotency_conflict"):
                batches.create(load(), ["adventurelog"], **args)
            with self.assertRaisesRegex(RuntimeError, "idempotency_conflict"):
                batches.create(load(), ["mealie"], **(args | {"parallel_downloads": 3}))
            other = batches.create(
                load(),
                ["adventurelog"],
                **(args | {"owner_uid": "person-two", "identity": identity | {"owner_uid": "person-two"}}),
            )
            self.assertNotEqual(first["id"], other["id"])
