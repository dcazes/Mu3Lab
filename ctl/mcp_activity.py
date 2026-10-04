"""Metadata-only MCP call history, per-tool policy, and one-use write approvals."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ctl.store import db as database_store


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class McpActivity:
    def __init__(self, path: Path | None = None):
        self.path = path or database_store.database()

    @contextmanager
    def _db(self):
        with database_store.connect(self.path) as connection:
            yield connection

    def history(self, server_id: str, *, before: int = 0, limit: int = 50) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM calls WHERE server_id=? AND (?=0 OR id<?) ORDER BY id DESC LIMIT ?",
                (server_id, before, before, max(1, min(limit, 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def record(
        self, server_id: str, tool_name: str, source: str, actor: str, outcome: str, duration_ms: int, detail: str = ""
    ) -> None:
        # detail is an enum-like diagnostic, never upstream exception text or content.
        with self._db() as db:
            db.execute(
                "INSERT INTO calls (server_id,tool_name,source,actor,outcome,duration_ms,created_at,detail) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (server_id, tool_name, source, actor, outcome, max(0, duration_ms), _now(), detail[:100]),
            )

    def ingest_chat_call(
        self, source_ref: str, server_id: str, tool_name: str, actor: str, outcome: str, created_at: str
    ) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO calls "
                "(server_id,tool_name,source,actor,outcome,duration_ms,created_at,detail,source_ref) "
                "VALUES (?,?, 'lobechat', ?, ?, 0, ?, '', ?)",
                (server_id, tool_name, actor, outcome, created_at, source_ref),
            )

    def permission(self, server_id: str, tool_name: str, risk: str) -> str:
        default = "auto" if risk == "read" else "needs_approval"
        # Dashboard snapshots are read-only and can run before bootstrap has
        # created /srv/mu3lab. Do not create a persistent runtime tree just to
        # project a default permission into those responses.
        if not self.path.is_file():
            return default
        try:
            db = database_store.connect(self.path, readonly=True, timeout=5)
            try:
                row = db.execute(
                    "SELECT permission FROM tool_permissions WHERE server_id=? AND tool_name=?", (server_id, tool_name)
                ).fetchone()
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            return default
        return str(row[0]) if row else default

    def _read_only(self, sql: str, params: tuple) -> list[sqlite3.Row]:
        """Query without creating the runtime tree; a missing store means no choices yet."""
        if not self.path.is_file():
            return []
        try:
            db = database_store.connect(self.path, readonly=True, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                return db.execute(sql, params).fetchall()
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            return []

    def explicit_permissions(self, server_id: str) -> dict[str, str]:
        """Only the tool choices the owner actually made, without defaults."""
        rows = self._read_only("SELECT tool_name, permission FROM tool_permissions WHERE server_id=?", (server_id,))
        return {str(row["tool_name"]): str(row["permission"]) for row in rows}

    def category_switches(self, server_id: str) -> dict[str, bool]:
        rows = self._read_only("SELECT category_id, enabled FROM category_switches WHERE server_id=?", (server_id,))
        return {str(row["category_id"]): bool(row["enabled"]) for row in rows}

    def set_category(self, server_id: str, category_id: str, enabled: bool) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO category_switches VALUES (?,?,?,?) ON CONFLICT(server_id,category_id) "
                "DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at",
                (server_id, category_id, int(enabled), _now()),
            )

    def set_permission(self, server_id: str, tool_name: str, permission: str) -> None:
        if permission not in {"auto", "needs_approval", "disabled"}:
            raise ValueError("invalid tool permission")
        with self._db() as db:
            db.execute(
                "INSERT INTO tool_permissions VALUES (?,?,?,?) ON CONFLICT(server_id,tool_name) "
                "DO UPDATE SET permission=excluded.permission, updated_at=excluded.updated_at",
                (server_id, tool_name, permission, _now()),
            )

    @staticmethod
    def input_hash(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def prepare(self, server_id: str, tool_name: str, actor: str, payload: dict) -> str:
        nonce = secrets.token_urlsafe(32)
        expiry = (datetime.now(UTC) + timedelta(minutes=5)).isoformat(timespec="seconds")
        with self._db() as db:
            db.execute("DELETE FROM write_confirmations WHERE expires_at < ?", (_now(),))
            db.execute(
                "INSERT INTO write_confirmations VALUES (?,?,?,?,?,?)",
                (nonce, server_id, tool_name, actor, self.input_hash(payload), expiry),
            )
        return nonce

    def consume(
        self, nonce: str, server_id: str, tool_name: str, actor: str, payload: dict, idempotency_key: str
    ) -> bool:
        if not nonce or not idempotency_key:
            return False
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM write_confirmations WHERE nonce=?", (nonce,)).fetchone()
            if (
                not row
                or row["server_id"] != server_id
                or row["tool_name"] != tool_name
                or row["actor"] != actor
                or row["input_hash"] != self.input_hash(payload)
                or row["expires_at"] < _now()
            ):
                return False
            db.execute("DELETE FROM write_confirmations WHERE nonce=?", (nonce,))
            try:
                db.execute("INSERT INTO call_keys VALUES (?,?)", (idempotency_key, _now()))
            except sqlite3.IntegrityError:
                return False
        return True
