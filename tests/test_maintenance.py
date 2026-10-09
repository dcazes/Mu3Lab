"""Updates take a backup first and put everything back when the new release fails."""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import ClassVar
from unittest.mock import ANY, patch

import yaml
from fastapi.testclient import TestClient

from ctl import job_guard
from ctl.api import create_app
from ctl.backups import BackupError
from ctl.jobs import JobStore, job_params
from ctl.lifecycle import app_releases, maintenance
from ctl.lifecycle.app_releases import Release
from ctl.registry import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
OLD = Release("v3.22.0", {"mealie": "ghcr.io/mealie-recipes/mealie:v3.22.0@sha256:old"})
NEW = Release("v3.23.0", {"mealie": "ghcr.io/mealie-recipes/mealie:v3.23.0@sha256:new"})


class _Workflow(unittest.TestCase):
    """Mealie installed at OLD on a machine whose checkout approves NEW."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.paths = RuntimePaths(base / "runtime")
        self.root = base / "checkout"
        definition = self.root / "apps" / "mealie"
        definition.mkdir(parents=True)
        compose = {"services": {name: {"image": image} for name, image in NEW.images.items()}}
        (definition / "docker-compose.yml").write_text(yaml.safe_dump(compose), encoding="utf-8")
        self.service = dataclasses.replace(
            load().get("mealie"), update={"repository": "mealie-recipes/mealie", "approved_version": NEW.version}
        )
        self.project = self.paths.projects / "mealie"
        self.project.mkdir(parents=True)
        (self.project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        self.store = JobStore(self.paths.runtime / "jobs.sqlite3")
        self.events: list[str] = []
        self.restored: list[str] = []
        self.started: list[str] = []
        self.stack = ExitStack()
        self.stack.enter_context(patch("ctl.lifecycle.app_releases.RuntimePaths", return_value=self.paths))
        app_releases.write("mealie", OLD)
        self.stack.enter_context(patch("ctl.service_ops.project_path", return_value=self.project))
        self.stack.enter_context(
            patch.object(maintenance, "data_directories", return_value=[self.paths.data / "mealie"])
        )
        self.download = self.stack.enter_context(patch.object(maintenance.app_releases, "download"))
        self.stack.enter_context(patch.object(maintenance, "_refresh_definition"))
        self.stack.enter_context(patch.object(maintenance, "_stop", side_effect=lambda *a: self.events.append("stop")))
        self.snapshot = self.stack.enter_context(
            patch.object(
                maintenance.backups, "snapshot", return_value={"snapshot_id": "c" * 64, "files": 1, "bytes": 1}
            )
        )
        self.stack.enter_context(
            patch.object(
                maintenance.backups, "restore", side_effect=lambda _s, snap, *_a, **_k: self.restored.append(snap)
            )
        )
        self.prune = self.stack.enter_context(patch.object(maintenance.backups, "prune", return_value=True))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def installed(self) -> Release | None:
        return app_releases.installed(self.service, self.root)

    def run_update(self, start_results, target=NEW.version):
        results = iter(start_results)

        def start(*_args):
            self.started.append(app_releases.installed_version(self.service, self.root))
            return next(results)

        self.stack.enter_context(patch.object(maintenance, "_start", side_effect=start))
        job = self.store.create(
            kind="lifecycle", service_id="mealie", action="update", actor="owner", params={"target_version": target}
        )
        maintenance.execute(self.store, None, self.store.get(job["id"]), self.service, self.root)
        return self.store.get(job["id"])


class UpdateWorkflowTests(_Workflow):
    def test_a_healthy_update_moves_to_the_approved_release(self):
        job = self.run_update([(True, "")])
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.started, ["v3.23.0"])
        self.assertEqual(self.installed(), NEW)
        self.download.assert_called_once_with(NEW.images, ANY)
        self.assertEqual(self.snapshot.call_args.kwargs["version"], "v3.22.0")
        self.assertIn("from v3.22.0 to v3.23.0", job["detail"])
        self.assertIn("Backup cccccccc", job["detail"])
        self.assertEqual(self.restored, [])

    def test_a_release_that_does_not_start_is_rolled_back_with_its_data(self):
        job = self.run_update([(False, "migration crashed"), (True, "")])
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error_code"], "update_rolled_back")
        self.assertEqual(self.restored, ["c" * 64])
        self.assertEqual(self.installed(), OLD)
        self.assertEqual(self.started, ["v3.23.0", "v3.22.0"])
        self.assertIn("nothing was lost", job["detail"])

    def test_no_backup_means_no_update(self):
        self.snapshot.side_effect = BackupError("disk full")
        job = self.run_update([(True, "")])
        self.assertEqual(job["error_code"], "backup_failed")
        self.assertEqual(self.installed(), OLD)
        # The app is only started again, still on its old release.
        self.assertEqual(self.started, ["v3.22.0"])

    def test_a_job_queued_before_a_different_approval_does_not_run(self):
        job = self.run_update([(True, "")], target="v3.22.5")
        self.assertEqual(job["error_code"], "approval_changed")
        self.assertEqual(self.installed(), OLD)
        self.download.assert_not_called()

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
        maintenance.execute(self.store, None, self.store.get(job["id"]), self.service, self.root)
        return self.store.get(job["id"])

    def test_restoring_the_backup_from_before_an_update_undoes_the_update(self):
        app_releases.write("mealie", NEW)
        job = self.restore("v3.22.0")
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.restored, ["a" * 64])
        self.assertEqual(self.installed(), OLD)
        self.assertIn("back on v3.22.0", job["detail"])

    def test_restoring_a_backup_from_the_approved_release_moves_forward_again(self):
        self.restore("v3.23.0")
        self.assertEqual(self.installed(), NEW)

    def test_a_backup_from_a_release_this_machine_never_ran_is_not_restored(self):
        job = self.restore("v3.10.0")
        self.assertEqual(job["error_code"], "release_unknown")
        self.assertEqual(self.restored, [])
        self.assertEqual(self.installed(), OLD)

    def test_restoring_data_from_the_same_release_leaves_the_release_alone(self):
        job = self.restore("v3.22.0")
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.download.assert_not_called()


class ReleaseIdentityRestoreTests(_Workflow):
    """R11: a backup brings back exactly the deployment it was saved from."""

    OTHER = Release("v3.22.0", {"mealie": OLD.images["mealie"], "postgres": "postgres:16@sha256:other"})

    def restore(self, **snapshot):
        listing = [{"id": "a" * 64, "time": "2026-10-01T12:00:00Z", "paths": [], **snapshot}]
        self.stack.enter_context(patch.object(maintenance.backups, "snapshots", return_value=listing))
        self.stack.enter_context(patch.object(maintenance, "_start", return_value=(True, "")))
        job = self.store.create(
            kind="lifecycle", service_id="mealie", action="restore", actor="owner", params={"snapshot_id": "a" * 64}
        )
        maintenance.execute(self.store, None, self.store.get(job["id"]), self.service, self.root)
        return self.store.get(job["id"])

    def test_a_backup_of_another_deployment_at_the_same_version_switches_to_it(self):
        app_releases.remember("mealie", self.OTHER)
        job = self.restore(version="v3.22.0", release_id=self.OTHER.id)
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.installed(), self.OTHER)
        self.download.assert_called_once_with(self.OTHER.images, ANY)

    def test_an_older_backup_whose_version_matches_two_deployments_is_refused(self):
        app_releases.remember("mealie", self.OTHER)
        app_releases.write("mealie", NEW)
        job = self.restore(version="v3.22.0")
        self.assertEqual(job["error_code"], "release_ambiguous")
        self.assertEqual(self.restored, [])
        self.assertEqual(self.installed(), NEW)

    def test_backups_are_tagged_with_the_installed_release(self):
        self.stack.enter_context(patch.object(maintenance, "_start", return_value=(True, "")))
        job = self.store.create(kind="lifecycle", service_id="mealie", action="backup", actor="owner")
        maintenance.execute(self.store, None, self.store.get(job["id"]), self.service, self.root)
        self.assertEqual(self.snapshot.call_args.kwargs["release_id"], OLD.id)
        self.assertEqual(app_releases.from_history("mealie", release_id=OLD.id), OLD)


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

    def status(self, update_available: bool) -> dict:
        return {
            "installed_version": "v3.22.0",
            "approved_version": "v3.23.0",
            "update_available": update_available,
            "supporting_only": False,
        }

    def test_the_update_target_is_the_approved_release_not_the_browsers(self):
        with (
            patch("ctl.api.routes.services.app_releases.status", return_value=self.status(True)),
            patch("ctl.api.routes.services.backup_readiness", return_value={"available": True}),
            patch("ctl.api.runtime.job_store") as store,
        ):
            store.return_value.by_idempotency_key.return_value = None
            store.return_value.create.return_value = {"id": "j", "state": "queued"}
            rejected = self.post({"action": "update", "target_version": "v99.0.0"})
            self.assertEqual(rejected.status_code, 422)
            response = self.post({"action": "update"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(store.return_value.create.call_args.kwargs["params"], {"target_version": "v3.23.0"})

    def test_updates_are_offered_only_when_they_can_run(self):
        cases = [("mealie", True, True), ("mealie", False, False), ("vaultwarden", True, False)]
        for service_id, available, enabled in cases:
            with (
                self.subTest(service_id=service_id, available=available),
                patch("ctl.api.routes.services.app_releases.status", return_value=self.status(available)),
                patch("ctl.api.routes.services.backup_readiness", return_value={"available": True}),
            ):
                response = self.client.get(f"/api/v1/services/{service_id}/updates", headers=self.headers)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["update_enabled"], enabled, body)
                if service_id == "vaultwarden":
                    self.assertIn("together with Mu3Lab", body["blocked_reason"])

    def test_checking_for_updates_needs_no_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
            response = self.client.get("/api/v1/services/mealie/updates", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("/releases/tag/", response.json()["release_url"])


if __name__ == "__main__":
    unittest.main()
