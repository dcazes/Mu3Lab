"""Keep Authentik sign-in evidence current, on its own thread beside the observer.

Installation proves an app's sign-in once. Authentik providers, client
secrets and outposts can break afterwards, so the worker repeats the same
browser-path check every few hours for each app whose route is answering.
Evidence older than its window stops supporting a usable claim (see
``ctl.status.checks``). Nothing here signs anyone in.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable

from ctl.control_state import ControlState
from ctl.identity import mode_for
from ctl.jobs import JobStore, redact
from ctl.lifecycle.signin_check import SignInError, verify_sign_in
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.status import snapshots
from ctl.status.checks import Check, sign_in_due
from ctl.store import records

INTERVAL = 60


def refresh(log: Callable[[str], None], now: Callable[[], float] = time.time) -> int:
    """Re-check every due app once; return how many were checked."""
    state = ControlState.runtime()
    if state is None:
        return 0
    store = JobStore.runtime()
    busy = {str(job["service_id"]) for job in store.active_jobs()} if store else set()
    host = str(records.get("status", "network").get("dns_name", ""))
    observed = snapshots.read()
    checked = 0
    for service in load().services:
        saved = state.service_identity(service.id)
        route = Check.load(((observed.get(service.id) or {}).get("checks") or {}).get("route"))
        # A job owns the identity row while it runs; a route that is not
        # answering is already reported, and would only make this check fail.
        if service.id in busy or route is None or route.state != "pass" or not sign_in_due(service, saved, now()):
            continue
        owner = str((saved or {}).get("owner_uid") or "")
        job_id = str((saved or {}).get("last_job_id") or "")
        try:
            detail = verify_sign_in(service.id, host, log, patience=0)
        except (SignInError, OSError, ValueError) as exc:
            message = redact(str(exc))
            state.set_service_identity(
                service.id,
                mode_for(service),
                "degraded",
                owner_uid=owner,
                job_id=job_id,
                detail=message,
                error={"code": "sign_in_failed", "message": message},
            )
            log(f"{service.name} sign-in check failed: {message}")
        else:
            state.set_service_identity(
                service.id, mode_for(service), "ready", owner_uid=owner, job_id=job_id, detail=detail, verified=True
            )
        checked += 1
    return checked


def start(stopping: threading.Event, log: Callable[[str], None]) -> threading.Thread:
    def run() -> None:
        while not stopping.wait(timeout=INTERVAL):
            if not RuntimePaths().state.is_dir():
                continue
            try:
                refresh(log)
            except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
                logging.getLogger(__name__).warning("Sign-in verification deferred: %s", redact(str(exc)))

    thread = threading.Thread(target=run, name="mu3lab-sign-in", daemon=True)
    thread.start()
    return thread
