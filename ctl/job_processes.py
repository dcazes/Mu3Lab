"""Commands a job started, recorded so the next worker can wait for leftovers (R10).

Systemd stops a worker's children with it, but a worker run by hand, or one
killed on its own, can leave a command running after its lease is gone. The
runner (``ctl.process``) reports each child a job starts; this module stores
its pid with the kernel's start time for that pid (so a recycled pid is never
mistaken for it), the worker that started it, and the deadline it ran under.

Before claiming work, a worker ``settle``s: leftovers from any other worker
that are still alive block claiming. One still running past its own deadline
is stopped, exactly as its original runner would have done; a command within
its deadline is left to finish, because stopping Docker mid-change is less
safe than waiting.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ctl import job_guard, process
from ctl.store import db

# A leftover past its deadline gets this long to be stopped by its own
# runner before a new worker stops it.
GRACE_SECONDS = 60


def start_ticks(pid: int) -> int | None:
    """The kernel's start time for ``pid`` (clock ticks since boot), or None if gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = stat.rsplit(")", 1)[-1].split()
    # fields[0] is the state (field 3); starttime is field 22.
    if not fields or fields[0] == "Z":
        return None
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def alive(pid: int, ticks: int) -> bool:
    return start_ticks(pid) == ticks


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobProcesses:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.pid = os.getpid()
        self.ticks = start_ticks(self.pid) or 0

    def _connect(self):
        return db.connect(self.database)

    def tracker(self, proc, argv: Sequence[str], timeout: float) -> Callable[[], None] | None:
        """``ctl.process`` tracker: record children started inside a job only."""
        execution = job_guard.current()
        ticks = start_ticks(proc.pid)
        if execution is None or ticks is None:
            return None
        key = (proc.pid, ticks)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO job_processes VALUES (?,?,?,?,?,?,?,?)",
                (
                    proc.pid,
                    ticks,
                    execution.job_id,
                    self.pid,
                    self.ticks,
                    Path(str(argv[0])).name[:80] if argv else "",
                    time.time() + timeout,
                    _now(),
                ),
            )

        def untrack() -> None:
            with self._connect() as connection:
                connection.execute("DELETE FROM job_processes WHERE pid=? AND start_ticks=?", key)

        return untrack

    def install(self) -> None:
        process.set_tracker(self.tracker)

    def settle(self, log: Callable[[str], None], *, now: Callable[[], float] = time.time) -> list[str]:
        """Forget finished leftovers, stop overdue ones, and describe those still running."""
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM job_processes").fetchall()
        running: list[str] = []
        for row in rows:
            mine = (row["worker_pid"], row["worker_start_ticks"]) == (self.pid, self.ticks)
            if mine and row["job_id"] == _current_job():
                continue
            if not alive(row["pid"], row["start_ticks"]):
                self._forget(row)
                continue
            if now() > float(row["deadline_at"]) + GRACE_SECONDS:
                log(f"Stopping {row['command']} left running by an earlier worker past its time limit.")
                _stop_group(row["pid"])
                if not alive(row["pid"], row["start_ticks"]):
                    self._forget(row)
                    continue
            running.append(f"{row['command']} (pid {row['pid']}, job {row['job_id'][:8]})")
        return running

    def _forget(self, row: sqlite3.Row) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM job_processes WHERE pid=? AND start_ticks=?", (row["pid"], row["start_ticks"])
            )


def _current_job() -> str:
    execution = job_guard.current()
    return execution.job_id if execution else ""


def _stop_group(pid: int, grace: float = 5.0) -> None:
    """The runner started every child in its own session: its pid is the group id."""
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, signum)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if start_ticks(pid) is None:
                return
            time.sleep(0.1)
