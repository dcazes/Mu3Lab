"""Persistent Mu3Lab workflow worker.

The web process queues reviewed jobs but cannot execute Docker or host actions.
This process claims durable leases from SQLite and resumes abandoned work after
its previous worker lease expires.
"""

from __future__ import annotations

import os
import signal
import socket
import threading
import time
from pathlib import Path

from ctl.core_setup import execute_claimed
from ctl.jobs import JobStore

ROOT = Path(__file__).resolve().parent.parent
POLL_SECONDS = 2


def run() -> int:
    store = JobStore.runtime()
    if store is None:
        raise SystemExit("Mu3Lab runtime is not initialized")
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stopping = False

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        job = store.claim(worker_id)
        if job is None:
            time.sleep(POLL_SECONDS)
            continue
        heartbeat_stop = threading.Event()

        def maintain_lease() -> None:
            while not heartbeat_stop.wait(10):
                if not store.heartbeat(str(job["id"]), worker_id,
                                       step_id=str(job.get("step_id") or "")):
                    return

        heartbeat_thread = threading.Thread(target=maintain_lease, daemon=True)
        heartbeat_thread.start()
        try:
            execute_claimed(store, job, worker_id, ROOT)
        except Exception as exc:  # final containment for all future dispatchers
            try:
                store.transition(str(job["id"]), "failed", actor=worker_id,
                                 detail=f"Worker stopped safely: {exc}",
                                 error_code="worker_failure")
            except (KeyError, ValueError):
                pass
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
