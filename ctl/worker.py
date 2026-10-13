"""Persistent Mu3Lab workflow worker.

The web process queues reviewed jobs; this process claims durable leases from
SQLite. A job whose worker died is claimed again; restores and updates then
fail as interrupted and wait for the owner (``ctl.lifecycle.maintenance``).

Only one worker executes at a time (``resource_locks.WorkerLock``): a second
one, or a restarted one while a paused one still lives, waits. Each job holds
its resource's lock while it runs, and no job is claimed while a command an
earlier worker started is still running (``job_processes``).
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path

from ctl import (
    access,
    backup_service,
    job_guard,
    local_models,
    logs,
    rebuild,
    resource_locks,
    self_update,
)
from ctl.chat_ops import sync_agents
from ctl.core_setup import execute_claimed
from ctl.engine.install import run_periodic
from ctl.job_processes import JobProcesses
from ctl.jobs import JobStore
from ctl.mcp_ops import execute_claimed as execute_mcp_claimed
from ctl.provider_ops import execute_claimed as execute_provider_claimed
from ctl.service_ops import execute_claimed as execute_service_claimed
from ctl.status import reconciler, sign_in, signals
from ctl.store import workflows as workflow_secrets

ROOT = Path(__file__).resolve().parent.parent
POLL_SECONDS = 2
log = logging.getLogger("mu3lab.worker")


LEASE_SECONDS = 45  # JobStore's default lease
RENEW_SECONDS = 10


def _maintain_lease(
    store: JobStore,
    job: dict,
    worker_id: str,
    stop: threading.Event,
    lost: threading.Event,
    *,
    every: float = RENEW_SECONDS,
    clock=time.monotonic,
) -> None:
    """Renew the job's lease; set ``lost`` once another worker could have taken the job.

    A briefly locked database is retried: the lease still holds until it
    expires, so only an error lasting until then (less a renewal's margin)
    counts as lost. A renewal the store refuses means someone else owns the job.
    """
    renewed_at = clock()
    while not stop.wait(every):
        try:
            renewed = store.heartbeat(str(job["id"]), worker_id, step_id=str(job.get("step_id") or ""))
        except Exception:  # a locked or unreadable store: our lease may still hold
            if clock() - renewed_at < LEASE_SECONDS - 2 * every:
                continue
            renewed = False
        if not renewed:
            # Another worker may reclaim the job; stop this one at its next checkpoint.
            lost.set()
            return
        renewed_at = clock()


def _dispatch(store: JobStore, job: dict, worker_id: str) -> None:
    if job.get("service_id") == "core-suite":
        execute_claimed(store, job, worker_id, ROOT)
    elif job.get("service_id") == self_update.SERVICE_ID:
        self_update.execute_claimed(store, job, worker_id, ROOT)
    elif job.get("service_id") == local_models.SERVICE_ID:
        local_models.execute_claimed(store, job, worker_id, ROOT)
    elif str(job.get("service_id") or "").startswith("mcp:"):
        execute_mcp_claimed(store, job, worker_id, ROOT)
    elif str(job.get("service_id") or "").startswith("provider:"):
        execute_provider_claimed(store, job, worker_id, ROOT)
    else:
        execute_service_claimed(store, job, worker_id, ROOT)


def resources(job: dict) -> tuple[str, ...]:
    """The lock keys a job holds while it runs: its app, connector, provider or the platform."""
    service_id = str(job.get("service_id") or "")
    if service_id == self_update.SERVICE_ID:
        return ("platform",)
    return (service_id if ":" in service_id else f"app:{service_id}",)


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
            with resource_locks.hold(*resources(job)):
                _dispatch(store, job, worker_id)
        except resource_locks.Busy as busy:
            store.transition(
                job_id,
                "failed",
                actor=worker_id,
                detail=f"Nothing was changed: {busy}. Try again once it has finished.",
                error_code="resource_busy",
            )
        except job_guard.JobInterrupted as interruption:
            if interruption.reason == job_guard.CANCELLED:
                store.acknowledge_cancel(job_id, worker_id)
                log.info(f"Mu3Lab job {job_id} stopped after cancellation.")
            else:
                log.warning(f"Mu3Lab job {job_id} stopped: this worker no longer holds its lease.")
        except Exception as exc:  # final containment for all future dispatchers
            log.exception(f"Mu3Lab job {job_id} failed in the worker")
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
            signals.changed.set()


def run() -> int:
    logs.configure()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stopping = False
    waiting_for_runtime_reported = False
    next_mcp_reconcile = 0.0
    next_chat_sync = 0.0
    next_rule_maintenance = 0.0
    next_vault_sync = 0.0
    next_backup_service = 0.0
    next_history_prune = 0.0

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    downloads_stopping = threading.Event()
    status_thread: threading.Thread | None = None
    lock = resource_locks.WorkerLock()
    waiting_for_lock_reported = False
    processes: JobProcesses | None = None
    leftovers_reported: list[str] = []
    settled = False
    survivors_since = 0.0
    while not stopping:
        # The unit can be installed before the privileged runtime-layout step
        # finishes.  That is an expected bootstrap state, not a crash: wait
        # quietly for the directory rather than creating a systemd restart
        # loop and falsely making the dashboard-service step look broken.
        store = JobStore.runtime()
        if store is None:
            if not waiting_for_runtime_reported:
                log.info("Mu3Lab worker is waiting for the runtime layout")
                waiting_for_runtime_reported = True
            time.sleep(POLL_SECONDS)
            continue
        waiting_for_runtime_reported = False
        if not lock.held:
            if not lock.try_acquire():
                if not waiting_for_lock_reported:
                    log.info("Another Mu3Lab worker is running; this one waits until it stops.")
                    waiting_for_lock_reported = True
                time.sleep(POLL_SECONDS)
                continue
            # Background work starts only once this is the one executing worker.
            from ctl import download_manager

            processes = JobProcesses(store.database)
            processes.install()
            # Reading status, downloading images and revoking access never change a running app,
            # so they start at once. App images download in the background, several at once,
            # while setup jobs run one at a time below.
            download_manager.start(log.info, downloads_stopping)
            status_thread = reconciler.start(downloads_stopping)
            sign_in.start(downloads_stopping, log.info)
            access.start(downloads_stopping, log.info)
        from ctl import backups

        assert processes is not None
        leftovers = processes.settle(log.info)
        if not settled:
            # Only at start: a one-off backup container of the previous worker may still be reading
            # an app's data. Later ones are this worker's own. Like the backups' own survivor check,
            # it gets a bounded wait, then is stopped (Kopia exits cleanly on SIGTERM).
            survivors = backups._running_backups()
            survivors_since = (survivors_since or time.monotonic()) if survivors else 0.0
            if survivors and time.monotonic() - survivors_since > backups.SURVIVOR_WAIT_SECONDS:
                for name in survivors:
                    log.info(f"Stopping backup container {name[:12]} left running by an earlier worker.")
                    backups._stop_container(name, log.info)
                survivors = backups._running_backups()
            leftovers += [f"backup container {name[:12]}" for name in survivors]
        if leftovers:
            # Never start, restart or reconfigure anything while an earlier worker's command may
            # still be changing things: no maintenance below, and no new jobs.
            if leftovers != leftovers_reported:
                log.info(f"Mu3Lab waits for an earlier worker's command to finish: {', '.join(leftovers)}")
                leftovers_reported = leftovers
            time.sleep(POLL_SECONDS)
            continue
        leftovers_reported = []
        if not settled:
            settled = True
            backups.start_verification(downloads_stopping, log.info)
        if time.monotonic() >= next_rule_maintenance:
            next_rule_maintenance = time.monotonic() + 60
            try:
                run_periodic(store, ROOT, log.info)
            except Exception as exc:
                log.warning(f"Mu3Lab sign-in verification deferred safely: {exc}")
        if time.monotonic() >= next_vault_sync:
            next_vault_sync = time.monotonic() + 300
            try:
                from ctl import vault_sync

                vault_sync.run(log.info)
            except Exception as exc:
                log.warning(f"Mu3Lab vault saving deferred safely: {exc}")
        if time.monotonic() >= next_backup_service:
            next_backup_service = time.monotonic() + 60
            try:
                # Kopia's server runs the schedule; keep its folders, actions and policy in step.
                backup_service.reconcile(store, ROOT, log.info)
            except Exception as exc:
                log.warning(f"Mu3Lab backup service upkeep deferred safely: {exc}")
            try:
                # After a rebuild on a new computer: start the apps that were running before.
                rebuild.resume(store, ROOT, log.info)
            except Exception as exc:
                log.warning(f"Mu3Lab could not start the restored apps yet: {exc}")
        if time.monotonic() >= next_history_prune:
            next_history_prune = time.monotonic() + 24 * 3600
            try:
                from ctl import history

                removed = history.prune(store.database)
                if any(removed.values()):
                    log.info(f"Mu3Lab removed old history: {removed}")
            except Exception as exc:
                log.warning(f"Mu3Lab history clean-up deferred: {exc}")
        if time.monotonic() >= next_mcp_reconcile:
            next_mcp_reconcile = time.monotonic() + 60
            try:
                from ctl.mcp_ops import reconcile_lifecycle

                reconcile_lifecycle(ROOT, log.info)
            except Exception as exc:
                log.warning(f"Mu3Lab MCP lifecycle reconciliation deferred: {exc}")
        if time.monotonic() >= next_chat_sync:
            next_chat_sync = time.monotonic() + 300
            try:
                sync_agents(log.info)
            except Exception as exc:
                log.warning(f"Mu3Lab chat assistant synchronization deferred: {exc}")
        try:
            from ctl.install_batches import InstallBatchStore

            batches = InstallBatchStore.runtime()
            if batches:
                batches.reconcile(store)
        except Exception as exc:
            log.warning(f"Mu3Lab batch reconciliation deferred safely: {exc}")
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
                log.warning(f"Mu3Lab post-job reconciliation deferred safely: {exc}")
    downloads_stopping.set()
    signals.changed.set()
    if status_thread is not None:
        status_thread.join(timeout=2)
    lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
