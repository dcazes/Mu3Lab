"""Durable, secret-free control-plane job and audit storage.

Jobs live only below /srv/mu3lab/runtime.  This module deliberately has no
Docker, Compose, shell, or HTTP dependency: an authenticated executor will be
added only after it can use these records for locks, approvals, redacted logs,
and auditability.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ctl.runtime import RuntimePaths

_SECRET = re.compile(r"(?i)(password|token|secret|api[_-]?key)\s*([=:])\s*\S+")
_SAFE_KINDS = frozenset({"lifecycle", "backup", "restore", "wiring", "update"})
_SAFE_STATES = frozenset({"queued", "running", "waiting_for_confirmation", "succeeded", "failed", "cancelled"})


def redact(value: str) -> str:
    """Remove common credential assignments before any record reaches SQLite."""
    return _SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)} [redacted]", value)[:1000]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobStore:
    """Small SQLite store with explicit state transitions and append-only audit."""

    def __init__(self, database: Path) -> None:
        self.database = database

    @classmethod
    def runtime(cls, paths: RuntimePaths = RuntimePaths()) -> "JobStore | None":
        """Return a store only when bootstrap created the approved runtime dir."""
        return cls(paths.runtime / "control-plane.sqlite3") if paths.runtime.is_dir() else None

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, service_id TEXT NOT NULL,
                action TEXT NOT NULL, state TEXT NOT NULL, actor TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, actor TEXT NOT NULL,
                event TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
        """)
        return conn

    def create(self, *, kind: str, service_id: str, action: str, actor: str, detail: str = "") -> dict[str, str]:
        """Create a queued job. Callers must authenticate and authorize first."""
        if kind not in _SAFE_KINDS or not service_id or not action or not actor:
            raise ValueError("invalid safe job request")
        job_id, now = uuid4().hex, _now()
        with self._connect() as conn:
            conn.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (job_id, kind, service_id, action, "queued", actor, now, now, redact(detail)))
            conn.execute("INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                         (job_id, actor, "job.created", now, redact(f"{kind}:{action}")))
        return {"id": job_id, "state": "queued", "created_at": now}

    def transition(self, job_id: str, state: str, *, actor: str, detail: str = "") -> None:
        """Move an existing job to a reviewed state and append its audit event."""
        if state not in _SAFE_STATES or not actor:
            raise ValueError("invalid job state transition")
        now = _now()
        with self._connect() as conn:
            exists = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not exists:
                raise KeyError(job_id)
            conn.execute("UPDATE jobs SET state = ?, updated_at = ?, detail = ? WHERE id = ?",
                         (state, now, redact(detail), job_id))
            conn.execute("INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                         (job_id, actor, f"job.{state}", now, redact(detail)))

    def jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [dict(row) for row in rows]

    def audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, job_id, actor, event, created_at, detail FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [dict(row) for row in rows]
