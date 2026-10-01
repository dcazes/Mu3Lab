"""Stop a job's side effects once it is cancelled or its worker loses the lease.

The worker runs each job inside ``executing``. Before every host or Docker
command, job event and durable state update, ``checkpoint`` confirms that
this worker still holds the job's lease and that nobody asked to cancel it.
Otherwise it raises ``JobInterrupted``. That derives from BaseException so a
step's ordinary ``except Exception`` cannot swallow it; the worker handles it.

A command that is already running is allowed to finish (stopping Docker
mid-change is less safe than letting it complete), but nothing after it runs.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ctl.jobs import JobStore

CANCELLED = "cancelled"
LEASE_LOST = "lease_lost"


class JobInterrupted(BaseException):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Execution:
    store: JobStore
    job_id: str
    worker_id: str
    # Set by the heartbeat thread when it can no longer renew the lease.
    lost: threading.Event = field(default_factory=threading.Event)
    # Inside ``uncancellable`` a cancel request waits until the block ends.
    shielded: int = 0


_local = threading.local()


@contextmanager
def executing(store: JobStore, job_id: str, worker_id: str) -> Iterator[Execution]:
    execution = Execution(store, job_id, worker_id)
    previous = getattr(_local, "execution", None)
    _local.execution = execution
    try:
        yield execution
    finally:
        _local.execution = previous


def current() -> Execution | None:
    return getattr(_local, "execution", None)


@contextmanager
def uncancellable() -> Iterator[None]:
    """Finish a step whose half-done state is worse than either end.

    Swapping an app's data or release, or putting it back, must run to the
    end once begun. A cancel request is honoured at the first checkpoint
    after the block; losing the lease still stops it, since another worker
    may then own the job.
    """
    execution = current()
    if execution is None:
        yield
        return
    execution.shielded += 1
    try:
        yield
    finally:
        execution.shielded -= 1


def checkpoint() -> None:
    """Raise JobInterrupted if the job running on this thread must stop now."""
    execution = current()
    if execution is None:
        return
    if execution.lost.is_set():
        raise JobInterrupted(LEASE_LOST)
    reason = execution.store.interruption(execution.job_id, execution.worker_id)
    if reason and not (reason == CANCELLED and execution.shielded):
        raise JobInterrupted(reason)
