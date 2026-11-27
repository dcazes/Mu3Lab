"""Persistent Mu3Lab workflow worker.

The web process queues reviewed jobs; this process claims durable leases from
SQLite. A job whose worker died is claimed again; restores and updates then
fail as interrupted and wait for the owner (``ctl.lifecycle.maintenance``).

Only one worker executes at a time (``resource_locks.WorkerLock``): a second
one, or a restarted one while a paused one still lives, waits. Each job holds
its resource's lock while it runs, and no job is claimed while a command an
earlier worker started is still running (``job_processes``).

Jobs run one at a time, in the order they were asked for, with one exception:
updates of catalog apps that have nothing to do with each other run side by
side, up to ``SIDE_BY_SIDE`` at once (``_together``). What they share (the
private ingress, sign-in, the backup repository, chat) is locked step by step.
While updates wait or run, the upkeep that talks to apps waits too, and runs
once after the last one, instead of after each app.
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
    backup_location,
    backup_service,
    chat_ops,
    disk_cleanup,
    job_guard,
    local_models,
    logs,
    rebuild,
    resource_locks,
    resource_usage,
    self_update,
)
from ctl.chat_ops import sync_agents
from ctl.control_state import ControlState
from ctl.core_setup import execute_claimed
from ctl.engine.install import run_periodic
from ctl.job_processes import JobProcesses
from ctl.jobs import JobStore
from ctl.mcp_ops import execute_claimed as execute_mcp_claimed
from ctl.provider_ops import execute_claimed as execute_provider_claimed
from ctl.registry import Registry, Service
from ctl.service_ops import execute_claimed as execute_service_claimed
from ctl.status import reconciler, sign_in, signals
from ctl.store import records
from ctl.store import workflows as workflow_secrets

ROOT = Path(__file__).resolve().parent.parent
POLL_SECONDS = 2  # while an earlier worker's command or the runtime layout is still settling
# With nothing to do the worker sleeps until the dashboard nudges it (``signals.nudge``), and at
# most this long, so upkeep keeps its pace and a missed nudge only delays work.
IDLE_SECONDS = 30.0
# Upkeep that talks to apps (each app's ``hooks.periodic``, the backup service's and the chat
# connectors') runs when something happened: a job finished, an app started or stopped outside
# Mu3Lab, the household or the backup settings changed, or the worker started. It runs at once,
# then each minute for a while so steps waiting on an app that is still starting are retried, and
# otherwise once an hour as a safety pass. A running backup keeps the backup upkeep each minute.
EVENT_FOLLOW_UP_SECONDS = 600
EVENT_RETRY_SECONDS = 60
SAFETY_PASS_SECONDS = 3600
log = logging.getLogger("mu3lab.worker")


_upstream_checking = threading.Event()


def _check_upstream() -> None:
    try:
        from ctl.lifecycle import upstream
        from ctl.registry import load as load_registry

        upstream.check_due(load_registry(), ROOT, log.info)
    except Exception as exc:
        log.warning(f"Mu3Lab's check for newer app releases deferred: {exc}")
    finally:
        _upstream_checking.clear()


def _running_apps() -> frozenset[str]:
    state = ControlState.runtime()
    return state.running_apps() if state else frozenset()


def _backup_changed_at() -> str:
    return str(records.get(*backup_service.SETTINGS).get("changed_at") or "")


def _backup_busy() -> bool:
    return bool(records.names(backup_service.HOLDS))


# App updates that may run at once (see ``_together``).
SIDE_BY_SIDE = 3
# Apps whose update always runs alone: every chat app, and the backup service the others' backups use.
_ALONE_CAPABILITIES = frozenset({"chat", "backup_service"})


def _side_by_side(job: dict, registry: Registry) -> Service | None:
    """The app, when this job is an update that may run beside other updates."""
    if job.get("action") != "update" or job.get("kind") != "lifecycle":
        return None
    try:
        service = registry.get(str(job.get("service_id") or ""))
    except Exception:
        return None
    if service.stage != "optional" or set(service.manifest.capabilities) & _ALONE_CAPABILITIES:
        return None
    return service


def _related(one: Service, other: Service) -> bool:
    """Whether one app needs the other, or works with what the other offers."""
    if one.id in other.dependencies or other.id in one.dependencies:
        return True
    offers = set(one.manifest.capabilities), set(other.manifest.capabilities)
    wants = (
        {item.capability for item in one.manifest.integrates_with},
        {item.capability for item in other.manifest.integrates_with},
    )
    return bool(offers[0] & wants[1] or offers[1] & wants[0])


def _together(job: dict, running: list[dict]) -> bool:
    """Whether ``job`` may start while ``running`` jobs run: unrelated app updates, a few at once."""
    if not running or len(running) >= SIDE_BY_SIDE:
        return False
    from ctl.registry import load as load_registry

    try:
        registry = load_registry()
    except Exception:
        return False
    service = _side_by_side(job, registry)
    if service is None:
        return False
    for other in running:
        current = _side_by_side(other, registry)
        if current is None or current.id == service.id or _related(service, current):
            return False
    return True


def _updates_waiting(store: JobStore) -> bool:
    """An app update is waiting or running: upkeep that talks to apps waits until the last one."""
    return any(job.get("action") == "update" for job in store.active_jobs())


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
    elif job.get("service_id") == disk_cleanup.SERVICE_ID:
        disk_cleanup.execute_claimed(store, job, worker_id, ROOT)
    elif job.get("service_id") == backup_location.SERVICE_ID:
        backup_location.execute_claimed(store, job, worker_id, ROOT)
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
    # Upkeep runs at its usual pace while someone uses Mu3Lab (``signals.active``), and
    # rarely while nobody does, so an idle Mu3Lab barely touches Authentik, Vaultwarden or Docker.
    last_run: dict[str, float] = {}
    vault_sync_soon = 0.0
    vault_household_seen = 0.0  # the household change the last vault save already covered
    build_cache_due = False

    def due(task: str, fast: float, slow: float) -> bool:
        now = time.monotonic()
        if task in last_run and now - last_run[task] < signals.every(fast, slow):
            return False
        last_run[task] = now
        return True

    # The worker starting counts as an event: after a boot, an update or a restart every
    # app's upkeep runs and then follows up.
    last_event = time.monotonic()
    seen: dict[str, object] = {}

    def notice_events() -> None:
        """Record an event when an app started or stopped, or the household or backup settings changed."""
        nonlocal last_event
        try:
            now_seen = {
                "running": _running_apps(),
                "household": signals.household_changed_at(),
                "backup": _backup_changed_at(),
            }
        except Exception as exc:  # unreadable for a moment: treat it as a change, to be safe
            log.warning(f"Mu3Lab could not read app states for upkeep: {exc}")
            last_event = time.monotonic()
            return
        if seen and now_seen != seen:
            last_event = time.monotonic()
        seen.update(now_seen)

    def event_due(task: str, *, busy: bool = False) -> bool:
        """Upkeep that talks to apps: after an event, during its follow-up, while ``busy``, or hourly."""
        now = time.monotonic()
        last = last_run.get(task)
        following_up = busy or now - last_event < EVENT_FOLLOW_UP_SECONDS
        if (
            last is None
            or last < last_event
            or (following_up and now - last >= EVENT_RETRY_SECONDS)
            or now - last >= SAFETY_PASS_SECONDS
        ):
            last_run[task] = now
            return True
        return False

    wakeup = signals.waiter()

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True
        signals.nudge()  # ends the idle sleep; a socket message is safe to send from a signal handler

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    downloads_stopping = threading.Event()
    status_thread: threading.Thread | None = None
    # App updates running beside each other, on their own threads (see ``_together``).
    running: dict[str, tuple[threading.Thread, dict]] = {}

    def finished(store: JobStore, job: dict) -> None:
        """What follows every job, once it ended."""
        nonlocal last_event, build_cache_due, vault_sync_soon
        # Installs, removals, updates, starts, stops and settings changes are the events
        # app upkeep follows (see EVENT_FOLLOW_UP_SECONDS).
        last_event = time.monotonic()
        # Once nothing else runs, clear what its builds left (see ctl/disk_cleanup.py).
        build_cache_due = build_cache_due or disk_cleanup.builds(job)
        if resource_usage.touches_disk(job):
            resource_usage.disk_stale.set()
        # A finished install may have created logins; save them within a minute.
        vault_sync_soon = vault_sync_soon or time.monotonic() + 30
        try:
            from ctl.install_batches import InstallBatchStore

            batches = InstallBatchStore.runtime()
            if batches:
                batches.advance_for_job(str(job["id"]), store)
            workflow_secrets.cleanup()
        except Exception as exc:  # batch recovery will reconcile on next API/worker pass
            log.warning(f"Mu3Lab post-job reconciliation deferred safely: {exc}")

    def beside(store: JobStore, job: dict) -> None:
        def body() -> None:
            try:
                run_job(store, job, worker_id)
            finally:
                signals.wake()  # the main loop reaps it and may start the next one

        thread = threading.Thread(target=body, name=f"mu3lab-job-{str(job['id'])[:8]}", daemon=True)
        running[str(job["id"])] = (thread, job)
        thread.start()

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
            signals.listen()
            download_manager.start(log.info, downloads_stopping)
            status_thread = reconciler.start(downloads_stopping)
            resource_usage.start(downloads_stopping, ROOT, log.info)
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
        for job_id, (thread, done) in list(running.items()):
            if not thread.is_alive():
                thread.join()
                del running[job_id]
                finished(store, done)
        if running:
            # Only another app update that has nothing to do with these may start now.
            others = [job for _thread, job in running.values()]

            def accept(row: dict, others: list[dict] = others) -> bool:
                return _together(row, others)

            try:
                job = store.claim(worker_id, accept=accept)
            except Exception as exc:  # a locked store: try again shortly
                log.warning(f"Mu3Lab could not look for the next job yet: {exc}")
                job = None
            if job is not None:
                beside(store, job)
                continue
            wakeup.wait(POLL_SECONDS)
            wakeup.clear()
            continue
        notice_events()
        try:
            # Updates still waiting: the upkeep that talks to apps runs once, after the last one.
            upkeep = not _updates_waiting(store)
        except Exception:
            upkeep = True
        rules_due = upkeep and due("rules", 60, 900)
        app_upkeep_due = upkeep and event_due("app-upkeep")
        if rules_due or app_upkeep_due:
            try:
                run_periodic(store, ROOT, log.info, rules=rules_due, app_upkeep=app_upkeep_due)
            except Exception as exc:
                log.warning(f"Mu3Lab app maintenance deferred safely: {exc}")
        # Logins are saved after each job, when the household changes, and while someone's logins
        # wait for their vault account; otherwise an hourly pass catches anything changed elsewhere.
        from ctl import vault_sync

        household_at = signals.household_changed_at()
        if upkeep and (
            (vault_sync_soon and time.monotonic() >= vault_sync_soon)
            or household_at > vault_household_seen
            or due("vault", 300 if vault_sync.someone_waiting() else 3600, 3600)
        ):
            vault_sync_soon = 0.0
            vault_household_seen = household_at
            last_run["vault"] = time.monotonic()
            try:
                vault_sync.run(log.info, upkeep=True)
            except Exception as exc:
                log.warning(f"Mu3Lab vault saving deferred safely: {exc}")
        try:
            backup_busy = _backup_busy()
        except Exception:
            backup_busy = True  # unsure: keep the backup upkeep each minute
        if upkeep and event_due("backup", busy=backup_busy):
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
        if due("upstream-releases", 3600, 3600) and not _upstream_checking.is_set():
            # Newer releases from each installed app's makers, about once a day per app
            # (ctl/lifecycle/upstream.py). Read-only: it only decides what the Update button
            # offers. It reads GitHub and the registries, so it runs beside the job loop.
            _upstream_checking.set()
            threading.Thread(target=_check_upstream, name="upstream-releases", daemon=True).start()
        if due("history", 24 * 3600, 24 * 3600):
            try:
                from ctl import history

                removed = history.prune(store.database)
                if any(removed.values()):
                    log.info(f"Mu3Lab removed old history: {removed}")
            except Exception as exc:
                log.warning(f"Mu3Lab history clean-up deferred: {exc}")
        if upkeep and event_due("mcp"):
            try:
                from ctl.mcp_ops import reconcile_lifecycle

                reconcile_lifecycle(ROOT, log.info)
            except Exception as exc:
                log.warning(f"Mu3Lab MCP lifecycle reconciliation deferred: {exc}")
        # App updates hold their chat syncs back (``chat_ops.deferred_sync``): one sync after the last.
        if upkeep and (chat_ops.sync_pending() or due("chat", 300, 3600)):
            try:
                sync_agents(log.info)
            except Exception as exc:
                log.warning(f"Mu3Lab chat assistant synchronization deferred: {exc}")
        batches = None
        try:
            from ctl.install_batches import InstallBatchStore

            batches = InstallBatchStore.runtime()
            if batches:
                batches.reconcile(store)
        except Exception as exc:
            log.warning(f"Mu3Lab batch reconciliation deferred safely: {exc}")
        if upkeep and build_cache_due and not disk_cleanup.busy(store, batches):
            build_cache_due = False
            try:
                if disk_cleanup.clear_build_cache(log.info):
                    resource_usage.disk_stale.set()
            except Exception as exc:
                log.warning(f"Mu3Lab build cache clean-up deferred: {exc}")
        job = store.claim(worker_id)
        if job is None:
            idle = IDLE_SECONDS
            if vault_sync_soon:
                idle = min(idle, max(0.0, vault_sync_soon - time.monotonic()))
            wakeup.wait(idle)
            wakeup.clear()
            continue
        try:
            from ctl.registry import load as load_registry

            alongside = _side_by_side(job, load_registry()) is not None
        except Exception:
            alongside = False
        if alongside:
            # Updates after it that have nothing to do with it may start beside it.
            beside(store, job)
            continue
        try:
            run_job(store, job, worker_id)
        finally:
            finished(store, job)
    # Updates already under way finish first: stopping one half-way is worse than waiting.
    for thread, _job in running.values():
        thread.join()
    downloads_stopping.set()
    signals.wake()
    if status_thread is not None:
        status_thread.join(timeout=2)
    lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
