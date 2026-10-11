"""Restores and updates either finish or put the app back as it was; nothing continues after a crash.

The data folders are real folders under a temporary data root, so every
rename a restore or an undo makes is checked on disk. Kopia, Docker and the
app's own start are replaced by fakes.
"""

from __future__ import annotations

import dataclasses
import shutil
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
from ctl.lifecycle import app_releases, in_progress, maintenance, restore, update
from ctl.lifecycle.app_releases import Release
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.store import records

ROOT = Path(__file__).resolve().parents[1]
OLD = Release("v3.22.0", {"mealie": "ghcr.io/mealie-recipes/mealie:v3.22.0@sha256:old"})
NEW = Release("v3.23.0", {"mealie": "ghcr.io/mealie-recipes/mealie:v3.23.0@sha256:new"})
BACKUP = "a" * 64
PRE_UPDATE = "c" * 64


class _Workflow(unittest.TestCase):
    """Mealie installed at OLD, with one data folder holding "live", on a checkout that approves NEW."""

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
        (self.project / "docker-compose.yml").write_text("services: {old: {}}\n", encoding="utf-8")
        self.live = self.paths.data / "mealie"
        self.live.mkdir(parents=True)
        (self.live / "data.txt").write_text("live", encoding="utf-8")
        self.paths.state.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.paths.runtime / "jobs.sqlite3")
        self.events: list[str] = []
        self.starts: list[tuple[bool, str]] = []
        self.listing: list[dict] = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(self.temp.cleanup)
        for module in ("ctl.lifecycle.app_releases", "ctl.lifecycle.deployments", "ctl.app_settings"):
            self.stack.enter_context(patch(f"{module}.RuntimePaths", return_value=self.paths))
        for module in (restore, update, in_progress):
            self.stack.enter_context(patch.object(module, "RuntimePaths", return_value=self.paths))
        app_releases.write("mealie", OLD)
        self.stack.enter_context(patch("ctl.service_ops.project_path", return_value=self.project))
        for module in (restore, update):
            self.stack.enter_context(patch.object(module, "data_directories", return_value=[self.live]))
        self.download = self.stack.enter_context(patch.object(app_releases, "download"))
        self.stack.enter_context(patch.object(update, "render_service", side_effect=self.new_definition))
        self.stack.enter_context(patch.object(app_releases, "align"))
        self.validate = self.stack.enter_context(patch.object(update, "_validate"))
        self.stack.enter_context(patch("ctl.lifecycle.app_control.stop", side_effect=self.stop))
        self.stack.enter_context(patch("ctl.lifecycle.app_control.start", side_effect=self.start))
        self.unpack = self.stack.enter_context(patch("ctl.backups.restore_folder", side_effect=self.restore_folder))
        self.stack.enter_context(patch("ctl.backups.wipe", side_effect=self.wipe))
        self.stack.enter_context(patch("ctl.backups.snapshots", side_effect=lambda *_a, **_k: self.listing))
        self.snapshot = self.stack.enter_context(
            patch("ctl.backups.snapshot", return_value={"snapshot_id": PRE_UPDATE, "files": 1})
        )

    # --- fakes

    def new_definition(self, *_args):
        (self.project / "docker-compose.yml").write_text("services: {new: {}}\n", encoding="utf-8")

    def stop(self, *_args):
        self.events.append("stop")

    def start(self, *_args):
        self.events.append(f"start {app_releases.installed_version(self.service, self.root)}")
        return self.starts.pop(0) if self.starts else (True, "")

    def restore_folder(self, snapshot_id, _folder, target, *_args, **_kwargs):
        (target / "data.txt").write_text(f"from {snapshot_id[:1]}", encoding="utf-8")

    def wipe(self, places, *_args, **_kwargs):
        for place in places:
            shutil.rmtree(place, ignore_errors=True)

    # --- helpers

    def backup(self, version: str = OLD.version, **extra) -> None:
        self.listing = [
            {"id": BACKUP, "time": "2026-10-01T12:00:00Z", "version": version, "paths": ["/data/mealie"], **extra}
        ]

    def job(self, action: str, *, state=None, reclaimed: bool = False, **params) -> dict:
        job = self.store.create(kind="lifecycle", service_id="mealie", action=action, actor="owner", params=params)
        self.store.claim("worker")
        if reclaimed:
            # The first worker died: its lease ran out and another worker claimed the job.
            with self.store._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (job["id"],)
                )
            self.store.claim("worker-2")
        maintenance.execute(self.store, state, self.store.get(job["id"]), self.service, self.root)
        return self.store.get(job["id"])

    def data(self) -> str:
        return (self.live / "data.txt").read_text(encoding="utf-8")

    def leftovers(self) -> list[str]:
        return sorted(path.name for path in self.paths.data.iterdir() if path.name.startswith("."))

    def installed(self) -> Release | None:
        return app_releases.installed(self.service, self.root)


