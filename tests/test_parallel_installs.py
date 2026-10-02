"""Parallel downloads with one-at-a-time setup for install batches."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.control_state import ControlState
from ctl.download_manager import DownloadManager, declared_images, progress_key
from ctl.image_downloads import ImageDownloadStore
from ctl.image_fetch import FetchError, Paused
from ctl.install_batches import InstallBatchStore
from ctl.jobs import JobStore
from ctl.registry import load
from ctl.runtime import RuntimePaths

IDENTITY = {"owner_uid": "uid", "email": "o@example.test", "username": "operator", "display_name": "Operator"}
APPS = ["paperless-ngx", "adventurelog", "mealie"]


class BatchCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        paths = RuntimePaths(self.root)
        paths.runtime.mkdir(parents=True)
        database = paths.runtime / "control-plane.sqlite3"
        self.control = ControlState(database)
        self.jobs = JobStore(database)
        self.batches = InstallBatchStore(database)
        self.downloads = ImageDownloadStore(database)
        for target in (
            "ctl.install_batches.workflow_secrets.save_job_identity",
            "ctl.install_batches.workflow_secrets.delete_by_job",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("ctl.install_batches.workflow_secrets.job_identity", return_value=IDENTITY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def create(self, apps=APPS, parallel: int = 3) -> dict:
        return self.batches.create(
            load(),
            apps,
            actor="operator",
            owner_uid="uid",
            identity=IDENTITY,
            idempotency_key="",
            jobs=self.jobs,
            control=self.control,
            parallel_downloads=parallel,
        )

    def ordinal(self, batch: dict, service_id: str) -> int:
        return next(item["ordinal"] for item in batch["items"] if item["service_id"] == service_id)

    def ready(self, batch: dict, service_id: str) -> None:
        self.batches.set_download_state(batch["id"], self.ordinal(batch, service_id), "ready")
        self.batches.continue_batch(batch["id"], self.jobs)

    def states(self, batch: dict) -> dict[str, str]:
        return {item["service_id"]: item["state"] for item in self.batches.get(batch["id"])["items"]}

    def finish(self, batch: dict, service_id: str, state: str = "succeeded") -> None:
        item = next(item for item in self.batches.get(batch["id"])["items"] if item["service_id"] == service_id)
        self.jobs.transition(item["job_id"], "running", actor="worker")
        self.jobs.transition(item["job_id"], state, actor="worker", detail=state)
        self.batches.advance_for_job(item["job_id"], self.jobs)


class SchedulingTests(BatchCase):
    def test_new_selections_append_to_the_active_queue(self):
        batch = self.create(["mealie"], parallel=1)
        self.batches.set_download_state(batch["id"], 0, "downloading")
        extended = self.create(["mealie", "paperless-ngx"], parallel=2)
        self.assertEqual(extended["id"], batch["id"])
        self.assertEqual([item["service_id"] for item in extended["items"]], ["mealie", "paperless-ngx"])
        self.assertEqual(extended["items"][0]["download_state"], "downloading")
        self.assertEqual(extended["items"][1]["ordinal"], 1)
        self.assertEqual(extended["parallel_downloads"], 2)
        with self.assertRaisesRegex(ValueError, "already queued"):
            self.create(["mealie"])

    def test_duplicate_app_in_another_owners_queue_is_rejected(self):
        self.create(["mealie"])
        with self.assertRaisesRegex(ValueError, "another installation queue"):
            self.batches.create(
                load(),
                ["mealie"],
                actor="other",
                owner_uid="other",
                identity=IDENTITY,
                idempotency_key="",
                jobs=self.jobs,
                control=self.control,
            )

    def test_whichever_app_finishes_downloading_first_is_set_up_first(self):
        batch = self.create()
        self.assertEqual(batch["state"], "running")
        self.assertEqual(batch["parallel_downloads"], 3)
        self.ready(batch, "mealie")
        self.assertEqual(self.states(batch)["mealie"], "queued")
        # Another download finishing does not start a second setup at the same time.
        self.ready(batch, "paperless-ngx")
        self.assertEqual(
            self.states(batch), {"paperless-ngx": "pending", "adventurelog": "pending", "mealie": "queued"}
        )
        self.assertEqual(len([job for job in self.jobs.jobs() if job["state"] == "queued"]), 1)
        self.finish(batch, "mealie")
        self.assertEqual(self.states(batch)["paperless-ngx"], "queued")

    def test_the_owners_order_decides_among_ready_apps(self):
        batch = self.create()
        self.batches.reorder(batch["id"], ["mealie", "adventurelog", "paperless-ngx"])
        for app in APPS:
            self.batches.set_download_state(batch["id"], self.ordinal(batch, app), "ready")
        self.batches.continue_batch(batch["id"], self.jobs)
        self.assertEqual(self.states(batch)["mealie"], "queued")
        self.assertEqual(self.batches.get(batch["id"])["items"][0]["service_id"], "mealie")

    def test_an_app_waits_for_a_dependency_in_the_same_batch(self):
        batch = self.create(["paperless-ngx", "adventurelog"])
        with self.batches._connect() as conn:
            conn.execute(
                "UPDATE install_batch_items SET depends_on_json = ? WHERE batch_id = ? AND service_id = ?",
                (json.dumps(["paperless-ngx"]), batch["id"], "adventurelog"),
            )
        self.ready(batch, "adventurelog")
        self.assertEqual(self.states(batch), {"paperless-ngx": "pending", "adventurelog": "pending"})
        self.ready(batch, "paperless-ngx")
        self.assertEqual(self.states(batch)["paperless-ngx"], "queued")
        self.finish(batch, "paperless-ngx")
        self.assertEqual(self.states(batch)["adventurelog"], "queued")

    def test_the_batch_finishes_only_when_every_app_is_done(self):
        batch = self.create(["paperless-ngx", "adventurelog"])
        self.ready(batch, "paperless-ngx")
        self.finish(batch, "paperless-ngx", "failed")
        self.assertEqual(self.batches.get(batch["id"])["state"], "running")  # adventurelog still downloading
        self.ready(batch, "adventurelog")
        self.finish(batch, "adventurelog")
        final = self.batches.get(batch["id"])
        self.assertEqual(final["state"], "completed_with_failures")
        self.assertEqual(final["error"]["message"], "failed")

    def test_calling_continue_twice_never_starts_two_setups(self):
        batch = self.create()
        for app in APPS:
            self.batches.set_download_state(batch["id"], self.ordinal(batch, app), "ready")
        threads = [
            threading.Thread(target=self.batches.continue_batch, args=(batch["id"], self.jobs)) for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(list(self.states(batch).values()).count("queued"), 1)


class ControlTests(BatchCase):
    def test_pause_and_resume_a_download(self):
        batch = self.create()
        paused = self.batches.pause_download(batch["id"], "mealie")
        item = next(item for item in paused["items"] if item["service_id"] == "mealie")
        self.assertEqual(item["download_state"], "paused")
        # The manager marking it as downloading does not undo the owner's pause.
        self.batches.set_download_state(batch["id"], item["ordinal"], "downloading")
        self.assertEqual(self.batches.download_state(batch["id"], item["ordinal"]), "paused")
        with self.assertRaises(ValueError):
            self.batches.pause_download(batch["id"], "mealie")
        self.batches.resume_download(batch["id"], "mealie")
        self.assertEqual(self.batches.download_state(batch["id"], item["ordinal"]), "")

    def test_apps_already_being_set_up_keep_their_place(self):
        batch = self.create()
        self.ready(batch, "paperless-ngx")
        self.batches.reorder(batch["id"], ["mealie", "adventurelog", "paperless-ngx"])
        items = {item["service_id"]: item for item in self.batches.get(batch["id"])["items"]}
        self.assertEqual(items["paperless-ngx"]["priority"], 0)
        self.assertEqual(items["mealie"]["priority"], 0)
        self.assertEqual(items["adventurelog"]["priority"], 1)
        with self.assertRaises(ValueError):
            self.batches.reorder(batch["id"], ["mealie", "not-an-app"])

    def test_downloads_at_once_is_kept_within_limits(self):
        batch = self.create(parallel=40)
        self.assertEqual(batch["parallel_downloads"], 8)
        self.assertEqual(self.batches.set_parallel_downloads(batch["id"], 0)["parallel_downloads"], 1)


class FakeFetch:
    """Stands in for image_fetch.fetch_images; each app finishes when released."""

    def __init__(self) -> None:
        self.release: dict[str, threading.Event] = {}
        self.running: set[str] = set()
        self.most = 0
        self.fail: set[str] = set()
        self.lock = threading.Lock()

    def __call__(self, images, _cache, _log, *, report=None, stop=None):
        app = images[0]
        gate = self.release.setdefault(app, threading.Event())
        with self.lock:
            self.running.add(app)
            self.most = max(self.most, len(self.running))
        try:
            while not gate.wait(0.01):
                if stop.is_set():
                    raise Paused
            if report:
                report({"state": "done", "total_bytes": 1, "done_bytes": 1})
            if app in self.fail:
                raise FetchError("registry unreachable")
        finally:
            with self.lock:
                self.running.discard(app)


class ManagerTests(BatchCase):
    def manager(self, fetch: FakeFetch) -> DownloadManager:
        manager = DownloadManager(
            self.batches,
            self.jobs,
            self.downloads,
            self.root / "cache",
            lambda _line: None,
            images_for=lambda app: [app],
            fetch=fetch,
        )

        def stop_downloads():
            threads = list(manager.active.values())
            for _thread, stop in threads:
                stop.set()
            for thread, _stop in threads:
                thread.join(timeout=3)

        self.addCleanup(stop_downloads)
        return manager

    def settle(self, manager: DownloadManager, until, seconds: float = 3.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            manager.tick()
            if until():
                return
            time.sleep(0.02)
        self.fail("condition not reached")

    def test_downloads_run_in_parallel_up_to_the_limit_and_setup_follows(self):
        fetch = FakeFetch()
        batch = self.create(parallel=2)
        manager = self.manager(fetch)
        self.settle(manager, lambda: len(fetch.running) == 2)
        manager.tick()
        self.assertEqual(fetch.most, 2)
        first = sorted(fetch.running)[0]
        fetch.release[first].set()
        self.settle(manager, lambda: self.states(batch)[first] == "queued")
        self.settle(manager, lambda: len(fetch.running) == 2)  # the third app starts downloading
        for event in list(fetch.release.values()):
            event.set()
        self.settle(
            manager, lambda: all(item["download_state"] == "ready" for item in self.batches.get(batch["id"])["items"])
        )
        self.assertEqual(list(self.states(batch).values()).count("queued"), 1)
        self.assertEqual(
            self.downloads.for_jobs([progress_key(batch["id"], 0)])[progress_key(batch["id"], 0)]["state"], "done"
        )

    def test_appended_apps_download_after_the_existing_queue(self):
        fetch = FakeFetch()
        batch = self.create(["mealie"], parallel=1)
        manager = self.manager(fetch)
        self.settle(manager, lambda: fetch.running == {"mealie"})
        extended = self.create(["paperless-ngx"], parallel=1)
        self.assertEqual(extended["id"], batch["id"])
        manager.tick()
        self.assertEqual(fetch.running, {"mealie"})
        fetch.release["mealie"].set()
        self.settle(manager, lambda: fetch.running == {"paperless-ngx"})
        self.assertEqual(fetch.most, 1)

    def test_pausing_stops_the_download_and_resuming_restarts_it(self):
        fetch = FakeFetch()
        batch = self.create(["paperless-ngx"], parallel=1)
        manager = self.manager(fetch)
        self.settle(manager, lambda: fetch.running == {"paperless-ngx"})
        self.batches.pause_download(batch["id"], "paperless-ngx")
        self.settle(manager, lambda: not fetch.running and not manager.active)
        self.assertEqual(self.batches.download_state(batch["id"], 0), "paused")
        self.batches.resume_download(batch["id"], "paperless-ngx")
        self.settle(manager, lambda: fetch.running == {"paperless-ngx"})
        fetch.release["paperless-ngx"].set()
        self.settle(manager, lambda: self.states(batch)["paperless-ngx"] == "queued")

    def test_a_failed_download_still_goes_to_setup_which_retries_with_docker(self):
        fetch = FakeFetch()
        fetch.fail.add("paperless-ngx")
        fetch.release.setdefault("paperless-ngx", threading.Event()).set()
        batch = self.create(["paperless-ngx"], parallel=1)
        manager = self.manager(fetch)
        self.settle(manager, lambda: self.states(batch)["paperless-ngx"] == "queued")
        item = self.batches.get(batch["id"])["items"][0]
        self.assertIn("registry unreachable", item["download_error"])

    def test_cancelling_the_batch_stops_its_downloads(self):
        fetch = FakeFetch()
        batch = self.create(["paperless-ngx"], parallel=1)
        manager = self.manager(fetch)
        self.settle(manager, lambda: fetch.running == {"paperless-ngx"})
        self.batches.cancel(batch["id"], self.jobs)
        self.settle(manager, lambda: not fetch.running)


class DeclaredImageTests(unittest.TestCase):
    def test_lists_registry_images_but_not_local_builds_or_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose = Path(tmp) / "docker-compose.yml"
            compose.write_text(
                "services:\n"
                "  app: {image: ghcr.io/a/b:1}\n"
                "  worker: {image: ghcr.io/a/b:1}\n"
                "  db: {image: 'postgres:16'}\n"
                "  built: {build: ., image: local/app}\n"
                "  chosen: {image: 'ghcr.io/x/y:${TAG}'}\n",
                encoding="utf-8",
            )
            self.assertEqual(declared_images(compose), ["ghcr.io/a/b:1", "postgres:16"])

    def test_every_curated_app_declares_downloadable_images(self):
        from ctl.download_manager import service_images

        for app in APPS:
            with self.subTest(app=app):
                self.assertTrue(service_images(app))


if __name__ == "__main__":
    unittest.main()


class AppSizeTests(unittest.TestCase):
    def layers(self, *items):
        return [
            {"digest": digest, "size": size, "unpacked": size * 3, "present": present}
            for digest, size, present in items
        ]

    def test_a_selection_counts_shared_layers_once_and_skips_what_is_here(self):
        from ctl.app_sizes import summarize

        sizes = {
            "a": {"layers": self.layers(("sha256:base", 100, False), ("sha256:a", 50, False))},
            "b": {"layers": self.layers(("sha256:base", 100, False), ("sha256:b", 20, True))},
        }
        self.assertEqual(
            summarize(sizes, ["a", "b"]),
            {"download_bytes": 170, "needed_bytes": 150, "disk_bytes": 680, "needed_disk_bytes": 600},
        )

    def test_the_unpacked_size_comes_from_the_gzip_trailer(self):
        import gzip
        import struct

        import httpx

        from ctl.app_sizes import unpacked_size

        data = gzip.compress(b"x" * 100_000)

        class OneBlob:
            def blob_location(self, _digest):
                return "https://cdn.test/blob", {}

        def handler(request: httpx.Request) -> httpx.Response:
            start, end = (int(part) for part in request.headers["range"].removeprefix("bytes=").split("-"))
            return httpx.Response(206, content=data[start : end + 1])

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(unpacked_size(client, OneBlob(), "sha256:x", len(data)), 100_000)
        self.assertEqual(struct.unpack("<I", data[-4:])[0], 100_000)

    def test_the_sizes_endpoint_reports_apps_and_the_selection(self):
        from fastapi.testclient import TestClient

        from ctl.api import create_app
        from ctl.app_sizes import AppSizeStore

        with tempfile.TemporaryDirectory() as tmp:
            store = AppSizeStore(Path(tmp) / "db.sqlite3")
            store.save("mealie", self.layers(("sha256:m", 400, False)))
            store.save("surfsense", self.layers(("sha256:s", 2000, True)))
            headers = {
                "x-mu3lab-proxy-token": "real-token",
                "x-authentik-username": "owner",
                "x-authentik-uid": "subject",
                "x-authentik-groups": "mu3lab-operators",
                "host": "testserver",
            }
            with (
                patch("ctl.api.security.ingress_token", return_value="real-token"),
                patch("ctl.api.routes.install_batches.AppSizeStore.runtime", return_value=store),
            ):
                response = TestClient(create_app()).get(
                    "/api/v1/app-sizes", params={"ids": "mealie,surfsense"}, headers=headers
                )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["apps"]["mealie"]["needed_bytes"], 400)
        self.assertEqual(body["apps"]["surfsense"]["needed_bytes"], 0)
        self.assertEqual(body["selection"]["needed_bytes"], 400)
        self.assertTrue(body["measured"])
        self.assertGreater(body["free_bytes"], 0)
