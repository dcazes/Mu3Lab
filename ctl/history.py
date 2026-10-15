"""How long Mu3Lab keeps its own history (review R28).

Job records stay: they are small, other records point at them, and each app's
newest jobs drive what its page shows. What grows is everything around them:

- a finished job's log lines, kept for ``LOGS``, except for the newest job of
  each kind on each app, whose log the app's page still shows;
- the chat tool-call log and its replay keys, kept for ``CALLS``;
- the audit log (who changed what), kept for ``AUDIT``.

Nothing a job still running or waiting needs is ever removed. The worker runs
``prune`` about once a day.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ctl.store import db

LOGS = timedelta(days=90)
CALLS = timedelta(days=90)
AUDIT = timedelta(days=365)
FINISHED = ("succeeded", "failed", "cancelled")


def _before(now: datetime, age: timedelta) -> str:
    return (now - age).isoformat(timespec="seconds")


def prune(database: Path, now: datetime | None = None) -> dict[str, int]:
    """Remove history older than its keeping time; returns how many rows each kind lost."""
    now = now or datetime.now(UTC)
    finished = ",".join("?" * len(FINISHED))
    with db.connect(database) as connection:
        logs = connection.execute(
            f"""
            DELETE FROM job_events WHERE job_id IN (
                SELECT id FROM jobs AS old
                WHERE state IN ({finished}) AND updated_at < ?
                  AND EXISTS (
                    SELECT 1 FROM jobs AS newer
                    WHERE newer.service_id = old.service_id AND newer.action = old.action
                      AND newer.created_at > old.created_at
                  )
            )
            """,
            (*FINISHED, _before(now, LOGS)),
        ).rowcount
        audit = connection.execute(
            f"""
            DELETE FROM audit WHERE created_at < ?
              AND (job_id IS NULL OR job_id IN (SELECT id FROM jobs WHERE state IN ({finished})))
            """,
            (_before(now, AUDIT), *FINISHED),
        ).rowcount
        calls = connection.execute("DELETE FROM calls WHERE created_at < ?", (_before(now, CALLS),)).rowcount
        keys = connection.execute("DELETE FROM call_keys WHERE created_at < ?", (_before(now, CALLS),)).rowcount
    return {"job_logs": logs, "audit": audit, "calls": calls + keys}