class RestoreTests(_Workflow):
    def test_a_healthy_restore_replaces_the_data_and_keeps_nothing_aside(self):
        self.backup()
        job = self.job("restore", snapshot_id=BACKUP)
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.data(), "from a")
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.events, ["stop", "start v3.22.0"])
        self.assertEqual(in_progress.get("mealie", self.paths), {})
        self.assertTrue(records.get("backup-apps", "mealie", self.paths).get("restore_tested_at"))
        self.download.assert_not_called()

    def test_restored_data_that_does_not_start_is_put_back(self):
        self.backup()
        self.starts = [(False, "crash"), (True, "")]
        job = self.job("restore", snapshot_id=BACKUP)
        self.assertEqual(job["error_code"], "restore_rolled_back")
        self.assertIn("data was put back as it was", job["detail"])
        self.assertEqual(self.data(), "live")
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.events, ["stop", "start v3.22.0", "stop", "start v3.22.0"])
        self.assertEqual(in_progress.get("mealie", self.paths), {})

    def test_a_backup_that_cannot_be_unpacked_changes_nothing(self):
        self.backup()
        self.unpack.side_effect = BackupError("damaged block")
        job = self.job("restore", snapshot_id=BACKUP)
        self.assertEqual(job["error_code"], "restore_failed")
        self.assertIn("Nothing changed", job["detail"])
        self.assertEqual((self.data(), self.events, self.leftovers()), ("live", [], []))

    def test_a_stopped_app_stays_stopped_and_is_not_marked_tested(self):
        self.backup()
        state = type("State", (), {"installation": lambda _self, _id: {"state": "stopped"}})()
        state.set_installation = lambda *_a, **_k: None
        job = self.job("restore", state=state, snapshot_id=BACKUP)
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.events, [])
        self.assertIn("stays stopped", job["detail"])
        self.assertFalse(records.get("backup-apps", "mealie", self.paths).get("restore_tested_at"))

    def test_restoring_the_backup_from_before_an_update_undoes_the_update(self):
        app_releases.write("mealie", NEW)
        self.backup(OLD.version)
        job = self.job("restore", snapshot_id=BACKUP)
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.installed(), OLD)
        self.assertIn("back on v3.22.0", job["detail"])
        self.download.assert_called_once_with(OLD.images, ANY)

    def test_a_backup_from_a_release_this_machine_never_ran_is_not_restored(self):
        self.backup("v3.10.0")
        job = self.job("restore", snapshot_id=BACKUP)
        self.assertEqual(job["error_code"], "release_unknown")
        self.assertEqual((self.data(), self.installed()), ("live", OLD))

    def test_a_backup_of_another_deployment_at_the_same_version_switches_to_it(self):
        other = Release("v3.22.0", {"mealie": OLD.images["mealie"], "postgres": "postgres:16@sha256:other"})
        app_releases.remember("mealie", other)
        self.backup(OLD.version, release_id=other.id)
        job = self.job("restore", snapshot_id=BACKUP)
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.installed(), other)

    def test_an_older_backup_whose_version_matches_two_deployments_is_refused(self):
        app_releases.remember("mealie", Release("v3.22.0", {"mealie": "x@sha256:other"}))
        app_releases.write("mealie", NEW)
        self.backup(OLD.version)
        job = self.job("restore", snapshot_id=BACKUP)
        self.assertEqual(job["error_code"], "release_ambiguous")
        self.assertEqual(self.installed(), NEW)

    def test_a_restore_worker_that_died_is_never_continued_and_the_owner_puts_it_back(self):
        # The worker died between the two renames: live data aside, restored copy unpacked.
        (self.paths.data / ".mealie.original").mkdir()
        (self.paths.data / ".mealie.original" / "data.txt").write_text("live", encoding="utf-8")
        shutil.rmtree(self.live)
        (self.paths.data / ".mealie.restoring").mkdir()
        in_progress.begin("mealie", "restore", "dead-job", self.paths, folders=["mealie"], absent=[], bundle="")
        job = self.job("restore", reclaimed=True, snapshot_id=BACKUP)
        self.assertEqual(job["error_code"], "interrupted")
        self.assertEqual(self.events, [])
        blocked = self.job("update", target_version=NEW.version)
        self.assertEqual(blocked["error_code"], "recovery_required")
        put_back = self.job("recover")
        self.assertEqual(put_back["state"], "succeeded", put_back["detail"])
        self.assertEqual((self.data(), self.leftovers()), ("live", []))
        self.assertEqual(in_progress.get("mealie", self.paths), {})

    def test_putting_back_after_the_swap_restores_the_original(self):
        os_live = self.paths.data / ".mealie.original"
        self.live.rename(os_live)
        self.live.mkdir()
        (self.live / "data.txt").write_text("from a", encoding="utf-8")
        in_progress.begin("mealie", "restore", "dead-job", self.paths, folders=["mealie"], absent=[], bundle="")
        job = self.job("recover")
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual((self.data(), self.leftovers()), ("live", []))


