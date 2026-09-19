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
from ctl.service_ops import execute_claimed as execute_service_claimed
from ctl.mcp_ops import execute_claimed as execute_mcp_claimed
from ctl.provider_ops import execute_claimed as execute_provider_claimed

ROOT = Path(__file__).resolve().parent.parent
POLL_SECONDS = 2


def run() -> int:
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stopping = False
    waiting_for_runtime_reported = False

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        # The unit can be installed before the privileged runtime-layout step
        # finishes.  That is an expected bootstrap state, not a crash: wait
        # quietly for the directory rather than creating a systemd restart
        # loop and falsely making the dashboard-service step look broken.
        store = JobStore.runtime()
        if store is None:
            if not waiting_for_runtime_reported:
                print("Mu3Lab worker is waiting for the runtime layout", flush=True)
                waiting_for_runtime_reported = True
            time.sleep(POLL_SECONDS)
            continue
        waiting_for_runtime_reported = False
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
            if job.get("service_id") == "core-suite":
                execute_claimed(store, job, worker_id, ROOT)
            elif str(job.get("service_id") or "").startswith("mcp:"):
                execute_mcp_claimed(store, job, worker_id, ROOT)
            elif str(job.get("service_id") or "").startswith("provider:"):
                execute_provider_claimed(store, job, worker_id, ROOT)
            else:
                execute_service_claimed(store, job, worker_id, ROOT)
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
