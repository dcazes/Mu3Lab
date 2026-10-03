"""Background image downloads for install batches.

Runs inside the worker next to the job loop. Each running batch downloads up
to its ``parallel_downloads`` apps at once, in the owner's order; when an
app's images are present it becomes ready and the batch sets it up (one setup
at a time, through the ordinary job queue). Pausing stops a download but
keeps its finished ranges, so resuming continues where it left off.
"""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ctl import image_fetch
from ctl.image_downloads import ImageDownloadStore
from ctl.install_batches import InstallBatchStore
from ctl.jobs import JobStore
from ctl.registry import ROOT, RegistryError
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths

Log = Callable[[str], None]
TICK_SECONDS = 1.0
SIZES_EVERY = 600.0


def declared_images(compose_file: Path) -> list[str]:
    """Registry images a curated Compose file names literally.

    Locally built services and images chosen through variables are left to
    the setup step, which resolves the full runtime project first.
    """
    definition = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
    images = []
    for service in (definition.get("services") or {}).values():
        if not isinstance(service, dict) or service.get("build"):
            continue
        image = str(service.get("image") or "")
        if image and "$" not in image:
            images.append(image)
    return list(dict.fromkeys(images))


def service_images(service_id: str) -> list[str]:
    try:
        return declared_images(load_registry().get(service_id).compose_path(ROOT) / "docker-compose.yml")
    except (RegistryError, OSError, yaml.YAMLError):
        return []


def progress_key(batch_id: str, ordinal: int) -> str:
    return f"prefetch:{batch_id}:{ordinal}"


class DownloadManager:
    def __init__(
        self,
        batches: InstallBatchStore,
        jobs: JobStore,
        downloads: ImageDownloadStore | None,
        cache: Path,
        log: Log,
        *,
        images_for: Callable[[str], list[str]] = service_images,
        fetch: Callable[..., Any] = image_fetch.fetch_images,
    ) -> None:
        self.batches = batches
        self.jobs = jobs
        self.downloads = downloads
        self.cache = cache
        self.log = log
        self.images_for = images_for
        self.fetch = fetch
        self.active: dict[tuple[str, int], tuple[threading.Thread, threading.Event]] = {}
        # Set when downloads change what this server holds, so sizes are re-measured.
        self.sizes_stale = threading.Event()
        self.sizes_stale.set()

    @classmethod
    def runtime(cls, log: Log) -> DownloadManager | None:
        batches, jobs = InstallBatchStore.runtime(), JobStore.runtime()
        if batches is None or jobs is None:
            return None
        return cls(batches, jobs, ImageDownloadStore.runtime(), RuntimePaths().runtime / "image-cache", log)

    def tick(self) -> None:
        queue = self.batches.download_queue()
        wanted = {(str(item["batch_id"]), int(item["ordinal"])): item for item in queue}
        for key, (thread, stop) in list(self.active.items()):
            if not thread.is_alive():
                del self.active[key]
            elif key not in wanted or wanted[key]["download_state"] == "paused":
                stop.set()
        running: dict[str, int] = {}
        for batch_id, _ordinal in self.active:
            running[batch_id] = running.get(batch_id, 0) + 1
        for key, item in wanted.items():
            batch_id = key[0]
            if key in self.active or item["download_state"] == "paused":
                continue
            if running.get(batch_id, 0) >= int(item["parallel_downloads"]):
                continue
            stop = threading.Event()
            thread = threading.Thread(target=self._download, args=(item, stop), daemon=True)
            self.active[key] = (thread, stop)
            running[batch_id] = running.get(batch_id, 0) + 1
            thread.start()

    def _download(self, item: dict[str, Any], stop: threading.Event) -> None:
        batch_id, ordinal, service_id = str(item["batch_id"]), int(item["ordinal"]), str(item["service_id"])
        workdir = self.cache / f"{batch_id}-{ordinal}"
        self.batches.set_download_state(batch_id, ordinal, "downloading")

        def report(snapshot: dict[str, Any]) -> None:
            if self.downloads:
                self.downloads.update(service_id, progress_key(batch_id, ordinal), snapshot)

        def log(line: str) -> None:
            self.log(f"[{service_id}] {line}")

        try:
            self.fetch(self.images_for(service_id), workdir, log, report=report, stop=stop)
            self.batches.set_download_state(batch_id, ordinal, "ready")
        except image_fetch.Paused:
            if self.batches.download_state(batch_id, ordinal) != "paused":
                shutil.rmtree(workdir, ignore_errors=True)  # cancelled: nothing to resume
            return
        except Exception as exc:  # the setup step retries with Docker's own pull
            log(f"Download will be retried during setup: {exc}")
            self.batches.set_download_state(batch_id, ordinal, "ready", error=str(exc))
        shutil.rmtree(workdir, ignore_errors=True)
        self.sizes_stale.set()
        self.batches.continue_batch(batch_id, self.jobs)

    def measure_sizes(self) -> None:
        """Re-measure app sizes for the Apps page (network only; never blocks downloads)."""
        from ctl import app_sizes

        store = app_sizes.AppSizeStore(self.batches.database)
        try:
            registry = load_registry()
            services = {
                service.id: self.images_for(service.id) for service in registry.services if service.stage == "optional"
            }
            app_sizes.refresh(services, store, self.cache / "sizes", self.log)
        except Exception as exc:  # sizes are informational; try again later
            self.log(f"Mu3Lab app size check deferred: {exc}")

    def run(self, stopping: threading.Event) -> None:
        sizes: threading.Thread | None = None
        next_sizes = 0.0
        while not stopping.is_set():
            try:
                self.tick()
            except Exception as exc:  # keep the manager alive; the next tick retries
                self.log(f"Mu3Lab download manager deferred: {exc}")
            idle = sizes is None or not sizes.is_alive()
            if idle and (self.sizes_stale.is_set() or time.monotonic() >= next_sizes):
                self.sizes_stale.clear()
                next_sizes = time.monotonic() + SIZES_EVERY
                sizes = threading.Thread(target=self.measure_sizes, name="mu3lab-sizes", daemon=True)
                sizes.start()
            stopping.wait(TICK_SECONDS)
        for _thread, stop in self.active.values():
            stop.set()


def start(log: Log, stopping: threading.Event) -> threading.Thread | None:
    """Start the manager once the runtime exists; returns its thread."""

    def loop() -> None:
        manager = None
        while manager is None and not stopping.is_set():
            manager = DownloadManager.runtime(log)
            if manager is None:
                time.sleep(2)
        if manager:
            manager.run(stopping)

    thread = threading.Thread(target=loop, name="mu3lab-downloads", daemon=True)
    thread.start()
    return thread
