"""Metadata-only MCP call history, per-tool policy, and one-use write approvals."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ctl.runtime import RuntimePaths


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class McpActivity:
    def __init__(self, path: Path | None = None):
        self.path = path or RuntimePaths().runtime / "mcp-activity.sqlite3"

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        os.chmod(self.path, 0o600)
        db.row_factory = sqlite3.Row
        db.executescript("""
          CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, server_id TEXT NOT NULL,
            tool_name TEXT NOT NULL, source TEXT NOT NULL, actor TEXT NOT NULL,
            outcome TEXT NOT NULL, duration_ms INTEGER NOT NULL,
            created_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
            source_ref TEXT
          );
          CREATE INDEX IF NOT EXISTS calls_server_id_id ON calls(server_id,id DESC);
          CREATE TABLE IF NOT EXISTS tool_permissions (
            server_id TEXT NOT NULL, tool_name TEXT NOT NULL,
            permission TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(server_id,tool_name)
          );
          CREATE TABLE IF NOT EXISTS write_confirmations (
            nonce TEXT PRIMARY KEY, server_id TEXT NOT NULL, tool_name TEXT NOT NULL,
            actor TEXT NOT NULL, input_hash TEXT NOT NULL, expires_at TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS call_keys (
            idempotency_key TEXT PRIMARY KEY, created_at TEXT NOT NULL
          );
        """)
        if "source_ref" not in {row[1] for row in db.execute("PRAGMA table_info(calls)")}:
            db.execute("ALTER TABLE calls ADD COLUMN source_ref TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS calls_source_ref ON calls(source_ref) WHERE source_ref IS NOT NULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def history(self, server_id: str, *, before: int = 0, limit: int = 50) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM calls WHERE server_id=? AND (?=0 OR id<?) ORDER BY id DESC LIMIT ?",
                (server_id, before, before, max(1, min(limit, 100)))).fetchall()
        return [dict(row) for row in rows]

    def record(self, server_id: str, tool_name: str, source: str, actor: str,
               outcome: str, duration_ms: int, detail: str = "") -> None:
        # detail is an enum-like diagnostic, never upstream exception text or content.
        with self._db() as db:
            db.execute("INSERT INTO calls (server_id,tool_name,source,actor,outcome,duration_ms,created_at,detail) "
                       "VALUES (?,?,?,?,?,?,?,?)",
                       (server_id, tool_name, source, actor, outcome, max(0, duration_ms),
                        _now(), detail[:100]))

    def ingest_chat_call(self, source_ref: str, server_id: str, tool_name: str,
                         actor: str, outcome: str, created_at: str) -> None:
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO calls "
                       "(server_id,tool_name,source,actor,outcome,duration_ms,created_at,detail,source_ref) "
                       "VALUES (?,?, 'lobechat', ?, ?, 0, ?, '', ?)",
                       (server_id, tool_name, actor, outcome, created_at, source_ref))

    def permission(self, server_id: str, tool_name: str, risk: str) -> str:
        default = "auto" if risk == "read" else "needs_approval"
        # Dashboard snapshots are read-only and can run before bootstrap has
        # created /srv/mu3lab. Do not create a persistent runtime tree just to
        # project a default permission into those responses.
        if not self.path.is_file():
            return default
        try:
            db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5)
            try:
                row = db.execute(
                    "SELECT permission FROM tool_permissions WHERE server_id=? AND tool_name=?",
                    (server_id, tool_name)).fetchone()
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            return default
        return str(row[0]) if row else default

    def set_permission(self, server_id: str, tool_name: str, permission: str) -> None:
        if permission not in {"auto", "needs_approval", "disabled"}:
            raise ValueError("invalid tool permission")
        with self._db() as db:
            db.execute("INSERT INTO tool_permissions VALUES (?,?,?,?) ON CONFLICT(server_id,tool_name) "
                       "DO UPDATE SET permission=excluded.permission, updated_at=excluded.updated_at",
                       (server_id, tool_name, permission, _now()))

    @staticmethod
    def input_hash(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def prepare(self, server_id: str, tool_name: str, actor: str, payload: dict) -> str:
        nonce = secrets.token_urlsafe(32)
        expiry = (datetime.now(UTC) + timedelta(minutes=5)).isoformat(timespec="seconds")
        with self._db() as db:
            db.execute("DELETE FROM write_confirmations WHERE expires_at < ?", (_now(),))
            db.execute("INSERT INTO write_confirmations VALUES (?,?,?,?,?,?)",
                       (nonce, server_id, tool_name, actor, self.input_hash(payload), expiry))
        return nonce

    def consume(self, nonce: str, server_id: str, tool_name: str, actor: str,
                payload: dict, idempotency_key: str) -> bool:
        if not nonce or not idempotency_key:
            return False
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM write_confirmations WHERE nonce=?", (nonce,)).fetchone()
            if not row or row["server_id"] != server_id or row["tool_name"] != tool_name or \
                    row["actor"] != actor or row["input_hash"] != self.input_hash(payload) or \
                    row["expires_at"] < _now():
                return False
            db.execute("DELETE FROM write_confirmations WHERE nonce=?", (nonce,))
            try:
                db.execute("INSERT INTO call_keys VALUES (?,?)", (idempotency_key, _now()))
            except sqlite3.IntegrityError:
                return False
        return True
