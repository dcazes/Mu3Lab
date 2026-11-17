"""Wake the worker when there is something to do, without probing in a request.

The worker sleeps until it is nudged: the dashboard sends a one-byte message to
its socket (``nudge``) when it queues a job, changes downloads or opens a page
of app statuses, and the worker's own jobs wake its other threads directly
(``wake``). Each sleep still ends after a while, so nothing is missed if a
nudge is.

The worker also paces its upkeep by whether anyone is looking: the dashboard
touches a marker file on each refresh, and while nobody has looked recently
the worker checks rarely, like a normal background app. App statuses are
checked again only when a page that shows them asks (see ``status_wanted``).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from threading import Event

from ctl.runtime import RuntimePaths

changed = Event()  # a job moved on, or something was queued: the status observer looks again
SOCKET_FILE = "worker.sock"
_waiting: list[Event] = [changed]

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
    nudge(paths)


def status_wanted_at(paths: RuntimePaths | None = None) -> float:
    """When a page last asked for app statuses (seconds since the epoch), or 0."""
    try:
        return ((paths or RuntimePaths()).state / STATUS_FILE).stat().st_mtime
    except OSError:
        return 0.0


def household_changed(paths: RuntimePaths | None = None) -> None:
    """Someone was added, invited, deactivated or had their role changed."""
    _touch(HOUSEHOLD_FILE, paths)
    nudge(paths)


def household_changed_at(paths: RuntimePaths | None = None) -> float:
    """When the household last changed through Mu3Lab (seconds since the epoch), or 0."""
    try:
        return ((paths or RuntimePaths()).state / HOUSEHOLD_FILE).stat().st_mtime
    except OSError:
        return 0.0


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


# ---------------------------------------------------------------- waking the worker


def waiter() -> Event:
    """An event set whenever the worker is woken; each sleeping loop waits on its own."""
    event = Event()
    _waiting.append(event)
    return event


def wake() -> None:
    """Wake every sleeping loop in this process."""
    for event in _waiting:
        event.set()


def nudge(paths: RuntimePaths | None = None) -> None:
    """Tell the worker something is waiting for it. Never blocks, and does nothing without a worker."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            sock.sendto(b"1", str((paths or RuntimePaths()).state / SOCKET_FILE))
    except OSError:
        pass  # no worker listening (stopped, or still starting): its loops look again on their own


def listen(paths: RuntimePaths | None = None) -> threading.Thread | None:
    """In the worker: turn nudges from other processes into ``wake``. Returns None if it cannot listen."""
    path = (paths or RuntimePaths()).state / SOCKET_FILE
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        path.unlink(missing_ok=True)  # left by an earlier worker; only one worker runs at a time
        sock.bind(str(path))
        os.chmod(path, 0o600)
    except OSError as exc:
        sock.close()
        logging.getLogger(__name__).warning("The worker will look for work on its own schedule: %s", exc)
        return None

    def receive() -> None:
        while True:
            try:
                sock.recv(16)
            except OSError:
                return
            wake()

    thread = threading.Thread(target=receive, name="mu3lab-nudges", daemon=True)
    thread.start()
    return thread