class UpdateTests(_Workflow):
    def test_a_healthy_update_moves_to_the_approved_release(self):
        job = self.job("update", target_version=NEW.version)
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.installed(), NEW)
        self.assertEqual(self.events, ["stop", "start v3.23.0"])
        self.assertEqual(self.snapshot.call_args.kwargs["version"], OLD.version)
        self.assertIn("from v3.22.0 to v3.23.0", job["detail"])
        self.assertIn("Backup cccccccc", job["detail"])
        self.assertEqual(in_progress.get("mealie", self.paths), {})

    def test_a_release_that_does_not_start_is_put_back_with_its_files_and_data(self):
        self.listing = [
            {"id": PRE_UPDATE, "time": "2026-10-01T12:00:00Z", "version": OLD.version, "paths": ["/data/mealie"]}
        ]
        self.starts = [(False, "migration crashed"), (True, "")]
        job = self.job("update", target_version=NEW.version)
        self.assertEqual(job["error_code"], "update_rolled_back")
        self.assertIn("nothing was lost", job["detail"])
        self.assertEqual(self.installed(), OLD)
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")
        self.assertEqual(self.data(), "from c")  # the data from just before the update
        self.assertEqual(self.events, ["stop", "start v3.23.0", "stop", "start v3.22.0"])
        self.assertEqual(self.leftovers(), [])

    def test_no_backup_means_no_update(self):
        self.snapshot.side_effect = BackupError("disk full")
        job = self.job("update", target_version=NEW.version)
        self.assertEqual(job["error_code"], "backup_failed")
        self.assertEqual(self.installed(), OLD)
        self.assertEqual(self.events, ["stop", "start v3.22.0"])
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")

    def test_an_invalid_new_definition_stops_before_the_app_does(self):
        self.validate.side_effect = RuntimeError("unknown service key")
        job = self.job("update", target_version=NEW.version)
        self.assertEqual(job["error_code"], "definition_invalid")
        self.assertIn("Nothing changed", job["detail"])
        self.assertEqual(self.events, [])
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")
        self.snapshot.assert_not_called()

    def test_a_job_queued_before_a_different_approval_does_not_run(self):
        job = self.job("update", target_version="v3.22.5")
        self.assertEqual(job["error_code"], "approval_changed")
        self.download.assert_not_called()

    def test_retrying_a_job_keeps_its_target(self):
        self.starts = [(False, "boom"), (True, "")]
        job = self.job("update", target_version=NEW.version)
        retry = self.store.retry(job["id"], actor="owner")
        self.assertEqual(job_params(self.store.get(retry["id"])), {"target_version": "v3.23.0"})

    def interrupted_after_apply(self):
        bundle = update.deployments.capture(self.service, "update-dead", release_id=OLD.id, storage=["mealie"])
        self.new_definition()
        app_releases.write("mealie", NEW)
        in_progress.begin(
            "mealie",
            "update",
            "dead-job",
            self.paths,
            bundle=bundle,
            was_running=True,
            previous=OLD.as_dict(),
            target=NEW.as_dict(),
            snapshot_id=PRE_UPDATE,
            applied=True,
        )
        self.listing = [
            {"id": PRE_UPDATE, "time": "2026-10-01T12:00:00Z", "version": OLD.version, "paths": ["/data/mealie"]}
        ]

    def test_an_interrupted_update_can_be_undone(self):
        self.interrupted_after_apply()
        job = self.job("recover")
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.installed(), OLD)
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")
        self.assertEqual(self.data(), "from c")
        self.assertEqual(in_progress.get("mealie", self.paths), {})

    def test_an_interrupted_update_can_be_kept_when_it_starts_healthy(self):
        self.interrupted_after_apply()
        job = self.job("keep_update")
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(self.installed(), NEW)
        self.assertEqual(self.data(), "live")
        self.assertEqual(in_progress.get("mealie", self.paths), {})

    def test_keeping_a_new_release_that_does_not_start_leaves_the_choice_open(self):
        self.interrupted_after_apply()
        self.starts = [(False, "crash")]
        job = self.job("keep_update")
        self.assertEqual(job["error_code"], "put_back_failed")
        self.assertTrue(in_progress.get("mealie", self.paths))


