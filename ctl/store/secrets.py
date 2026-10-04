"""One encryption key and scoped ciphertext rows, with optional expiration."""

from __future__ import annotations

import json
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ctl.runtime import RuntimePaths
from ctl.secret_file import locked, read_or_create_key
from ctl.store import db


class SecretError(ValueError):
    pass


class SecretStore:
    def __init__(self, paths: RuntimePaths = RuntimePaths()) -> None:
        self.paths = paths

    def _cipher(self, *, create: bool = False) -> Fernet:
        with locked(self.paths.state / "secrets-key.lock"):
            path = self.paths.state / "secrets.key"
            if not path.is_file():
                if not create:
                    raise SecretError("The secret store encryption key is missing.")
                database = db.database(self.paths)
                if database.is_file():
                    with db.connect(database) as connection:
                        if connection.execute("SELECT 1 FROM secrets LIMIT 1").fetchone():
                            raise SecretError("The secret store encryption key is missing.")
            key = read_or_create_key(path, Fernet.generate_key)
        try:
            return Fernet(key)
        except ValueError:
            raise SecretError("The secret store's encryption key is invalid.") from None

    def put(self, scope: str, name: str, value: Any, ttl: float | None = None) -> None:
        if not scope or not name:
            raise SecretError("A secret scope and name are required.")
        now = time.time()
        ciphertext = self._cipher(create=True).encrypt(json.dumps(value).encode())
        with db.connect(db.database(self.paths)) as connection:
            connection.execute(
                "INSERT INTO secrets VALUES (?, ?, ?, ?, ?) ON CONFLICT(scope, name) DO UPDATE SET "
                "ciphertext=excluded.ciphertext, updated_at=excluded.updated_at, expires_at=excluded.expires_at",
                (scope, name, ciphertext, now, now + ttl if ttl is not None else None),
            )

    def get(self, scope: str, name: str) -> Any:
        path = db.database(self.paths)
        if not path.exists():
            return None
        with db.connect(path, readonly=True) as connection:
            row = connection.execute(
                "SELECT ciphertext, expires_at FROM secrets WHERE scope=? AND name=?", (scope, name)
            ).fetchone()
        if row is None or (row["expires_at"] is not None and row["expires_at"] <= time.time()):
            return None
        try:
            return json.loads(self._cipher().decrypt(row["ciphertext"]))
        except (InvalidToken, ValueError):
            raise SecretError("The saved secret could not be read.") from None

    def delete(self, scope: str, name: str) -> bool:
        if not db.database(self.paths).exists():
            return False
        with db.connect(db.database(self.paths)) as connection:
            return bool(connection.execute("DELETE FROM secrets WHERE scope=? AND name=?", (scope, name)).rowcount)

    def list_names(self, scope: str) -> list[str]:
        path = db.database(self.paths)
        if not path.exists():
            return []
        with db.connect(path, readonly=True) as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM secrets WHERE scope=? AND (expires_at IS NULL OR expires_at>?) ORDER BY name",
                    (scope, time.time()),
                )
            ]

    def cleanup(self, scope: str | None = None) -> list[str]:
        if not db.database(self.paths).exists():
            return []
        condition = "expires_at<=?" + (" AND scope=?" if scope else "")
        parameters = (time.time(), scope) if scope else (time.time(),)
        with db.connect(db.database(self.paths)) as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM secrets WHERE " + condition, parameters)]
            connection.execute("DELETE FROM secrets WHERE " + condition, parameters)
        return names
