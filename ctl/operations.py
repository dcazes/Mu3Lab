"""The durable journal of an app's backup, restore and update (R09).

Before each step that changes an app (stopping it, saving a backup, swapping
its data or release, putting the old ones back) the worker records the step
it is about to take and everything needed to undo it: the release that was
installed, the backup holding the data from just before, and whether the app
was running. A worker that restarts mid-operation reads this record, not the
dashboard's latest view, to decide whether to carry on or put things back.

An operation ends in exactly one state:

- ``succeeded``: the change was made and verified.
- ``failed``: nothing needed undoing, or the requested data is in place but
  the app did not start; ``detail`` says which.
- ``rolled_back``: a failed update or restore was undone and verified.
- ``cancelled``: stopped before anything changed.
- ``needs_attention``: putting things back failed too. The app is blocked
  for everything except retrying recovery or restoring a backup, until one
  of those succeeds and marks it ``resolved``.

While an operation is ``active`` or ``needs_attention``, the backups it names
must not be pruned; see ``protected``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ctl.jobs import redact
from ctl.lifecycle.app_releases import Release
from ctl.runtime import RuntimePaths
from ctl.store import db

Kind = Literal["backup", "restore", "update"]
State = Literal["active", "succeeded", "failed", "rolled_back", "cancelled", "needs_attention", "resolved"]
TERMINAL = frozenset({"succeeded", "failed", "rolled_back", "cancelled", "resolved"})
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _release_text(release: Release | None) -> str:
    return json.dumps({"version": release.version, "images": release.images}, sort_keys=True) if release else ""


def _release(text: str) -> Release | None:
    if not text:
        return None
    value = json.loads(text)
    return Release(str(value.get("version") or ""), {str(k): str(v) for k, v in (value.get("images") or {}).items()})


@dataclass(frozen=True)
class Operation:
    id: str
    job_id: str
    service_id: str
    kind: Kind
    phase: str
    state: State
    was_running: bool
    previous_release: Release | None
    target_release: Release | None
    chosen_snapshot: str
    recovery_snapshot: str
    attempt: int
    detail: str
    created_at: str
    updated_at: str

    @classmethod
    def load(cls, row: Any) -> Operation:
        return cls(
            id=str(row["id"]),
            job_id=str(row["job_id"]),
            service_id=str(row["service_id"]),
            kind=row["kind"],
            phase=str(row["phase"]),
            state=row["state"],
            was_running=bool(row["was_running"]),
            previous_release=_release(str(row["previous_release"])),
            target_release=_release(str(row["target_release"])),
            chosen_snapshot=str(row["chosen_snapshot"]),
            recovery_snapshot=str(row["recovery_snapshot"]),
            attempt=int(row["attempt"]),
            detail=str(row["detail"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


class OperationStore:
    """Lives in the job store's database: every operation belongs to one job."""

    def __init__(self, database: Path) -> None:
        self.database = database

    @classmethod
    def runtime(cls, paths: RuntimePaths | None = None) -> OperationStore | None:
        paths = paths or RuntimePaths()
        return cls(db.database(paths)) if paths.runtime.is_dir() else None

    def _connect(self):
        return db.connect(self.database)

    def _step(self, connection, operation_id: str, phase: str, event: str, detail: str = "") -> None:
        seq = connection.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM operation_steps WHERE operation_id=?", (operation_id,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO operation_steps VALUES (?,?,?,?,?,?)",
            (operation_id, seq, phase, event, redact(detail)[:500], _now()),
        )

    def for_job(self, job_id: str) -> Operation | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM operations WHERE job_id=?", (job_id,)).fetchone()
        return Operation.load(row) if row else None

    def get(self, operation_id: str) -> Operation | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        return Operation.load(row) if row else None

    def begin(
        self,
        job_id: str,
        service_id: str,
        kind: Kind,
        *,
        was_running: bool,
        previous_release: Release | None,
        target_release: Release | None = None,
        chosen_snapshot: str = "",
    ) -> tuple[Operation, bool]:
        """Start this job's operation, or return the one it already started.

        Returns ``(operation, resumed)``; a resumed operation keeps everything
        its first attempt recorded and counts one more attempt.
        """
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM operations WHERE job_id=?", (job_id,)).fetchone()
            if row:
                connection.execute("UPDATE operations SET attempt=attempt+1, updated_at=? WHERE id=?", (now, row["id"]))
                self._step(connection, str(row["id"]), str(row["phase"]), "resumed")
                row = connection.execute("SELECT * FROM operations WHERE id=?", (row["id"],)).fetchone()
                return Operation.load(row), True
            connection.execute(
                "INSERT INTO operations (id, job_id, service_id, kind, schema_version, phase, state, was_running, "
                "previous_release, target_release, chosen_snapshot, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    job_id,
                    service_id,
                    kind,
                    SCHEMA_VERSION,
                    "prepare",
                    "active",
                    int(was_running),
                    _release_text(previous_release),
                    _release_text(target_release),
                    chosen_snapshot,
                    now,
                    now,
                ),
            )
            self._step(connection, job_id, "prepare", "begin")
            row = connection.execute("SELECT * FROM operations WHERE id=?", (job_id,)).fetchone()
        return Operation.load(row), False

    def advance(
        self,
        operation_id: str,
        phase: str,
        *,
        recovery_snapshot: str | None = None,
        target_release: Release | None = None,
    ) -> Operation:
        """Record the step about to be taken, and what undoing it needs, before taking it."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if recovery_snapshot is not None:
                connection.execute(
                    "UPDATE operations SET recovery_snapshot=? WHERE id=?", (recovery_snapshot, operation_id)
                )
            if target_release is not None:
                connection.execute(
                    "UPDATE operations SET target_release=? WHERE id=?",
                    (_release_text(target_release), operation_id),
                )
            connection.execute("UPDATE operations SET phase=?, updated_at=? WHERE id=?", (phase, _now(), operation_id))
            self._step(connection, operation_id, phase, "begin")
            row = connection.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        return Operation.load(row)

    def finish(self, operation_id: str, state: State, detail: str = "") -> None:
        if state == "active":
            raise ValueError("an operation finishes in a final state")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT phase FROM operations WHERE id=?", (operation_id,)).fetchone()
            if not row:
                raise KeyError(operation_id)
            connection.execute(
                "UPDATE operations SET state=?, detail=?, updated_at=? WHERE id=?",
                (state, redact(detail)[:1000], _now(), operation_id),
            )
            self._step(connection, operation_id, str(row["phase"]), "finished", state)

    def needing_attention(self, service_id: str) -> Operation | None:
        """The oldest unresolved failed recovery for this app, if any."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE service_id=? AND state='needs_attention' ORDER BY created_at LIMIT 1",
                (service_id,),
            ).fetchone()
        return Operation.load(row) if row else None

    def resolve(self, service_id: str, detail: str) -> int:
        """Mark every failed recovery of this app resolved, after a later success."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, phase FROM operations WHERE service_id=? AND state='needs_attention'", (service_id,)
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE operations SET state='resolved', detail=?, updated_at=? WHERE id=?",
                    (redact(detail)[:1000], _now(), row["id"]),
                )
                self._step(connection, str(row["id"]), str(row["phase"]), "finished", "resolved")
        return len(rows)

    def count_attempt(self, operation_id: str) -> int:
        """Count one more attempt (a recovery being queued) and return the total."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE operations SET attempt=attempt+1, updated_at=? WHERE id=?", (_now(), operation_id)
            )
            row = connection.execute("SELECT attempt FROM operations WHERE id=?", (operation_id,)).fetchone()
        return int(row[0]) if row else 0

    def orphaned(self) -> list[Operation]:
        """Active operations whose job ended without finishing them (a crash or a forced cancel)."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT o.* FROM operations o JOIN jobs j ON j.id = o.job_id "
                "WHERE o.state='active' AND j.state IN ('succeeded', 'failed', 'cancelled')"
            ).fetchall()
        return [Operation.load(row) for row in rows]

    def protected(self, service_id: str, *, excluding: str = "") -> set[str]:
        """Backups an unfinished or unresolved operation of this app still relies on."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT chosen_snapshot, recovery_snapshot FROM operations "
                "WHERE service_id=? AND state IN ('active', 'needs_attention') AND id != ?",
                (service_id, excluding),
            ).fetchall()
        return {value for row in rows for value in (row[0], row[1]) if value}

    def steps(self, operation_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM operation_steps WHERE operation_id=? ORDER BY seq", (operation_id,)
            ).fetchall()
        return [dict(row) for row in rows]
