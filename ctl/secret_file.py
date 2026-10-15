"""Shared plumbing for Mu3Lab's small encrypted stores.

The dashboard and the worker are separate processes that update the same
files, so every read/modify/write happens under an interprocess lock, keys are
created exactly once, and files are replaced atomically with owner-only
permissions from the moment they exist.
"""

from __future__ import annotations

import fcntl
import functools
import inspect
import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import ParamSpec, TypeVar

_held = threading.local()


@contextmanager
def locked(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive lock on ``lock_path``; re-entering in the same thread is a no-op.

    ``flock`` locks are per open file, so two threads or two processes each
    opening the lock file wait for one another.
    """
    held: set[str] = getattr(_held, "paths", None) or set()
    _held.paths = held
    key = str(lock_path)
    if key in held:
        yield
        return
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def read_or_create_key(key_path: Path, generate) -> bytes:
    """Return the key, creating it once with 0600 permissions if it is missing."""
    try:
        return key_path.read_bytes()
    except FileNotFoundError:
        pass
    key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = generate()
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process won the race; use its key.
        return key_path.read_bytes()
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    return value


def write_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data``; readers see the old or the new file, never a partial one."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _sync_directory(directory: Path) -> None:
    """Make the rename itself survive a power cut."""
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


P = ParamSpec("P")
R = TypeVar("R")


def serialized(lock_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Run a store function under ``<runtime>/<lock_name>``, found from its ``paths`` argument."""

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(function)
        default = signature.parameters["paths"].default

        @functools.wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            paths = signature.bind_partial(*args, **kwargs).arguments.get("paths", default)
            with locked(paths.runtime / lock_name):
                return function(*args, **kwargs)

        return wrapper

    return decorate
