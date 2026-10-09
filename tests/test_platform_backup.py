"""R14 step 1: Mu3Lab's own state and its core apps are backed up; the household gets them back running."""

from __future__ import annotations

import json
import sqlite3
import stat
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import backups, job_guard, platform_backup
from ctl.api import create_app
from ctl.jobs import JobStore
from ctl.registry import load
from ctl.runtime import RuntimePaths
from tests import test_maintenance

ROOT = Path(__file__).resolve().parents[1]


class CoreAppTests(unittest.TestCase):
    def test_core_apps_are_ordered_by_dependency_and_downloads_are_left_out(self):
        included, skipped = platform_backup.core_apps(load(), ROOT)
        ids = [service.id for service in included]
        self.assertIn("authentik", ids)
        self.assertIn("vaultwarden", ids)
        self.assertNotIn("ingress", ids)  # no data of its own
        self.assertEqual([service.id for service, _reason in skipped], ["ollama"])
        self.assertIn("downloaded AI models", skipped[0][1])
        for position, service in enumerate(included):
            for dependency in service.dependencies:
                if dependency in ids:
                    self.assertLess(ids.index(dependency), position, f"{dependency} must start before {service.id}")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = RuntimePaths(Path(self.temp.name))
        self.paths.state.mkdir(parents=True)

    def test_the_export_is_a_consistent_private_copy_of_a_live_database(self):
        database = self.paths.state / "mu3lab.db"
        live = sqlite3.connect(database)
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE people (name TEXT)")
        live.execute("INSERT INTO people VALUES ('owner')")
        live.commit()  # still in the WAL, with the connection open
        (self.paths.state / "secrets.key").write_bytes(b"key")
        (self.paths.state / "deployments" / "mealie" / "b1").mkdir(parents=True)
        (self.paths.state / "deployments" / "mealie" / "b1" / "bundle.json").write_text("{}", encoding="utf-8")
        try:
            export = platform_backup.export_control_plane(self.paths)
        finally:
            live.close()
        with sqlite3.connect(export / "mu3lab.db") as copy:
            self.assertEqual(copy.execute("SELECT name FROM people").fetchall(), [("owner",)])
        self.assertEqual((export / "secrets.key").read_bytes(), b"key")
        self.assertTrue((export / "deployments" / "mealie" / "b1" / "bundle.json").is_file())
        self.assertEqual(stat.S_IMODE(export.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((export / "mu3lab.db").stat().st_mode), 0o600)
        manifest = json.loads((export / "export.json").read_text())
        self.assertIn("mu3lab.db", manifest["files"])


class _Job(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = RuntimePaths(Path(self.temp.name))
        self.paths.state.mkdir(parents=True)
        self.store = JobStore(self.paths.state / "mu3lab.db")
        self.events: list[str] = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("ctl.platform_backup.RuntimePaths", return_value=self.paths))
        self.stack.enter_context(patch("ctl.platform_backup.ControlState.runtime", return_value=self.state()))
        self.stack.enter_context(
            patch.object(platform_backup, "data_directories", side_effect=lambda service, _root: [Path(service.id)])
        )
        self.stack.enter_context(
            patch.object(
                platform_backup.maintenance,
                "stop_app",
                side_effect=lambda service, *_a: self.events.append(f"stop {service.id}"),
            )
        )
        self.stack.enter_context(
            patch.object(
                platform_backup.maintenance,
                "start_app",
                side_effect=lambda service, *_a: self.events.append(f"start {service.id}") or (True, ""),
            )
        )
        self.snapshot = self.stack.enter_context(
            patch.object(
                platform_backup.backups,
                "snapshot",
                side_effect=lambda service_id, *_a, **_k: self.events.append(f"backup {service_id}") or {},
            )
        )
        self.platform = self.stack.enter_context(
            patch.object(
                platform_backup.backups,
                "snapshot_source",
                side_effect=lambda source, *_a, **_k: (
                    self.events.append(f"backup {source}") or {"snapshot_id": "c" * 32}
                ),
            )
        )
        self.stack.enter_context(patch.object(platform_backup.backups, "check"))
        self.stack.enter_context(patch.object(platform_backup.backups, "prune"))

    def state(self):
        return None

    def run_job(self):
        job = self.store.create(
            kind="backup", service_id=platform_backup.SERVICE_ID, action=platform_backup.ACTION, actor="owner"
        )
        platform_backup.execute_claimed(self.store, self.store.get(job["id"]), "worker", ROOT)
        return self.store.get(job["id"])

    def core(self) -> list[str]:
        return [service.id for service in platform_backup.core_apps(load(), ROOT)[0]]


class PathsTests(unittest.TestCase):
    def test_backing_up_mu3lab_never_falls_back_to_the_real_runtime_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.state.mkdir()
            with patch.object(
                platform_backup.backups, "snapshot_source", return_value={"snapshot_id": "c" * 32}
            ) as saved:
                platform_backup._back_up_platform(paths, lambda _line: None)
        self.assertIs(saved.call_args.kwargs["paths"], paths)


class PlatformJobTests(_Job):
    def test_state_first_then_core_apps_stop_in_reverse_and_start_in_dependency_order(self):
        job = self.run_job()
        self.assertEqual(job["state"], "succeeded", job["detail"])
        core = self.core()
        self.assertEqual(self.events[0], "backup mu3lab")
        stops = [event.split()[1] for event in self.events if event.startswith("stop")]
        starts = [event.split()[1] for event in self.events if event.startswith("start")]
        self.assertEqual(stops, list(reversed(core)))
        self.assertEqual(starts, core)
        # Every core app is backed up while all of them are stopped.
        last_stop = max(i for i, event in enumerate(self.events) if event.startswith("stop"))
        first_start = min(i for i, event in enumerate(self.events) if event.startswith("start"))
        backed_up = [event.split()[1] for event in self.events[last_stop:first_start] if event.startswith("backup")]
        self.assertEqual(backed_up, core)
        self.assertNotIn("stop ollama", self.events)
        self.assertIn("Ollama is left out", job["detail"])
        self.assertFalse((self.paths.state / platform_backup.MARKER).exists())

    def test_one_failed_backup_still_restarts_every_core_app(self):
        def snapshot(service_id, *_args, **_kwargs):
            self.events.append(f"backup {service_id}")
            if service_id == "vaultwarden":
                raise backups.BackupError("disk full")
            return {}

        self.snapshot.side_effect = snapshot
        job = self.run_job()
        self.assertEqual(job["error_code"], "platform_backup_incomplete")
        self.assertIn("Vaultwarden was not backed up: disk full", job["detail"])
        self.assertEqual([event.split()[1] for event in self.events if event.startswith("start")], self.core())

    def test_an_interrupted_job_restarts_what_it_stopped(self):
        stops = []

        def stop(service, *_args):
            if len(stops) == 2:
                raise job_guard.JobInterrupted(job_guard.LEASE_LOST)
            stops.append(service.id)
            self.events.append(f"stop {service.id}")

        self.stack.enter_context(patch.object(platform_backup.maintenance, "stop_app", side_effect=stop))
        with self.assertRaises(job_guard.JobInterrupted):
            self.run_job()
        self.assertEqual(
            [event.split()[1] for event in self.events if event.startswith("start")], list(reversed(stops))
        )

    def test_a_worker_that_died_mid_backup_starts_the_apps_again_when_it_returns(self):
        (self.paths.state / platform_backup.MARKER).write_text(json.dumps({"stopped": ["lobehub", "authentik"]}))
        platform_backup.recover(ROOT, lambda _line: None, self.paths)
        self.assertEqual(self.events, ["start lobehub", "start authentik"])
        self.assertFalse((self.paths.state / platform_backup.MARKER).exists())


class OwnerStoppedTests(_Job):
    def state(self):
        return SimpleNamespace(
            installation=lambda service_id: {"state": "stopped"} if service_id == "lobehub" else None
        )

    def test_an_app_the_owner_stopped_is_backed_up_but_not_started(self):
        job = self.run_job()
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertIn("backup lobehub", self.events)
        self.assertNotIn("stop lobehub", self.events)
        self.assertNotIn("start lobehub", self.events)


class PlatformApiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("ctl.api.security.ingress_token", return_value="token"))
        self.stack.enter_context(patch("ctl.api.security.csrf_token", return_value="bound"))
        self.client = TestClient(create_app())
        self.headers = test_maintenance.MaintenanceApiTests.headers

    def test_an_operator_can_queue_it_once_and_read_its_status(self):
        store = JobStore.runtime()
        assert store is not None
        response = self.client.post("/api/v1/system/backups/platform", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200, response.text)
        job = store.get(response.json()["job"]["id"])
        self.addCleanup(store.transition, job["id"], "cancelled", actor="test")
        self.assertEqual((job["kind"], job["service_id"]), ("backup", platform_backup.SERVICE_ID))
        again = self.client.post("/api/v1/system/backups/platform", headers=self.headers, json={})
        # The same request again returns the queued job rather than a second backup.
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["job"]["id"], job["id"])
        status = self.client.get("/api/v1/system/backups/platform", headers=self.headers).json()
        self.assertEqual(status["platform"]["state"], "missing")
        self.assertTrue(any(app["id"] == "ollama" and not app["included"] for app in status["core_apps"]))


if __name__ == "__main__":
    unittest.main()
