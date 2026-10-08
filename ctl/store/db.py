"""Private SQLite connections and serialized, numbered schema migrations."""

from __future__ import annotations

import functools
import inspect
import os
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from ctl.runtime import RuntimePaths
from ctl.secret_file import locked

MIGRATIONS = Path(__file__).with_name("migrations")
_ready: set[tuple[str, int]] = set()
_guard = threading.Lock()
_local = threading.local()


class ClosingConnection(sqlite3.Connection):
    depth = 0
    store_path: str = ""

    def __enter__(self):
        self.depth += 1
        if not hasattr(_local, "connections"):
            _local.connections = {}
        _local.connections[self.store_path] = self
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.depth -= 1
        if self.depth:
            return False
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            _local.connections.pop(self.store_path, None)
            self.close()


def database(paths: RuntimePaths = RuntimePaths()) -> Path:
    return paths.state / "mu3lab.db"


def _migrate(connection: sqlite3.Connection, path: Path) -> None:
    identity = (str(path.resolve()), path.stat().st_ino)
    if identity in _ready:
        return
    with _guard, locked(path.with_suffix(".migration.lock")):
        if identity in _ready:
            return
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
        connection.commit()
        current = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
        files = sorted(MIGRATIONS.glob("[0-9]*.sql"))
        versions = [int(file.name.split("_", 1)[0]) for file in files]
        if current > max(versions, default=0):
            raise ValueError("This database belongs to a newer Mu3Lab release.")
        for version, file in zip(versions, files, strict=True):
            if version <= current:
                continue
            try:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + file.read_text()
                    + f"\nINSERT INTO schema_version(version) VALUES ({version});\nCOMMIT;"
                )
            except (sqlite3.Error, OSError):
                connection.rollback()
                raise
        _ready.add(identity)


def connect(path: Path | str, *, timeout: float = 15, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    key = str(path.resolve())
    active = getattr(_local, "connections", {}).get(key)
    if active is not None:
        return active
    if readonly:
        if (key, path.stat().st_ino) not in _ready:
            with connect(path, timeout=timeout):
                pass
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout, factory=ClosingConnection
        )
    else:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(path, 0o600)
        connection = sqlite3.connect(path, timeout=timeout, factory=ClosingConnection)
    connection.store_path = key
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    if not readonly:
        try:
            _migrate(connection, path)
        except (sqlite3.Error, ValueError, OSError):
            connection.close()
            raise
    return connection


def transactional[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Serialize a store's read/modify/write inside its shared SQLite transaction.

    Nested job preparation joins the caller's transaction, so ciphertext and
    metadata commit together without acquiring a file lock after a SQL lock.
    """
    signature = inspect.signature(function)
    default = signature.parameters["paths"].default

    @functools.wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        paths = signature.bind_partial(*args, **kwargs).arguments.get("paths", default)
        with connect(database(paths)) as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            return function(*args, **kwargs)

    return wrapper
