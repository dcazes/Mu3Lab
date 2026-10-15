"""The one note a restore or an update leaves while it has changed an app.

A restore or update writes this note just before it stops the app, and
removes it once the app is healthy again or has been put back. Nothing ever
continues from it automatically. If the note is still there with no job
running on the app, the job was interrupted (a power cut, a crash): the
dashboard shows "Needs attention" and the owner chooses what happens, with
putting the app back as it was as the safe default.

The note holds only what putting the app back needs: the deployment copy
taken before the change, the backup of the data from before (updates), the
folders swapped aside (restores) and whether the app was running.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from ctl.runtime import RuntimePaths
from ctl.store import records

SCOPE = "in-progress"
Kind = Literal["restore", "update"]


def begin(service_id: str, kind: Kind, job_id: str, paths: RuntimePaths | None = None, **fields: Any) -> dict:
    note = {"kind": kind, "job_id": job_id, "started_at": datetime.now(UTC).isoformat(timespec="seconds"), **fields}
    records.put(SCOPE, service_id, note, paths or RuntimePaths())
    return note


def update(service_id: str, paths: RuntimePaths | None = None, **fields: Any) -> dict:
    paths = paths or RuntimePaths()
    note = {**records.get(SCOPE, service_id, paths), **fields}
    records.put(SCOPE, service_id, note, paths)
    return note


def get(service_id: str, paths: RuntimePaths | None = None) -> dict:
    return records.get(SCOPE, service_id, paths or RuntimePaths())


def clear(service_id: str, paths: RuntimePaths | None = None) -> None:
    records.delete(SCOPE, service_id, paths or RuntimePaths())


def interrupted(service_id: str, active_job_ids: set[str], paths: RuntimePaths | None = None) -> dict:
    """The note of a restore or update that stopped part-way, or {} while its job still runs."""
    note = get(service_id, paths)
    if not note or str(note.get("job_id") or "") in active_job_ids:
        return {}
    return note


def all_interrupted(active_job_ids: set[str], paths: RuntimePaths | None = None) -> dict[str, dict]:
    paths = paths or RuntimePaths()
    found = {}
    for service_id in records.names(SCOPE, paths):
        note = interrupted(service_id, active_job_ids, paths)
        if note:
            found[service_id] = note
    return found
