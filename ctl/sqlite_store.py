"""SQLite connections that close deterministically and set up their schema once.

``with sqlite3.connect(...)`` commits or rolls back but leaves the connection
open until garbage collection; dashboard polling made that pile up.  Stores
open a ``ClosingConnection`` instead, and run their CREATE/ALTER statements
only the first time a process uses a database file.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back like a normal connection, then close."""

    def __exit__(self, exc_type, exc, traceback):
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


def connect(database: Path | str, *, timeout: float = 15.0) -> sqlite3.Connection:
    connection = sqlite3.connect(database, timeout=timeout, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    return connection


_ready: set[tuple[str, int, str]] = set()


def _key(database: Path | str, store: str) -> tuple[str, int, str]:
    path = os.path.abspath(database)
    try:
        return path, os.stat(path).st_ino, store
    except OSError:
        return path, -1, store


def _marked(connection: sqlite3.Connection, store: str) -> bool:
    """Whether this file records that ``store`` created its schema.

    Several stores can share one file, and a new or replaced file has no
    record, so it is always set up whatever this process remembers.
    """
    try:
        return connection.execute("SELECT 1 FROM mu3lab_schema WHERE store = ?", (store,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


@contextmanager
def schema_once(connection: sqlite3.Connection, database: Path | str, store: str) -> Iterator[bool]:
    """Yield True to the one caller that must create or migrate ``store``'s schema in this file."""
    if _key(database, store) in _ready and _marked(connection, store):
        yield False
        return
    path = os.path.abspath(database)
    with _locks_guard:
        lock = _locks.setdefault(path, threading.Lock())
    with lock:
        if _key(database, store) in _ready and _marked(connection, store):
            yield False
            return
        yield True
        connection.execute("CREATE TABLE IF NOT EXISTS mu3lab_schema (store TEXT PRIMARY KEY)")
        connection.execute("INSERT OR IGNORE INTO mu3lab_schema (store) VALUES (?)", (store,))
        connection.commit()
        _ready.add(_key(database, store))
