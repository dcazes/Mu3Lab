"""Scheduled backups: when they run, and queuing them as ordinary durable jobs.

The owner chooses how often (off, every 6 or 12 hours, daily or weekly), the
day for weekly runs and the time, in this server's local time. The default is
weekly on Sunday at 03:30.

A run queues the same jobs the dashboard's "Back up now" buttons queue: first
Mu3Lab itself and its core apps, then each installed app. The worker runs
them one at a time, with their usual locks, journal and crash recovery, so a
scheduled backup is never a second, different way of backing up. Each job's
idempotency key names the run, so a worker restart never queues a run twice.

A run the server missed (it was off) starts once the worker has been up for a
few minutes. Changing the settings never starts a run straight away: only a
scheduled time that passes after the change does.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any, Literal

from ctl import platform_backup
from ctl.control_state import ControlState
from ctl.jobs import JobConflict, JobStore
from ctl.lifecycle.uninstall import data_directories
from ctl.operations import OperationStore
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.store import records

Frequency = Literal["off", "6h", "12h", "daily", "weekly"]
FREQUENCIES: tuple[str, ...] = ("off", "6h", "12h", "daily", "weekly")
# Hours between runs for the frequencies that repeat within a day.
_EVERY = {"6h": 6, "12h": 12, "daily": 24}
DEFAULTS: dict[str, Any] = {"frequency": "weekly", "weekday": 6, "time": "03:30"}  # weekday: Monday is 0
SETTINGS = ("backup", "schedule")
LAST_RUN = ("backup", "schedule-run")
ACTOR = "schedule"
# A worker that has just started waits this long before catching up on a missed run.
SETTLE = timedelta(minutes=10)
# Apps in these states are backed up; others are mid-change and are skipped.
_BACKUP_STATES = frozenset({"ready", "running", "stopped"})


def _parse_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def validate(settings: dict[str, Any]) -> dict[str, Any]:
    """The settings as stored; raises ValueError for anything out of range."""
    frequency = str(settings.get("frequency", ""))
    if frequency not in FREQUENCIES:
        raise ValueError("Choose how often to back up.")
    weekday = settings.get("weekday", DEFAULTS["weekday"])
    if not isinstance(weekday, int) or isinstance(weekday, bool) or not 0 <= weekday <= 6:
        raise ValueError("Choose a day of the week.")
    value = str(settings.get("time", ""))
    try:
        if len(value) != 5 or value[2] != ":":
            raise ValueError
        _parse_time(value)
    except ValueError:
        raise ValueError("Choose a time as HH:MM, for example 03:30.") from None
    return {"frequency": frequency, "weekday": weekday, "time": value}


def settings(paths: RuntimePaths | None = None) -> dict[str, Any]:
    stored = records.get(*SETTINGS, paths or RuntimePaths())
    try:
        return {**validate({**DEFAULTS, **stored}), "changed_at": str(stored.get("changed_at") or "")}
    except ValueError:
        return {**DEFAULTS, "changed_at": ""}


def save(new: dict[str, Any], actor: str, paths: RuntimePaths | None = None, *, now: datetime | None = None) -> dict:
    clean = validate(new)
    when = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    records.put(*SETTINGS, {**clean, "changed_at": when, "changed_by": actor}, paths or RuntimePaths())
    return settings(paths)


def _local(day: date, at: time, tz: tzinfo | None) -> datetime:
    """That wall-clock time on that day here; without ``tz``, the server's own zone (DST included)."""
    naive = datetime.combine(day, at)
    return naive.replace(tzinfo=tz) if tz else naive.astimezone()


def _times_on(day: date, chosen: dict[str, Any], tz: tzinfo | None) -> list[datetime]:
    at = _parse_time(chosen["time"])
    if chosen["frequency"] == "weekly":
        return [_local(day, at, tz)] if day.weekday() == chosen["weekday"] else []
    every = _EVERY[chosen["frequency"]]
    first = at.hour % every
    return [_local(day, time(hour, at.minute), tz) for hour in range(first, 24, every)]


def latest(chosen: dict[str, Any], now: datetime, tz: tzinfo | None = None) -> datetime | None:
    """The most recent scheduled time at or before ``now``."""
    if chosen["frequency"] == "off":
        return None
    today = now.astimezone(tz).date() if tz else now.astimezone().date()
    for back in range(8):
        due = [moment for moment in _times_on(today - timedelta(days=back), chosen, tz) if moment <= now]
        if due:
            return max(due)
    return None


def upcoming(chosen: dict[str, Any], now: datetime, tz: tzinfo | None = None) -> datetime | None:
    """The next scheduled time after ``now``."""
    if chosen["frequency"] == "off":
        return None
    today = now.astimezone(tz).date() if tz else now.astimezone().date()
    for ahead in range(9):
        later = [moment for moment in _times_on(today + timedelta(days=ahead), chosen, tz) if moment > now]
        if later:
            return min(later)
    return None


