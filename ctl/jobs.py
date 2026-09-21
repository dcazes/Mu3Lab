"""Durable, secret-free control-plane job and audit storage.

Jobs live only below /srv/mu3lab/runtime.  This module deliberately has no
Docker, Compose, shell, or HTTP dependency: an authenticated executor will be
added only after it can use these records for locks, approvals, redacted logs,
and auditability.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ctl.runtime import RuntimePaths

_SECRET = re.compile(
    r'''(?ix)(
    ["']?(?:password|token|secret|api[_-]?key|cookie|set-cookie|authorization)["']?
        \s*[:=]\s*["']?(?:bearer\s+)?[^\s,;}"']+
    |authorization\s*:\s*bearer\s+\S+
    |https?://[^\s:/]+:[^\s@/]+@[^\s]+
    )'''
)
_PRIVATE_DIAGNOSTIC = re.compile(
    r"(?ix)("
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
    r"|[A-Z0-9.-]+\.ts\.net"
    r"|/(?:home|srv|opt|var|tmp)/[^\s,;:'\"]+"
    r"|\b(?:\d{1,3}\.){3}\d{1,3}\b"
    r")"
)
_SAFE_KINDS = frozenset({"lifecycle", "backup", "restore", "wiring", "update", "verification"})
_SAFE_STATES = frozenset({"queued", "running", "waiting_for_confirmation", "succeeded", "failed", "cancelled"})
_TRANSITIONS = {
    "queued": frozenset({"running", "waiting_for_confirmation", "failed", "cancelled"}),
    "running": frozenset({"waiting_for_confirmation", "succeeded", "failed", "cancelled"}),
    "waiting_for_confirmation": frozenset({"queued", "running", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def redact(value: str) -> str:
    """Remove common credential assignments before any record reaches SQLite."""
    secret_free = _SECRET.sub("[redacted]", value)
    return _PRIVATE_DIAGNOSTIC.sub("[private]", secret_free)[:4000]


def redact_data(value: Any) -> Any:
    """Recursively sanitize structured workflow inputs before serialization."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            clean[name] = ("[redacted]" if re.search(
                r"(?i)(password|token|secret|api[_-]?key|cookie|authorization)", name)
                else redact_data(item))
        return clean
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[unsupported]"


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
        conn = sqlite3.connect(self.database, timeout=15)
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
            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                event TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
        """)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        additions = {
            "idempotency_key": "TEXT",
            "step_id": "TEXT NOT NULL DEFAULT ''",
            "lease_owner": "TEXT NOT NULL DEFAULT ''",
            "lease_expires_at": "TEXT NOT NULL DEFAULT ''",
            "heartbeat_at": "TEXT NOT NULL DEFAULT ''",
            "error_code": "TEXT NOT NULL DEFAULT ''",
        }
        for name, declaration in additions.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_key "
                     "ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL")
        return conn

    def create(self, *, kind: str, service_id: str, action: str, actor: str,
               detail: str = "", idempotency_key: str | None = None) -> dict[str, str]:
        """Create a queued job. Callers must authenticate and authorize first."""
        if kind not in _SAFE_KINDS or not service_id or not action or not actor:
            raise ValueError("invalid safe job request")
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 128):
            raise ValueError("invalid idempotency key")
        job_id, now = uuid4().hex, _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                row = conn.execute("SELECT id, state, created_at FROM jobs WHERE idempotency_key = ?",
                                   (idempotency_key,)).fetchone()
                if row:
                    return dict(row)
            active = conn.execute(
                "SELECT id, state, created_at FROM jobs WHERE service_id = ? "
                "AND state IN ('queued', 'running', 'waiting_for_confirmation') "
                "ORDER BY created_at DESC LIMIT 1", (service_id,)).fetchone()
            if active:
                return dict(active)
            conn.execute("""
                INSERT INTO jobs
                (id, kind, service_id, action, state, actor, created_at, updated_at,
                 detail, idempotency_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, kind, service_id, action, "queued", actor, now, now,
                  redact(detail), idempotency_key))
            conn.execute("INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                         (job_id, actor, "job.created", now, redact(f"{kind}:{action}")))
        return {"id": job_id, "state": "queued", "created_at": now}

    def transition(self, job_id: str, state: str, *, actor: str, detail: str = "",
                   error_code: str = "", step_id: str = "") -> None:
        """Move an existing job to a reviewed state and append its audit event."""
        if state not in _SAFE_STATES or not actor:
            raise ValueError("invalid job state transition")
        now = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            current = str(row["state"])
            if state != current and state not in _TRANSITIONS.get(current, frozenset()):
                raise ValueError(f"invalid job transition: {current} -> {state}")
            conn.execute("""
                UPDATE jobs SET state = ?, updated_at = ?, detail = ?, error_code = ?,
                    step_id = ?, lease_owner = CASE WHEN ? = 'running' THEN lease_owner ELSE '' END,
                    lease_expires_at = CASE WHEN ? = 'running' THEN lease_expires_at ELSE '' END
                WHERE id = ?
            """, (state, now, redact(detail), error_code[:64], step_id[:64], state, state, job_id))
            conn.execute("INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                         (job_id, actor, f"job.{state}", now, redact(detail)))

    def claim(self, worker_id: str, *, lease_seconds: int = 45) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job or reclaim an expired runner."""
        if not worker_id or lease_seconds < 10:
            raise ValueError("invalid worker lease")
        now = _now()
        expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT * FROM jobs
                WHERE state = 'queued'
                   OR (state = 'running' AND lease_expires_at != '' AND lease_expires_at < ?)
                ORDER BY created_at ASC LIMIT 1
            """, (now,)).fetchone()
            if not row:
                return None
            conn.execute("""
                UPDATE jobs SET state = 'running', lease_owner = ?, lease_expires_at = ?,
                    heartbeat_at = ?, updated_at = ? WHERE id = ?
            """, (worker_id, expires, now, now, row["id"]))
            conn.execute("INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                         (row["id"], worker_id, "job.claimed", now, "Durable worker claimed the job."))
            claimed = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        return dict(claimed) if claimed else None

    def heartbeat(self, job_id: str, worker_id: str, *, lease_seconds: int = 45,
                  step_id: str = "") -> bool:
        now = _now()
        expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        with self._connect() as conn:
            result = conn.execute("""
                UPDATE jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?,
                    step_id = CASE WHEN ? != '' THEN ? ELSE step_id END
                WHERE id = ? AND state = 'running' AND lease_owner = ?
            """, (now, expires, now, step_id[:64], step_id[:64], job_id, worker_id))
        return result.rowcount == 1

    def append_event(self, job_id: str, event: str, detail: str) -> None:
        if not event or len(event) > 64:
            raise ValueError("invalid job event")
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone():
                raise KeyError(job_id)
            conn.execute("INSERT INTO job_events (job_id, event, created_at, detail) VALUES (?, ?, ?, ?)",
                         (job_id, event, _now(), redact(detail)))

    def events(self, job_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, job_id, event, created_at, detail FROM job_events
                WHERE job_id = ? ORDER BY id DESC LIMIT ?
            """, (job_id, max(1, min(limit, 1000)))).fetchall()
        return [dict(row) for row in reversed(rows)]

    def jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [dict(row) for row in rows]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        """Return the original mutation result record for safe HTTP retries."""
        if not key:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def retry(self, job_id: str, *, actor: str, idempotency_key: str | None = None) -> dict[str, str]:
        """Queue a new attempt from a retryable terminal/waiting record."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        if row["state"] not in {"failed", "waiting_for_confirmation"}:
            raise ValueError("job is not retryable")
        if row["state"] == "waiting_for_confirmation":
            self.transition(job_id, "cancelled", actor=actor,
                            detail="Superseded by an explicit retry.")
        return self.create(kind=str(row["kind"]), service_id=str(row["service_id"]),
                           action=str(row["action"]), actor=actor,
                           detail=f"Retry of job {job_id}", idempotency_key=idempotency_key)

    def cancel(self, job_id: str, *, actor: str) -> None:
        """Request cancellation at the next executor boundary."""
        self.transition(job_id, "cancelled", actor=actor,
                        detail="Cancellation requested by the operator.")

    def audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, job_id, actor, event, created_at, detail FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [dict(row) for row in rows]

    def record_audit(self, *, actor: str, event: str, detail: str = "",
                     job_id: str | None = None) -> None:
        """Append a non-job configuration audit record after authorization."""
        if not actor or not event or len(event) > 96:
            raise ValueError("invalid audit record")
        with self._connect() as conn:
            if job_id and not conn.execute(
                    "SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone():
                raise KeyError(job_id)
            conn.execute(
                "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                (job_id, actor, event, _now(), redact(detail)),
            )
