"""Interprocess locks for the worker and the resources its jobs change (R10).

A job lease proves a worker is alive; it cannot stop a worker that was
paused past its lease from finishing a command after another worker took the
job over. Two kernel-held ``flock`` locks close that gap on a single host:

- ``WorkerLock``: one worker process executes jobs and background repairs at
  a time. A paused worker keeps the lock, so no second worker can start while
  the first could still wake up and continue. The kernel releases it when
  the process exits, however it exits.
- ``hold(*keys)``: per-resource locks (``app:<id>``, ``backup-repository``,
  ``chat-assistants`` …). Keys are always taken in sorted order, and a thread
  that already holds a key takes it again for free.

Only a job takes several keys, possibly one after another (its own app, then
apps it re-wires). Background tasks take a single key and never nest, so the
two can never wait for each other in a cycle.
"""

from __future__ import annotations

import fcntl
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ctl.runtime import RuntimePaths

LOCK_TIMEOUT = 3600
_KEY = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,120}$")
_POLL = 0.2
_held = threading.local()


class Busy(RuntimeError):
    """A resource stayed locked by other work for longer than the caller waits."""

    def __init__(self, key: str) -> None:
        super().__init__(f"{key} is busy with other work")
        self.key = key


def directory(paths: RuntimePaths | None = None) -> Path:
    path = (paths or RuntimePaths()).state / "locks"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _file(key: str, paths: RuntimePaths | None) -> Path:
    if not _KEY.fullmatch(key):
        raise ValueError(f"invalid resource key: {key!r}")
    return directory(paths) / f"{key.replace(':', '--')}.lock"


def held() -> frozenset[str]:
    return frozenset(getattr(_held, "keys", ()))


def _acquire(fd: int, key: str, deadline: float | None) -> None:
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if deadline is not None and time.monotonic() >= deadline:
                raise Busy(key) from None
            time.sleep(_POLL)


@contextmanager
def hold(*keys: str, timeout: float | None = LOCK_TIMEOUT, paths: RuntimePaths | None = None) -> Iterator[None]:
    """Hold every key until the block ends; raise ``Busy`` after ``timeout`` seconds."""
    mine = getattr(_held, "keys", None)
    if mine is None:
        mine = _held.keys = set()
    wanted = sorted(set(keys) - mine)
    files = [_file(key, paths) for key in wanted]
    deadline = None if timeout is None else time.monotonic() + timeout
    taken: list[tuple[str, int]] = []
    try:
        for key, path in zip(wanted, files, strict=True):
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                _acquire(fd, key, deadline)
            except BaseException:
                os.close(fd)
                raise
            taken.append((key, fd))
            mine.add(key)
        yield
    finally:
        for key, fd in reversed(taken):
            mine.discard(key)
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


class WorkerLock:
    """The single-worker execution lock; held for the life of the process."""

    NAME = "worker"

    def __init__(self, paths: RuntimePaths | None = None) -> None:
        self.paths = paths
        self.fd: int | None = None

    @property
    def held(self) -> bool:
        return self.fd is not None

    def try_acquire(self) -> bool:
        if self.fd is not None:
            return True
        fd = os.open(_file(self.NAME, self.paths), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self.fd = fd
        return True

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None