class BackupHoldJobTests(_Workflow):
    def test_the_backup_service_stops_and_starts_any_app_with_the_installers_settings(self):
        for service_id in ("mealie", "authentik"):
            service = load().get(service_id)
            for action in ("backup_stop", "backup_start"):
                job = self.store.create(kind="lifecycle", service_id=service_id, action=action, actor="backup-service")
                maintenance.execute(self.store, None, self.store.get(job["id"]), service, self.root)
                self.assertEqual(self.store.get(job["id"])["state"], "succeeded")
        self.assertEqual(len(self.events), 4)


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
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("ctl.api.security.ingress_token", return_value="token"))
        self.stack.enter_context(patch("ctl.api.security.csrf_token", return_value="bound"))
        self.stack.enter_context(patch("ctl.api.routes.services._effective_state", return_value="ready"))
        self.client = TestClient(create_app())

    def post(self, body):
        return self.client.post("/api/v1/services/mealie/actions", headers=self.headers, json=body)

    def test_restoring_requires_the_app_name(self):
        response = self.post({"action": "restore", "snapshot_id": "a" * 64, "confirm": "mealie"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("type Mealie", response.text)

    def test_an_interrupted_change_blocks_everything_but_putting_it_back(self):
        with patch("ctl.api.routes.services.in_progress.interrupted", return_value={"kind": "restore"}):
            response = self.post({"action": "start"})
            self.assertEqual(response.status_code, 409)
            self.assertIn("recovery_required", response.text)
            refused = self.post({"action": "keep_update"})
            self.assertEqual(refused.status_code, 409)
            self.assertIn("no interrupted update", refused.text)

    def test_nothing_changes_an_app_while_it_is_stopped_for_its_backup(self):
        with patch("ctl.api.routes.services.backup_service.holding", return_value=True):
            response = self.post({"action": "start"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("backup_running", response.text)

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
            store.return_value.active_jobs.return_value = []
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
