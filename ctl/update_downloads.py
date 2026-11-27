"""Download the images of the updates someone asked for, several apps at once, before their turn.

Only a queued update job is ever downloaded for: an update that is merely
available downloads nothing until someone presses Update (or Update all).
The download manager (``ctl.download_manager``) starts up to ``AT_ONCE``
of them in the background, oldest first, while the apps keep running. When an
update job reaches its download step it waits for its own download instead of
starting a second one, or starts it then if none ran yet.

Each download uses the fast, verified fetch the installer uses
(``ctl.image_fetch``) and falls back to Docker's own pull for anything that
fetch could not get. Progress goes to the image-download store under the
update job's id, so the app's progress card shows it.

Cancelling a waiting or downloading update stops its download: a cancelled
job is no longer queued or running, and the next tick stops what it started.
A download never changes a running app, so stopping one is always safe.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ctl import actions, image_fetch, job_guard
from ctl.image_downloads import ImageDownloadStore
from ctl.jobs import JobStore, redact
from ctl.runtime import RuntimePaths
from ctl.status import signals

Log = Callable[[str], None]
# Downloads at once. ``image_fetch`` also caps the connections all of them share.
AT_ONCE = 3


class DownloadFailed(RuntimeError):
    """An image could not be downloaded; the message is safe to show."""


@dataclass
class _Download:
    service_id: str
    images: tuple[str, ...]
    stop: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    error: str = ""
    thread: threading.Thread | None = None


_lock = threading.Lock()
_downloads: dict[str, _Download] = {}  # by update job id


def _cache(job_id: str) -> Path:
    return RuntimePaths().runtime / "image-cache" / f"update-{job_id[:16]}"


def _needed(images: tuple[str, ...]) -> list[str]:
    """Images not here yet. A digest-pinned image already present is exactly the one needed."""
    return [image for image in images if not ("@sha256:" in image and actions.docker_image_present(image))]


def fetch(service_id: str, job_id: str, images: tuple[str, ...], log: Log, stop: threading.Event) -> None:
    """Make every image present. Raises ``image_fetch.Paused`` when stopped, ``DownloadFailed`` on failure."""
    missing = _needed(images)
    store = ImageDownloadStore.runtime()

    def report(snapshot: dict) -> None:
        if store:
            store.update(service_id, job_id, snapshot)

    if not missing:
        report({"state": "done", "images_total": len(images), "images_done": len(images)})
        return
    cache = _cache(job_id)
    try:
        image_fetch.fetch_images(missing, cache, log, report=report, stop=stop)
    except image_fetch.Paused:
        raise
    except Exception as exc:  # the fast path could not finish: Docker's own pull gets the rest
        log(f"The fast download stopped ({redact(str(exc))[-200:]}); Docker downloads the rest.")
    finally:
        shutil.rmtree(cache, ignore_errors=True)
    for image in _needed(tuple(missing)):
        if stop.is_set():
            raise image_fetch.Paused()
        rc, output = actions.docker_cmd(["docker", "pull", image], log, timeout=3600)
        if rc:
            raise DownloadFailed(f"{image} could not be downloaded: {output[-300:]}")
    report({"state": "done", "images_total": len(images), "images_done": len(images)})


def _run(job_id: str, download: _Download, log: Log) -> None:
    try:
        fetch(download.service_id, job_id, download.images, log, download.stop)
    except image_fetch.Paused:
        download.error = "stopped"
    except Exception as exc:  # reported to the job that waits for it
        download.error = redact(str(exc)) or "the download failed"
    finally:
        download.done.set()
        signals.wake()  # the job waiting for it goes on; the manager starts the next download


def _start(job_id: str, service_id: str, images: tuple[str, ...], log: Log) -> _Download:
    """Start a download for the job (the caller holds ``_lock``)."""
    download = _Download(service_id, images)
    download.thread = threading.Thread(
        target=_run, args=(job_id, download, log), name=f"mu3lab-update-download-{service_id}", daemon=True
    )
    _downloads[job_id] = download
    download.thread.start()
    return download


def _job_log(store: JobStore, job_id: str) -> Log:
    def log(line: str) -> None:
        try:
            store.append_event(job_id, "log", line)
        except Exception:  # the job may have been cancelled and pruned meanwhile; the download is harmless
            pass

    return log


def tick(store: JobStore, images_for: Callable[[str], tuple[str, ...]], log: Log) -> None:
    """Stop downloads of cancelled updates, then start queued ones, up to ``AT_ONCE`` at a time."""
    jobs = store.active_jobs()
    live = {str(job["id"]) for job in jobs}
    # ``active_jobs`` is newest first (ties in the order asked); reversed and sorted stably, oldest first.
    queued = sorted(
        (job for job in reversed(jobs) if job.get("action") == "update" and job.get("state") == "queued"),
        key=lambda job: str(job.get("created_at") or ""),
    )
    with _lock:
        for job_id, download in list(_downloads.items()):
            if job_id not in live:
                download.stop.set()  # cancelled, or finished without waiting for it
                if download.done.is_set():
                    del _downloads[job_id]
        busy = sum(not download.done.is_set() for download in _downloads.values())
        for job in queued:
            if busy >= AT_ONCE:
                break
            job_id, service_id = str(job["id"]), str(job.get("service_id") or "")
            if job_id in _downloads:
                continue
            try:
                images = images_for(service_id)
            except Exception as exc:  # the job itself reports what is wrong with the app
                log(f"[{service_id}] Its update's images are downloaded when its turn comes: {exc}")
                images = ()
            if not images:
                continue
            _start(job_id, service_id, images, _job_log(store, job_id))
            busy += 1


def busy() -> bool:
    with _lock:
        return any(not download.done.is_set() for download in _downloads.values())


def ensure(service_id: str, job_id: str, images: tuple[str, ...], log: Log) -> None:
    """Called by the update job: wait for its download (starting it if none ran), or raise.

    Checks for a cancel request every second; a cancelled update stops its download.
    Raises ``DownloadFailed`` with a message safe to show.
    """
    with _lock:
        download = _downloads.get(job_id)
        if download and download.done.is_set() and download.error:
            # It stopped or failed while waiting its turn: try once more now.
            del _downloads[job_id]
            download = None
        if download is None:
            download = _start(job_id, service_id, images, log)
    try:
        while not download.done.wait(1.0):
            job_guard.checkpoint()
    except BaseException:
        download.stop.set()
        raise
    finally:
        if download.done.is_set():
            with _lock:
                if _downloads.get(job_id) is download:
                    del _downloads[job_id]
    if download.error:
        raise DownloadFailed(download.error)
