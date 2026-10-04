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

from ctl import job_guard, self_update
from ctl.core_setup import execute_claimed
from ctl.engine.install import run_periodic
from ctl.jobs import JobStore
from ctl.lobehub_ops import sync_agents
from ctl.mcp_ops import execute_claimed as execute_mcp_claimed
from ctl.provider_ops import execute_claimed as execute_provider_claimed
from ctl.service_ops import execute_claimed as execute_service_claimed
from ctl.store import workflows as workflow_secrets

ROOT = Path(__file__).resolve().parent.parent
POLL_SECONDS = 2


def _maintain_lease(store: JobStore, job: dict, worker_id: str, stop: threading.Event, lost: threading.Event) -> None:
    while not stop.wait(10):
        try:
            renewed = store.heartbeat(str(job["id"]), worker_id, step_id=str(job.get("step_id") or ""))
        except Exception:  # a locked or unreadable store cannot prove we still own the job
            renewed = False
        if not renewed:
            # Another worker may reclaim the job; stop this one at its next checkpoint.
            lost.set()
            return


def _dispatch(store: JobStore, job: dict, worker_id: str) -> None:
    if job.get("service_id") == "core-suite":
        execute_claimed(store, job, worker_id, ROOT)
    elif job.get("service_id") == self_update.SERVICE_ID:
        self_update.execute_claimed(store, job, worker_id, ROOT)
    elif str(job.get("service_id") or "").startswith("mcp:"):
        execute_mcp_claimed(store, job, worker_id, ROOT)
    elif str(job.get("service_id") or "").startswith("provider:"):
        execute_provider_claimed(store, job, worker_id, ROOT)
    else:
        execute_service_claimed(store, job, worker_id, ROOT)


def run_job(store: JobStore, job: dict, worker_id: str) -> None:
    """Run one claimed job, stopping it cleanly if it is cancelled or its lease is lost."""
    job_id = str(job["id"])
    with job_guard.executing(store, job_id, worker_id) as execution:
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=_maintain_lease, args=(store, job, worker_id, heartbeat_stop, execution.lost), daemon=True
        )
        heartbeat_thread.start()
        try:
            _dispatch(store, job, worker_id)
        except job_guard.JobInterrupted as interruption:
            if interruption.reason == job_guard.CANCELLED:
                store.acknowledge_cancel(job_id, worker_id)
                print(f"Mu3Lab job {job_id} stopped after cancellation.", flush=True)
            else:
                print(f"Mu3Lab job {job_id} stopped: this worker no longer holds its lease.", flush=True)
        except Exception as exc:  # final containment for all future dispatchers
            try:
                store.transition(
                    job_id,
                    "failed",
                    actor=worker_id,
                    detail=f"Worker stopped safely: {exc}",
                    error_code="worker_failure",
                )
            except (KeyError, ValueError, job_guard.JobInterrupted):
                pass
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)


def run() -> int:
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stopping = False
    waiting_for_runtime_reported = False
    next_mcp_reconcile = 0.0
    next_chat_sync = 0.0
    next_rule_maintenance = 0.0
    next_vault_sync = 0.0

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # App images download in the background, several at once, while setup
    # jobs run one at a time below.
    downloads_stopping = threading.Event()
    from ctl import download_manager

    download_manager.start(lambda line: print(line, flush=True), downloads_stopping)
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
        if time.monotonic() >= next_rule_maintenance:
            next_rule_maintenance = time.monotonic() + 60
            try:
                run_periodic(store, ROOT, lambda line: print(line, flush=True))
            except Exception as exc:
                print(f"Mu3Lab sign-in verification deferred safely: {exc}", flush=True)
        if time.monotonic() >= next_vault_sync:
            next_vault_sync = time.monotonic() + 300
            try:
                from ctl import vault_sync

                vault_sync.run(lambda line: print(line, flush=True))
            except Exception as exc:
                print(f"Mu3Lab vault saving deferred safely: {exc}", flush=True)
        if time.monotonic() >= next_mcp_reconcile:
            next_mcp_reconcile = time.monotonic() + 60
            try:
                from ctl.mcp_ops import reconcile_lifecycle

                reconcile_lifecycle(ROOT, lambda line: print(line, flush=True))
            except Exception as exc:
                print(f"Mu3Lab MCP lifecycle reconciliation deferred: {exc}", flush=True)
        if time.monotonic() >= next_chat_sync:
            next_chat_sync = time.monotonic() + 300
            try:
                sync_agents(lambda line: print(line, flush=True))
            except Exception as exc:
                print(f"Mu3Lab chat assistant synchronization deferred: {exc}", flush=True)
        try:
            from ctl.install_batches import InstallBatchStore

            batches = InstallBatchStore.runtime()
            if batches:
                batches.reconcile(store)
        except Exception as exc:
            print(f"Mu3Lab batch reconciliation deferred safely: {exc}", flush=True)
        job = store.claim(worker_id)
        if job is None:
            time.sleep(POLL_SECONDS)
            continue
        try:
            run_job(store, job, worker_id)
            # A finished install may have created logins; save them within a minute.
            next_vault_sync = min(next_vault_sync, time.monotonic() + 30)
        finally:
            try:
                from ctl.install_batches import InstallBatchStore

                batches = InstallBatchStore.runtime()
                if batches:
                    batches.advance_for_job(str(job["id"]), store)
                workflow_secrets.cleanup()
            except Exception as exc:  # batch recovery will reconcile on next API/worker pass
                print(f"Mu3Lab post-job reconciliation deferred safely: {exc}", flush=True)
    downloads_stopping.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
