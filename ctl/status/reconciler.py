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
from ctl.jobs import redact
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.service_ops import project_path
from ctl.service_state import compose_snapshot, status, tailnet_dns_name, tailnet_serve_ports
from ctl.status import signals, snapshots
from ctl.status.host import observe_system
from ctl.store import db, records

ROOT = Path(__file__).resolve().parents[2]
INTERVAL = 5


def reconcile(paths: RuntimePaths = RuntimePaths()) -> None:
    registry = load()
    dns_name = tailnet_dns_name()
    ports = tailnet_serve_ports()
    states, containers = compose_snapshot()
    with ThreadPoolExecutor(max_workers=min(4, len(registry.services))) as pool:
        projections = list(pool.map(lambda service: status(service, dns_name, ROOT, ports, states), registry.services))
    for service, projection in zip(registry.services, projections, strict=True):
        projection["containers"] = containers.get(str(project_path(service, ROOT).resolve()), [])
    core = core_plan(ROOT)
    system = observe_system()
    observed_at = datetime.now(UTC).isoformat(timespec="seconds")
    with db.connect(db.database(paths)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        snapshots.write(projections, observed_at, paths)
        records.put("status", "network", {"dns_name": dns_name}, paths)
        records.put("status", "core", core, paths)
        records.put("status", "system", system | {"observed_at": observed_at}, paths)


def start(stopping: threading.Event) -> threading.Thread:
    def observe() -> None:
        next_due = 0.0
        while not stopping.is_set():
            if RuntimePaths().state.is_dir() and (signals.changed.is_set() or time.monotonic() >= next_due):
                signals.changed.clear()
                try:
                    reconcile()
                except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
                    logging.getLogger(__name__).warning("Status observation deferred: %s", redact(str(exc)))
                next_due = time.monotonic() + INTERVAL
            signals.changed.wait(timeout=0.5)

    thread = threading.Thread(target=observe, name="mu3lab-status", daemon=True)
    thread.start()
    return thread
