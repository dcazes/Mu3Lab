"""Durable, secret-free control-plane job and audit storage.

Jobs live only below /srv/mu3lab/state.  This module deliberately has no
Docker, Compose, shell, or HTTP dependency: an authenticated executor will be
added only after it can use these records for locks, approvals, redacted logs,
and auditability.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ctl.runtime import RuntimePaths
from ctl.store import db

_SECRET = re.compile(
    r"""(?ix)(
    ["']?(?:password|token|secret|api[_-]?key|cookie|set-cookie|authorization)["']?
        \s*[:=]\s*["']?(?:bearer\s+)?[^\s,;}"']+
    |authorization\s*:\s*bearer\s+\S+
    |https?://[^\s:/]+:[^\s@/]+@[^\s]+
    )"""
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
            clean[name] = (
                "[redacted]"
                if re.search(r"(?i)(password|token|secret|api[_-]?key|cookie|authorization)", name)
                else redact_data(item)
            )
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


def job_params(job: dict[str, Any]) -> dict[str, str]:
    """The non-secret inputs a job was queued with."""
    try:
        value = json.loads(str(job.get("params_json") or "{}"))
    except ValueError:
        return {}
    return {str(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobStore:
    """Small SQLite store with explicit state transitions and append-only audit."""

    def __init__(self, database: Path) -> None:
        self.database = database

    @classmethod
    def runtime(cls, paths: RuntimePaths = RuntimePaths()) -> JobStore | None:
        """Return a store only when bootstrap created the approved runtime dir."""
        return cls(db.database(paths)) if paths.runtime.is_dir() else None

    def _connect(self) -> sqlite3.Connection:
        return db.connect(self.database)

    def create(
        self,
        *,
        kind: str,
        service_id: str,
        action: str,
        actor: str,
        detail: str = "",
        idempotency_key: str | None = None,
        prepare: Callable[[str], None] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Create a queued job. Callers must authenticate and authorize first.

        ``params`` holds small, non-secret worker inputs; secrets belong in
        ``ctl.store.workflows``.

        ``prepare(job_id)`` runs inside the insert's transaction, before the job
        can be claimed, and only when a new job is actually created; if it
        raises, no job is created.
        """
        if kind not in _SAFE_KINDS or not service_id or not action or not actor:
            raise ValueError("invalid safe job request")
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 128):
            raise ValueError("invalid idempotency key")
        job_id, now = uuid4().hex, _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                row = conn.execute(
                    "SELECT id, state, created_at FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if row:
                    return dict(row)
            active = conn.execute(
                "SELECT id, state, created_at FROM jobs WHERE service_id = ? "
                "AND state IN ('queued', 'running', 'waiting_for_confirmation') "
                "ORDER BY created_at DESC LIMIT 1",
                (service_id,),
            ).fetchone()
            if active:
                return dict(active)
            if prepare:
                prepare(job_id)
            conn.execute(
                """
                INSERT INTO jobs
                (id, kind, service_id, action, state, actor, created_at, updated_at,
                 detail, idempotency_key, params_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    job_id,
                    kind,
                    service_id,
                    action,
                    "queued",
                    actor,
                    now,
                    now,
                    redact(detail),
                    idempotency_key,
                    json.dumps(redact_data(params or {}), sort_keys=True),
                ),
            )
            conn.execute(
                "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                (job_id, actor, "job.created", now, redact(f"{kind}:{action}")),
            )
        return {"id": job_id, "state": "queued", "created_at": now}

    def transition(
        self, job_id: str, state: str, *, actor: str, detail: str = "", error_code: str = "", step_id: str = ""
    ) -> None:
        """Move an existing job to a reviewed state and append its audit event.

        While a worker runs the job, only that worker's lease may move it, and
        a cancelled job cannot be moved on; both raise JobInterrupted.
        """
        from ctl import job_guard

        if state not in _SAFE_STATES or not actor:
            raise ValueError("invalid job state transition")
        execution = job_guard.current()
        runner = execution.worker_id if execution and execution.job_id == job_id else ""
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state, lease_owner, cancel_requested_at FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not row:
                raise KeyError(job_id)
            current = str(row["state"])
            if runner:
                if current == "running" and row["lease_owner"] != runner:
                    raise job_guard.JobInterrupted(job_guard.LEASE_LOST)
                if current == "cancelled" and state != "cancelled":
                    raise job_guard.JobInterrupted(job_guard.CANCELLED)
                if row["cancel_requested_at"] and state in {"running", "waiting_for_confirmation"}:
                    raise job_guard.JobInterrupted(job_guard.CANCELLED)
            if state != current and state not in _TRANSITIONS.get(current, frozenset()):
                raise ValueError(f"invalid job transition: {current} -> {state}")
            conn.execute(
                """
                UPDATE jobs SET state = ?, updated_at = ?, detail = ?, error_code = ?,
                    step_id = ?, lease_owner = CASE WHEN ? = 'running' THEN lease_owner ELSE '' END,
                    lease_expires_at = CASE WHEN ? = 'running' THEN lease_expires_at ELSE '' END
                WHERE id = ?
            """,
                (state, now, redact(detail), error_code[:64], step_id[:64], state, state, job_id),
            )
            conn.execute(
                "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                (job_id, actor, f"job.{state}", now, redact(detail)),
            )

    def claim(self, worker_id: str, *, lease_seconds: int = 45) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job or reclaim an expired runner."""
        if not worker_id or lease_seconds < 10:
            raise ValueError("invalid worker lease")
        now = _now()
        expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # A job whose runner died after someone asked to cancel it is
            # finished as cancelled, never started again.
            abandoned = conn.execute(
                """
                SELECT id FROM jobs WHERE state = 'running' AND cancel_requested_at != ''
                  AND lease_expires_at != '' AND lease_expires_at < ?
            """,
                (now,),
            ).fetchall()
            for item in abandoned:
                conn.execute(
                    """
                    UPDATE jobs SET state = 'cancelled', lease_owner = '', lease_expires_at = '',
                        updated_at = ?, detail = 'Cancelled; its worker stopped before finishing.' WHERE id = ?
                """,
                    (now, item["id"]),
                )
                conn.execute(
                    "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                    (item["id"], worker_id, "job.cancelled", now, "Cancelled after its worker's lease expired."),
                )
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE state = 'queued'
                   OR (state = 'running' AND lease_expires_at != '' AND lease_expires_at < ?)
                ORDER BY created_at ASC LIMIT 1
            """,
                (now,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE jobs SET state = 'running', lease_owner = ?, lease_expires_at = ?,
                    heartbeat_at = ?, updated_at = ? WHERE id = ?
            """,
                (worker_id, expires, now, now, row["id"]),
            )
            conn.execute(
                "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                (row["id"], worker_id, "job.claimed", now, "Durable worker claimed the job."),
            )
            claimed = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        return dict(claimed) if claimed else None

    def heartbeat(self, job_id: str, worker_id: str, *, lease_seconds: int = 45, step_id: str = "") -> bool:
        now = _now()
        expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?,
                    step_id = CASE WHEN ? != '' THEN ? ELSE step_id END
                WHERE id = ? AND state = 'running' AND lease_owner = ?
            """,
                (now, expires, now, step_id[:64], step_id[:64], job_id, worker_id),
            )
        return result.rowcount == 1

    def append_event(self, job_id: str, event: str, detail: str) -> None:
        from ctl import job_guard

        # Steps log as they go; this is the most frequent safe place to stop.
        job_guard.checkpoint()
        if not event or len(event) > 64:
            raise ValueError("invalid job event")
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone():
                raise KeyError(job_id)
            conn.execute(
                "INSERT INTO job_events (job_id, event, created_at, detail) VALUES (?, ?, ?, ?)",
                (job_id, event, _now(), redact(detail)),
            )

    def events(self, job_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, job_id, event, created_at, detail FROM job_events
                WHERE job_id = ? ORDER BY id DESC LIMIT ?
            """,
                (job_id, max(1, min(limit, 1000))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def active_jobs(self) -> list[dict[str, Any]]:
        """Every job still queued, running or waiting, however old."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE state IN ('queued', 'running', 'waiting_for_confirmation') "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def jobs_for_service(self, service_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE service_id=? ORDER BY created_at DESC LIMIT ?",
                (service_id, max(1, min(limit, 100))),
            ).fetchall()
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

    def retry(
        self,
        job_id: str,
        *,
        actor: str,
        idempotency_key: str | None = None,
        prepare: Callable[[str], None] | None = None,
    ) -> dict[str, str]:
        """Queue a new attempt from a retryable terminal/waiting record."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        if row["state"] not in {"failed", "waiting_for_confirmation"}:
            raise ValueError("job is not retryable")
        if row["state"] == "waiting_for_confirmation":
            self.transition(job_id, "cancelled", actor=actor, detail="Superseded by an explicit retry.")
        return self.create(
            kind=str(row["kind"]),
            service_id=str(row["service_id"]),
            action=str(row["action"]),
            actor=actor,
            detail=f"Retry of job {job_id}",
            idempotency_key=idempotency_key,
            prepare=prepare,
            params=job_params(dict(row)),
        )

    def cancel(self, job_id: str, *, actor: str) -> str:
        """Cancel a job; a running one stops at its next checkpoint.

        Returns the job's state: "cancelled", or "cancelling" while its worker
        finishes the command it is in and acknowledges.
        """
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row["state"] == "running":
                conn.execute(
                    "UPDATE jobs SET cancel_requested_at = ?, updated_at = ? WHERE id = ? AND cancel_requested_at = ''",
                    (now, now, job_id),
                )
                conn.execute(
                    "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                    (job_id, actor, "job.cancel_requested", now, "Cancellation requested by the operator."),
                )
                return "cancelling"
        self.transition(job_id, "cancelled", actor=actor, detail="Cancellation requested by the operator.")
        return "cancelled"

    def interruption(self, job_id: str, worker_id: str) -> str:
        """Why the runner ``worker_id`` must stop this job now, or "" to carry on."""
        from ctl import job_guard

        with self._connect() as conn:
            row = conn.execute(
                "SELECT state, lease_owner, cancel_requested_at FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if not row:
            return job_guard.LEASE_LOST
        if row["state"] == "cancelled":
            return job_guard.CANCELLED
        if row["state"] == "queued" or (row["state"] == "running" and row["lease_owner"] != worker_id):
            return job_guard.LEASE_LOST
        if row["state"] == "running" and row["cancel_requested_at"]:
            return job_guard.CANCELLED
        # waiting/succeeded/failed: the runner moved it there itself.
        return ""

    def acknowledge_cancel(self, job_id: str, worker_id: str) -> bool:
        """The runner stopped at a checkpoint; record the job as cancelled."""
        now = _now()
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE jobs SET state = 'cancelled', lease_owner = '', lease_expires_at = '', updated_at = ?,
                    detail = 'Cancelled. Steps already finished were kept; nothing after them ran.'
                WHERE id = ? AND state = 'running' AND lease_owner = ?
            """,
                (now, job_id, worker_id),
            )
            if result.rowcount:
                conn.execute(
                    "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                    (job_id, worker_id, "job.cancelled", now, "Worker stopped at a checkpoint."),
                )
        return result.rowcount == 1

    def audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, job_id, actor, event, created_at, detail FROM audit ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_audit(self, *, actor: str, event: str, detail: str = "", job_id: str | None = None) -> None:
        """Append a non-job configuration audit record after authorization."""
        if not actor or not event or len(event) > 96:
            raise ValueError("invalid audit record")
        with self._connect() as conn:
            if job_id and not conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone():
                raise KeyError(job_id)
            conn.execute(
                "INSERT INTO audit (job_id, actor, event, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                (job_id, actor, event, _now(), redact(detail)),
            )
