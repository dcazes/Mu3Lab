"""Observe services on a worker thread, independently of long-running jobs."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from ctl.core_setup import plan as core_plan
from ctl.jobs import JobStore, redact
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.service_ops import project_path
from ctl.service_state import compose_snapshot, status, tailnet_dns_name, tailnet_serve_status
from ctl.status import signals, snapshots
from ctl.status.host import observe_system
from ctl.store import db, records

ROOT = Path(__file__).resolve().parents[2]
INTERVAL = 60  # a page showing app statuses asked: checked again at most once a minute
IDLE_INTERVAL = 300  # nobody is looking: Docker, Tailscale and every route are checked rarely
BUSY_INTERVAL = 5  # an app is installing, starting, stopping or updating: only that app, often
SETTLING_SECONDS = 120  # after its action ends, an app keeps the quick checks while it comes up
# Actions that change whether an app runs. "core-suite" stands for every core app at once.
BUSY_KINDS = frozenset({"lifecycle", "update", "restore"})
EVERY_APP = "core-suite"


def reconcile(paths: RuntimePaths = RuntimePaths(), only: set[str] | None = None) -> None:
    """Check every app, or just the ``only`` ones (an action is under way for them)."""
    registry = load()
    services = [service for service in registry.services if only is None or service.id in only]
    if not services:
        return
    dns_name = tailnet_dns_name()
    serve = tailnet_serve_status()
    ports = {int(port) for port in serve["ports"]} if isinstance(serve["ports"], list) else set()
    states, containers = compose_snapshot()
    # Route history (last success, consecutive misses) carries across cycles.
    previous = snapshots.read(paths)

    def observe(service):
        return status(
            service,
            dns_name,
            ROOT,
            ports,
            states,
            serve_readable=serve["state"] == "available",
            previous=previous.get(service.id),
        )

    with ThreadPoolExecutor(max_workers=min(4, len(services))) as pool:
        projections = list(pool.map(observe, services))
    for service, projection in zip(services, projections, strict=True):
        projection["containers"] = containers.get(str(project_path(service, ROOT).resolve()), [])
    observed_at = datetime.now(UTC).isoformat(timespec="seconds")
    if only is not None:
        snapshots.write(projections, observed_at, paths)
        return
    core = core_plan(ROOT)
    system = observe_system(registry, ROOT)
    with db.connect(db.database(paths)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        snapshots.write(projections, observed_at, paths)
        records.put("status", "network", {"dns_name": dns_name}, paths)
        records.put("status", "core", core, paths)
        records.put("status", "system", system | {"observed_at": observed_at}, paths)


def busy_apps(paths: RuntimePaths = RuntimePaths()) -> set[str]:
    """Apps with an install, start, stop, update or restore under way."""
    store = JobStore.runtime(paths)
    try:
        jobs = store.active_jobs() if store else []
    except sqlite3.Error:
        return set()  # the regular checks go on; quick ones resume once the job list reads again
    return {str(job["service_id"]) for job in jobs if job["kind"] in BUSY_KINDS}


def full_check_due(last_full: float | None, wanted_at: float, now: float) -> bool:
    """Every 5 minutes, or sooner when a page showing statuses asked and the last check is a minute old."""
    if last_full is None:
        return True
    age = now - last_full
    return age >= IDLE_INTERVAL or (wanted_at > last_full and age >= INTERVAL)


def start(stopping: threading.Event) -> threading.Thread:
    def observe() -> None:
        log = logging.getLogger(__name__)
        last_full: float | None = None
        last_busy = 0.0
        # App -> when its quick checks end; renewed while its action runs.
        settling: dict[str, float] = {}
        while not stopping.is_set():
            # A job moved on (``changed``): its app is looked at at once rather than on the next tick.
            moved = signals.changed.is_set()
            signals.changed.clear()
            now = time.time()
            if RuntimePaths().state.is_dir():
                try:
                    for app in busy_apps():
                        settling[app] = now + SETTLING_SECONDS
                    settling = {app: until for app, until in settling.items() if until > now}
                    quick = moved or now - last_busy >= BUSY_INTERVAL
                    if full_check_due(last_full, signals.status_wanted_at(), now) or (EVERY_APP in settling and quick):
                        last_full = last_busy = now
                        reconcile()
                    elif settling and quick:
                        last_busy = now
                        reconcile(only=set(settling))
                except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
                    log.warning("Status observation deferred: %s", redact(str(exc)))
            signals.changed.wait(timeout=1)

    thread = threading.Thread(target=observe, name="mu3lab-status", daemon=True)
    thread.start()
    return thread
