"""Updates take a backup first and put everything back when the new release fails."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import job_guard
from ctl.api import create_app
from ctl.backups import BackupError
from ctl.jobs import JobStore, job_params
from ctl.lifecycle import image_updates, maintenance
from ctl.registry import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
NEW = {"mealie": "ghcr.io/mealie-recipes/mealie:v3.23.0@sha256:new"}


class _Workflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.temp.name))
        self.project = self.paths.projects / "mealie"
        self.project.mkdir(parents=True)
        (self.project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        self.store = JobStore(self.paths.runtime / "jobs.sqlite3")
        self.service = load().get("mealie")
        self.events: list[str] = []
        self.restored: list[str] = []
        self.started: list[str] = []
        self.stack = ExitStack()
        self.stack.enter_context(patch("ctl.lifecycle.image_updates.RuntimePaths", return_value=self.paths))
        self.stack.enter_context(patch("ctl.service_ops.project_path", return_value=self.project))
        self.stack.enter_context(
            patch.object(maintenance, "data_directories", return_value=[self.paths.data / "mealie"])
        )
        self.stack.enter_context(patch.object(maintenance.image_updates, "download", return_value=NEW))
        self.stack.enter_context(patch.object(maintenance, "_stop", side_effect=lambda *a: self.events.append("stop")))
        self.stack.enter_context(
            patch.object(
                maintenance.backups, "snapshot", return_value={"snapshot_id": "c" * 64, "files": 1, "bytes": 1}
            )
        )
        self.stack.enter_context(
            patch.object(
                maintenance.backups, "restore", side_effect=lambda _s, snap, *_a, **_k: self.restored.append(snap)
            )
        )

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def run_update(self, start_results):
        results = iter(start_results)

        def start(*_args):
            version = image_updates.installed_version(self.service, self.paths)
            self.started.append(version)
            return next(results)

        self.stack.enter_context(patch.object(maintenance, "_start", side_effect=start))
        job = self.store.create(
            kind="lifecycle",
            service_id="mealie",
            action="update",
            actor="owner",
            params={"target_version": "v3.23.0"},
        )
        maintenance.execute(self.store, None, self.store.get(job["id"]), self.service, ROOT)
        return self.store.get(job["id"])


class UpdateWorkflowTests(_Workflow):
    def test_a_healthy_update_keeps_the_new_release(self):
        job = self.run_update([(True, "")])
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.started, ["v3.23.0"])
        self.assertEqual(image_updates.read("mealie", self.paths)["images"], NEW)
        self.assertIn("Backup cccccccc", job["detail"])
        self.assertEqual(self.restored, [])

    def test_a_release_that_does_not_start_is_rolled_back_with_its_data(self):
        job = self.run_update([(False, "migration crashed"), (True, "")])
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error_code"], "update_rolled_back")
        self.assertEqual(self.restored, ["c" * 64])
        self.assertIsNone(image_updates.read("mealie", self.paths))
        self.assertEqual(self.started, ["v3.23.0", "v3.22.0"])
        self.assertIn("nothing was lost", job["detail"])

    def test_no_backup_means_no_update(self):
        maintenance.backups.snapshot.side_effect = BackupError("disk full")
        job = self.run_update([(True, "")])
        self.assertEqual(job["error_code"], "backup_failed")
        self.assertIsNone(image_updates.read("mealie", self.paths))
        # The app is only started again, still on its old release.
        self.assertEqual(self.started, ["v3.22.0"])

    def test_retrying_a_job_keeps_its_target(self):
        job = self.run_update([(False, "boom"), (True, "")])
        retry = self.store.retry(job["id"], actor="owner")
        self.assertEqual(job_params(self.store.get(retry["id"])), {"target_version": "v3.23.0"})


class RestoreWorkflowTests(_Workflow):
    def restore(self, snapshot_version):
        listing = [{"id": "a" * 64, "time": "2026-10-01T12:00:00Z", "version": snapshot_version, "paths": []}]
        self.stack.enter_context(patch.object(maintenance.backups, "snapshots", return_value=listing))
        self.stack.enter_context(patch.object(maintenance, "_start", return_value=(True, "")))
        job = self.store.create(
            kind="lifecycle", service_id="mealie", action="restore", actor="owner", params={"snapshot_id": "a" * 64}
        )
        maintenance.execute(self.store, None, self.store.get(job["id"]), self.service, ROOT)
        return self.store.get(job["id"])

    def test_restoring_the_backup_from_before_an_update_undoes_the_update(self):
        image_updates.write("mealie", "v3.24.0", {"mealie": "ghcr.io/x:v3.24.0@sha256:b"}, self.paths)
        job = self.restore("v3.23.0")
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.restored, ["a" * 64])
        self.assertEqual(image_updates.read("mealie", self.paths), {"version": "v3.23.0", "images": NEW})
        self.assertIn("back on v3.23.0", job["detail"])

    def test_restoring_to_mu3labs_own_release_drops_the_override(self):
        image_updates.write("mealie", "v3.24.0", {"mealie": "ghcr.io/x:v3.24.0@sha256:b"}, self.paths)
        self.restore("v3.22.0")
        self.assertIsNone(image_updates.read("mealie", self.paths))

    def test_restoring_data_from_the_same_release_leaves_the_release_alone(self):
        job = self.restore("v3.22.0")
        self.assertEqual(job["state"], "succeeded", job["detail"])
        maintenance.image_updates.download.assert_not_called()


class UncancellableTests(unittest.TestCase):
    def test_a_cancel_waits_for_the_block_to_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            job = store.create(kind="lifecycle", service_id="mealie", action="update", actor="owner")
            claimed = store.claim("worker")
            with job_guard.executing(store, claimed["id"], "worker"):
                store.cancel(job["id"], actor="owner")
                with job_guard.uncancellable():
                    job_guard.checkpoint()
                with self.assertRaises(job_guard.JobInterrupted):
                    job_guard.checkpoint()


class MaintenanceApiTests(unittest.TestCase):
    headers: ClassVar[dict[str, str]] = {
        "x-mu3lab-proxy-token": "token",
        "x-authentik-username": "owner",
        "x-authentik-uid": "subject",
        "x-authentik-email": "owner@example.test",
        "x-authentik-groups": "mu3lab-operators",
        "host": "testserver",
        "origin": "https://testserver",
        "x-mu3lab-csrf": "bound",
    }

    def setUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(patch("ctl.api.security.ingress_token", return_value="token"))
        self.stack.enter_context(patch("ctl.api.security.csrf_token", return_value="bound"))
        self.stack.enter_context(patch("ctl.api.routes.services._effective_state", return_value="ready"))
        self.client = TestClient(create_app())

    def tearDown(self):
        self.stack.close()

    def post(self, body):
        return self.client.post("/api/v1/services/mealie/actions", headers=self.headers, json=body)

    def test_restoring_requires_the_app_name(self):
        response = self.post({"action": "restore", "snapshot_id": "a" * 64, "confirm": "mealie"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("type Mealie", response.text)

    def test_the_update_target_comes_from_upstream_not_the_browser(self):
        release = {"latest_version": "v3.23.0", "release_url": "", "current_version": "v3.22.0"}
        with (
            patch("ctl.api.routes.services.latest_release", return_value=release),
            patch("ctl.api.routes.services.backup_readiness", return_value={"available": True}),
            patch("ctl.api.runtime.job_store") as store,
        ):
            store.return_value.by_idempotency_key.return_value = None
            store.return_value.create.return_value = {"id": "j", "state": "queued"}
            response = self.post({"action": "update", "target_version": "v99.0.0"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(store.return_value.create.call_args.kwargs["params"], {"target_version": "v3.23.0"})

    def test_updates_are_offered_only_when_they_can_run(self):
        cases = [
            ("mealie", {"latest_version": "v3.23.0"}, True),
            ("mealie", {"latest_version": "v3.21.0"}, False),
            ("firecrawl", {"latest_version": "v2.12.0"}, False),
            ("vaultwarden", {"latest_version": "1.38.0"}, False),
        ]
        for service_id, release, enabled in cases:
            with (
                self.subTest(service_id=service_id, release=release),
                patch("ctl.api.routes.services.latest_release", return_value={**release, "update_available": True}),
                patch("ctl.api.routes.services.backup_readiness", return_value={"available": True}),
            ):
                response = self.client.get(f"/api/v1/services/{service_id}/updates", headers=self.headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["update_enabled"], enabled, response.json())
                if not enabled and response.json()["update_available"]:
                    self.assertTrue(response.json()["blocked_reason"])


if __name__ == "__main__":
    unittest.main()
