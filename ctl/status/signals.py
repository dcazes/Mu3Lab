"""Wake the worker's observer when a job advances, without probing in a request.

The worker also paces its upkeep by whether anyone is looking: the dashboard
touches a marker file on each refresh, and while nobody has looked recently
the worker checks rarely, like a normal background app. App statuses are
checked again only when a page that shows them asks (see ``status_wanted``).
"""

from __future__ import annotations

import os
import time
from threading import Event

from ctl.runtime import RuntimePaths

changed = Event()

VIEWED_FILE = "dashboard-viewed"
HOUSEHOLD_FILE = "household-changed"
STATUS_FILE = "status-wanted"
ACTIVE_SECONDS = 600  # someone looked within 10 minutes: keep the fast pace


def _touch(name: str, paths: RuntimePaths | None) -> None:
    marker = (paths or RuntimePaths()).state / name
    try:
        os.utime(marker)
    except FileNotFoundError:
        try:
            marker.touch()
        except OSError:
            pass
    except OSError:
        pass


def _age(name: str, paths: RuntimePaths | None, now=time.time) -> float:
    try:
        return now() - ((paths or RuntimePaths()).state / name).stat().st_mtime
    except OSError:
        return float("inf")


def viewed(paths: RuntimePaths | None = None) -> None:
    """The dashboard was just refreshed."""
    _touch(VIEWED_FILE, paths)


def status_wanted(paths: RuntimePaths | None = None) -> None:
    """A page showing app statuses was opened or refreshed."""
    _touch(STATUS_FILE, paths)


def status_wanted_at(paths: RuntimePaths | None = None) -> float:
    """When a page last asked for app statuses (seconds since the epoch), or 0."""
    try:
        return ((paths or RuntimePaths()).state / STATUS_FILE).stat().st_mtime
    except OSError:
        return 0.0


def household_changed(paths: RuntimePaths | None = None) -> None:
    """Someone was added, invited, deactivated or had their role changed."""
    _touch(HOUSEHOLD_FILE, paths)


def viewed_within(seconds: float, paths: RuntimePaths | None = None, now=time.time) -> bool:
    return _age(VIEWED_FILE, paths, now) <= seconds


def household_changed_within(seconds: float, paths: RuntimePaths | None = None, now=time.time) -> bool:
    return _age(HOUSEHOLD_FILE, paths, now) <= seconds


def active(paths: RuntimePaths | None = None, now=time.time) -> bool:
    """Someone is using Mu3Lab, or just changed the household (a new person may be signing up)."""
    return viewed_within(ACTIVE_SECONDS, paths, now) or household_changed_within(3600, paths, now)


def every(fast: float, slow: float, paths: RuntimePaths | None = None) -> float:
    """The fast interval while Mu3Lab is in use, else the slow one."""
    return fast if active(paths) else slow
