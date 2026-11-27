"""Faster app updates: side by side when unrelated, downloads before their turn, and cancellable."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import image_fetch, job_guard, update_downloads, worker
from ctl.jobs import JobStore
from ctl.lifecycle import builds, update
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths


def _job(service_id: str, action: str = "update") -> dict:
    return {"id": f"job-{service_id}", "kind": "lifecycle", "service_id": service_id, "action": action}


class SideBySideTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def together(self, candidate: dict, running: list[dict]) -> bool:
        with patch("ctl.registry.load", return_value=self.registry):
            return worker._together(candidate, running)

    def test_unrelated_catalog_apps_update_side_by_side(self):
        self.assertTrue(self.together(_job("mealie"), [_job("wallos")]))
        self.assertTrue(self.together(_job("outline"), [_job("wallos"), _job("mealie")]))

    def test_no_more_than_three_at_once(self):
        running = [_job("wallos"), _job("mealie"), _job("outline")]
        self.assertFalse(self.together(_job("homebox"), running))

    def test_apps_that_work_together_take_turns(self):
        # Sonarr needs qBittorrent; Seerr works with Jellyfin's videos.
        self.assertFalse(self.together(_job("sonarr"), [_job("qbittorrent")]))
        self.assertFalse(self.together(_job("seerr"), [_job("jellyfin")]))

    def test_core_chat_and_backup_apps_update_alone(self):
        for alone in ("litellm", "open-webui", "lobehub", "kopia-ui"):
            with self.subTest(alone):
                self.assertFalse(self.together(_job(alone), [_job("wallos")]))
                self.assertFalse(self.together(_job("wallos"), [_job(alone)]))

    def test_anything_but_an_update_waits_for_the_running_ones(self):
        self.assertFalse(self.together(_job("mealie", "restart"), [_job("wallos")]))
        self.assertFalse(self.together(_job("mealie"), [_job("wallos", "install")]))


class ClaimOrderTests(unittest.TestCase):
    def test_a_job_that_may_not_run_beside_others_holds_the_ones_behind_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "db.sqlite3")
            first = store.create(kind="lifecycle", service_id="mealie", action="restart", actor="owner")
            store.create(kind="lifecycle", service_id="wallos", action="update", actor="owner")
            self.assertIsNone(store.claim("worker", accept=lambda row: row["action"] == "update"))
            claimed = store.claim("worker")
        self.assertEqual(claimed["id"], first["id"])


class CancelTests(unittest.TestCase):
    def store(self, tmp: str) -> JobStore:
        return JobStore(Path(tmp) / "db.sqlite3")

    def test_waiting_and_downloading_updates_can_be_cancelled_but_switching_ones_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.store(tmp)
            job = store.create(kind="lifecycle", service_id="mealie", action="update", actor="owner")
            self.assertTrue(update.cancellable(store, store.get(job["id"])))
            store.claim("worker")
            self.assertTrue(update.cancellable(store, store.get(job["id"])))
            store.append_event(job["id"], "stage", "download_images: Downloading Mealie v3.")
            self.assertTrue(update.cancellable(store, store.get(job["id"])))
            store.append_event(job["id"], "stage", "backup: Stopping Mealie and saving a backup.")
            self.assertFalse(update.cancellable(store, store.get(job["id"])))


class UpdateDownloadTests(unittest.TestCase):
    def setUp(self):
        update_downloads._downloads.clear()
        self.started: list[str] = []
        self.release = threading.Event()

        def fetch(service_id, _job_id, _images, _log, stop):
            self.started.append(service_id)
            while not self.release.wait(0.01):
                if stop.is_set():
                    raise image_fetch.Paused()

        patcher = patch.object(update_downloads, "fetch", side_effect=fetch)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.release.set)
        self.addCleanup(update_downloads._downloads.clear)

    def queue(self, store: JobStore, *apps: str) -> list[dict]:
        return [store.create(kind="lifecycle", service_id=app, action="update", actor="owner") for app in apps]

    def settle(self) -> None:
        for _ in range(200):
            if len(self.started) >= min(len(update_downloads._downloads), update_downloads.AT_ONCE):
                return
            threading.Event().wait(0.01)

    def test_queued_updates_download_a_few_at_once_and_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "db.sqlite3")
            update_downloads.tick(store, lambda _id: ("image@sha256:x",), lambda _line: None)
            self.assertEqual(self.started, [])  # nothing asked for, nothing downloaded
            self.queue(store, "wallos", "mealie", "outline", "homebox")
            update_downloads.tick(store, lambda _id: ("image@sha256:x",), lambda _line: None)
            self.settle()
            self.assertEqual(sorted(self.started), ["mealie", "outline", "wallos"])

    def test_cancelling_a_waiting_update_stops_its_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "db.sqlite3")
            (job,) = self.queue(store, "wallos")
            update_downloads.tick(store, lambda _id: ("image@sha256:x",), lambda _line: None)
            self.settle()
            download = update_downloads._downloads[job["id"]]
            store.cancel(job["id"], actor="owner")
            update_downloads.tick(store, lambda _id: ("image@sha256:x",), lambda _line: None)
            self.assertTrue(download.done.wait(2))
            self.assertEqual(download.error, "stopped")

    def test_the_update_waits_for_its_own_download_instead_of_starting_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "db.sqlite3")
            (job,) = self.queue(store, "wallos")
            update_downloads.tick(store, lambda _id: ("image@sha256:x",), lambda _line: None)
            self.settle()
            self.release.set()
            update_downloads.ensure("wallos", job["id"], ("image@sha256:x",), lambda _line: None)
        self.assertEqual(self.started, ["wallos"])
        self.assertNotIn(job["id"], update_downloads._downloads)

    def test_a_cancelled_update_stops_waiting_and_its_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "db.sqlite3")
            (job,) = self.queue(store, "wallos")
            claimed = store.claim("worker")
            store.cancel(claimed["id"], actor="owner")
            with job_guard.executing(store, job["id"], "worker"), self.assertRaises(job_guard.JobInterrupted):
                update_downloads.ensure("wallos", job["id"], ("image@sha256:x",), lambda _line: None)
            download = update_downloads._downloads[job["id"]]
            self.assertTrue(download.done.wait(2))
            self.assertEqual(download.error, "stopped")


class BuildTests(unittest.TestCase):
    def test_own_images_are_rebuilt_only_when_their_files_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "root")
            project = Path(tmp) / "project"
            (project / "agent").mkdir(parents=True)
            (project / "docker-compose.yml").write_text(
                "services:\n  agent:\n    build: ./agent\n  db:\n    image: x\n"
            )
            (project / "agent" / "Dockerfile").write_text("FROM scratch\n")
            self.assertTrue(builds.needed("livekit", project, paths))  # never built here: build
            builds.remember("livekit", project, paths)
            self.assertFalse(builds.needed("livekit", project, paths))
            (project / "agent" / "main.py").write_text("print('new')\n")
            self.assertTrue(builds.needed("livekit", project, paths))
            builds.forget("livekit", paths)
            self.assertTrue(builds.needed("livekit", project, paths))

    def test_an_app_that_builds_nothing_never_rebuilds(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "docker-compose.yml").write_text("services:\n  app:\n    image: x\n")
            self.assertFalse(builds.needed("mealie", project, RuntimePaths(Path(tmp) / "root")))


if __name__ == "__main__":
    unittest.main()