def due(chosen: dict[str, Any], last_run: dict[str, Any], now: datetime, tz: tzinfo | None = None) -> datetime | None:
    """The scheduled time to run for now, or None. Only a time after the last run and the last change counts."""
    moment = latest(chosen, now, tz)
    if moment is None:
        return None
    floors = [str(last_run.get("scheduled_for") or ""), str(chosen.get("changed_at") or "")]
    for floor in filter(None, floors):
        if moment <= datetime.fromisoformat(floor):
            return None
    return moment


def _apps(root: Path, paths: RuntimePaths) -> tuple[list[Any], list[dict[str, str]]]:
    """Installed catalog apps to back up, and those skipped with why."""
    control = ControlState.runtime(paths)
    journal = OperationStore.runtime(paths)
    chosen, skipped = [], []
    for service in load().services:
        if service.stage != "optional" or not (paths.projects / service.id / "docker-compose.yml").is_file():
            continue
        if not data_directories(service, root):
            continue
        installed = control.installation(service.id) if control else None
        state = str(installed["state"]) if installed else ""
        if journal and journal.needing_attention(service.id):
            skipped.append({"id": service.id, "name": service.name, "reason": "an earlier change needs attention"})
        elif state not in _BACKUP_STATES:
            skipped.append({"id": service.id, "name": service.name, "reason": "it was being set up or changed"})
        else:
            chosen.append(service)
    return chosen, skipped


def queue(store: JobStore, root: Path, moment: datetime, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Queue one scheduled run: Mu3Lab itself first, then each app. Safe to repeat for the same ``moment``."""
    paths = paths or RuntimePaths()
    run = moment.isoformat(timespec="minutes")
    jobs: list[str] = []
    services, skipped = _apps(root, paths)
    requests = [
        (platform_backup.SERVICE_ID, "Mu3Lab itself", "backup", platform_backup.ACTION, "system.platform_backup")
    ]
    requests += [(service.id, service.name, "lifecycle", "backup", "services.actions") for service in services]
    for service_id, name, kind, action, namespace in requests:
        try:
            job = store.create(
                kind=kind,
                service_id=service_id,
                action=action,
                actor=ACTOR,
                namespace=namespace,
                detail=f"Scheduled backup ({run}).",
                idempotency_key=f"schedule:{run}:{service_id}",
                params={"reason": "scheduled"},
            )
            jobs.append(job["id"])
        except JobConflict:
            skipped.append({"id": service_id, "name": name, "reason": "another job was running on it"})
    result = {
        "scheduled_for": moment.isoformat(timespec="seconds"),
        "queued_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "jobs": jobs,
        "skipped": skipped,
    }
    records.put(*LAST_RUN, result, paths)
    return result


def tick(
    store: JobStore,
    root: Path,
    log,
    *,
    worker_started: datetime,
    now: datetime | None = None,
    paths: RuntimePaths | None = None,
    tz: tzinfo | None = None,
) -> dict[str, Any] | None:
    """Called by the worker about once a minute: queue a run when one is due."""
    paths = paths or RuntimePaths()
    now = now or datetime.now(UTC)
    chosen, last = settings(paths), records.get(*LAST_RUN, paths)
    if not last and not chosen["changed_at"]:
        # A new schedule starts from now, so updating Mu3Lab never stops sign-in and chat by surprise.
        records.put(*LAST_RUN, {"scheduled_for": now.isoformat(timespec="seconds"), "jobs": [], "skipped": []}, paths)
        return None
    moment = due(chosen, last, now, tz)
    if moment is None:
        return None
    if now - moment > timedelta(minutes=2) and now - worker_started < SETTLE:
        return None  # a missed run: let the server settle after starting first
    result = queue(store, root, moment, paths)
    names = ", ".join(item["name"] for item in result["skipped"])
    log(f"Scheduled backup queued: {len(result['jobs'])} jobs.{f' Skipped: {names}.' if names else ''}")
    return result


def status(store: JobStore | None, paths: RuntimePaths | None = None, *, now: datetime | None = None) -> dict:
    """The settings, the next run, and how the last run went, for the dashboard."""
    paths = paths or RuntimePaths()
    chosen = settings(paths)
    last = records.get(*LAST_RUN, paths)
    upcoming_at = upcoming(chosen, now or datetime.now(UTC))
    outcome: dict[str, Any] | None = None
    if last.get("jobs"):
        jobs = [job for job in (store.get(job_id) if store else None for job_id in last["jobs"]) if job]
        failed = [str(job.get("detail") or "") for job in jobs if job["state"] == "failed"]
        outcome = {
            "scheduled_for": str(last["scheduled_for"]),
            "jobs": len(last["jobs"]),
            "finished": sum(job["state"] in {"succeeded", "failed", "cancelled"} for job in jobs),
            "failed": len(failed),
            "problems": failed[:5],
            "skipped": [f"{item['name']}: {item['reason']}" for item in last.get("skipped", [])],
        }
    return {
        "frequency": chosen["frequency"],
        "weekday": chosen["weekday"],
        "time": chosen["time"],
        "next_run_at": upcoming_at.isoformat(timespec="seconds") if upcoming_at else "",
        "last_run": outcome,
    }
