"""Owner-scoped calendar kept by Mu3Lab itself whenever Nextcloud is not connected.

The dashboard calendar works before Nextcloud exists and after it is
disconnected or uninstalled: events are stored here, encrypted at rest, and
shown and edited the same way as Nextcloud events. Nothing syncs to phones
from this store.

Each event is kept as its full iCalendar text, so events copied out of
Nextcloud keep their notes, reminders, attendees and repeat rules, and go back
unchanged. ``import_from_nextcloud`` copies a calendar in before it is
disconnected; ``push_to_nextcloud`` sends everything back once it is connected
again and forgets the local copy, so the calendar always ends up in one place.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta

import recurring_ical_events
from icalendar import Calendar

from ctl import nextcloud_calendar as nextcloud
from ctl.nextcloud_calendar import CalendarError
from ctl.runtime import RuntimePaths
from ctl.secret_file import read_or_create_key, serialized, write_atomic

ID_PREFIX = "local-"
_MAX_EVENTS_PER_OWNER = 5000
_LOCK = "local-calendar.lock"


def _paths(paths: RuntimePaths):
    return paths.runtime / "local-calendar.key", paths.runtime / "local-calendar.enc"


def _cipher(paths: RuntimePaths):
    from cryptography.fernet import Fernet

    key_path, _ = _paths(paths)
    paths.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    return Fernet(read_or_create_key(key_path, Fernet.generate_key))


def _read(paths: RuntimePaths) -> dict[str, dict]:
    """``{owner_uid: {"events": {event_id: record}, "deleted": [href, ...]}}``."""
    _, target = _paths(paths)
    if not target.is_file():
        return {}
    try:
        value = json.loads(_cipher(paths).decrypt(target.read_bytes()).decode("utf-8"))
    except Exception as exc:
        raise CalendarError("unavailable", "The Mu3Lab calendar could not be read.") from exc
    return value if isinstance(value, dict) else {}


def _write(records: dict[str, dict], paths: RuntimePaths) -> None:
    _, target = _paths(paths)
    records = {owner: bucket for owner, bucket in records.items() if bucket.get("events") or bucket.get("deleted")}
    write_atomic(target, _cipher(paths).encrypt(json.dumps(records, sort_keys=True).encode("utf-8")))


def _bucket(records: dict, owner_uid: str) -> dict:
    bucket = records.setdefault(owner_uid, {})
    bucket.setdefault("events", {})
    bucket.setdefault("deleted", [])
    return bucket


def _summary(ics: str, uid: str) -> dict:
    """The fields the calendar view needs, read once when an event is stored."""
    try:
        calendar = Calendar.from_ical(ics)
    except ValueError as exc:
        raise CalendarError("invalid_event", "The calendar event could not be read.") from exc
    components = [event for event in calendar.walk("VEVENT") if str(event.get("UID", "")) == uid]
    if not components:
        raise CalendarError("invalid_event", "The calendar event could not be read.")
    event = components[0]
    recurring = len(components) > 1 or any(
        component.get(name) is not None for component in components for name in ("RRULE", "RDATE", "RECURRENCE-ID")
    )
    raw_start = event.decoded("DTSTART")
    if event.get("DTEND") is not None:
        raw_end = event.decoded("DTEND")
    elif event.get("DURATION") is not None:
        raw_end = raw_start + event.decoded("DURATION")
    else:
        raw_end = raw_start + timedelta(days=1) if not isinstance(raw_start, datetime) else raw_start
    start, all_day = nextcloud._iso(raw_start)
    end, _ = nextcloud._iso(raw_end)
    return {
        "title": str(event.get("SUMMARY", "Untitled event"))[:256],
        "start": start,
        "end": end,
        "all_day": all_day,
        "recurring": recurring,
    }


def _record(ics: str, uid: str, *, href: str = "", from_nextcloud: bool = False, modified: bool = False) -> dict:
    return {
        "uid": uid,
        "ics": ics,
        "href": href or f"{uid}.ics",
        "from_nextcloud": from_nextcloud,
        "modified": modified,
        "revision": secrets.token_hex(12),
        **_summary(ics, uid),
    }


def _utc(value: str, all_day: bool) -> datetime:
    if all_day:
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), UTC)
    return datetime.fromisoformat(value)


def _rows(event_id: str, record: dict, start: datetime, end: datetime) -> list[dict]:
    if record["recurring"]:
        # Repeating events are expanded for the requested range and, as in
        # Nextcloud mode, edited only in Nextcloud Calendar.
        rows = []
        for occurrence in recurring_ical_events.of(Calendar.from_ical(record["ics"])).between(start, end):
            if str(occurrence.get("UID", "")) != record["uid"]:
                continue
            occurrence_start, all_day = nextcloud._iso(occurrence.decoded("DTSTART"))
            raw_end = occurrence.decoded("DTEND") if occurrence.get("DTEND") else occurrence.decoded("DTSTART")
            occurrence_end, _ = nextcloud._iso(raw_end)
            rows.append(
                {
                    "id": f"{event_id}-{nextcloud._event_id(record['uid'], occurrence_start)}",
                    "title": str(occurrence.get("SUMMARY", record["title"]))[:256],
                    "start": occurrence_start,
                    "end": occurrence_end,
                    "all_day": all_day,
                    "editable": False,
                    "revision": "",
                    "local": True,
                }
            )
        return rows
    # All-day events are dates in the viewer's time zone, which the server does
    # not know, so they are widened by a day on each side. The browser places
    # them on the right day; a few extra rows outside a view are harmless.
    pad = timedelta(days=1) if record["all_day"] else timedelta(0)
    if _utc(record["end"], record["all_day"]) + pad <= start or _utc(record["start"], record["all_day"]) - pad >= end:
        return []
    return [
        {
            "id": event_id,
            "title": record["title"],
            "start": record["start"],
            "end": record["end"],
            "all_day": record["all_day"],
            "editable": True,
            "revision": record["revision"],
            "local": True,
        }
    ]


@serialized(_LOCK)
def has_events(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> bool:
    """True while anything still has to be sent to Nextcloud."""
    _, target = _paths(paths)
    bucket = _read(paths).get(owner_uid, {}) if target.is_file() else {}
    return bool(bucket.get("events") or bucket.get("deleted"))


@serialized(_LOCK)
def list_events(
    owner_uid: str, start: datetime, end: datetime, limit: int, paths: RuntimePaths = RuntimePaths()
) -> list[dict]:
    rows = []
    for event_id, record in _read(paths).get(owner_uid, {}).get("events", {}).items():
        rows.extend(_rows(event_id, record, start, end))
    rows.sort(key=lambda item: item["start"])
    return rows[: max(1, min(int(limit), 100))]


def events(
    owner_uid: str,
    *,
    start: datetime | None,
    end: datetime | None,
    limit: int,
    nextcloud_ready: bool,
    paths: RuntimePaths = RuntimePaths(),
) -> dict:
    now = datetime.now(UTC)
    start = (start or now.replace(second=0, microsecond=0)).astimezone(UTC)
    end = (end or start + timedelta(days=30)).astimezone(UTC)
    if end <= start or end - start > timedelta(days=93):
        raise CalendarError("invalid_range", "Calendar ranges must be between one minute and 93 days.")
    return {
        "ok": True,
        "state": "local",
        "calendar": {"id": "local", "name": "Calendar"},
        "fetched_at": now.isoformat(),
        "nextcloud_ready": nextcloud_ready,
        "events": list_events(owner_uid, start, end, limit, paths),
    }


@serialized(_LOCK)
def create_event(owner_uid: str, payload: dict, paths: RuntimePaths = RuntimePaths()) -> dict:
    uid = f"{uuid.uuid4()}@mu3lab"
    record = _record(nextcloud._write_ical(payload, uid).decode("utf-8"), uid)
    records = _read(paths)
    owned = _bucket(records, owner_uid)["events"]
    if len(owned) >= _MAX_EVENTS_PER_OWNER:
        raise CalendarError("invalid_event", "The calendar is full. Connect Nextcloud to keep more events.")
    event_id = ID_PREFIX + secrets.token_hex(10)
    owned[event_id] = record
    _write(records, paths)
    return {"ok": True, "id": event_id}


def _existing(records: dict, owner_uid: str, event_id: str, revision: str, verb: str) -> dict:
    record = records.get(owner_uid, {}).get("events", {}).get(event_id)
    if not record:
        raise CalendarError("not_found", "The calendar event is no longer available. Refresh and try again.")
    if record["recurring"]:
        raise CalendarError("recurring_event", "Repeating events can be changed once Nextcloud is connected.")
    if not revision or revision != record["revision"]:
        raise CalendarError("event_changed", f"This event changed in another window. Refresh before {verb}.")
    return record


@serialized(_LOCK)
def update_event(owner_uid: str, event_id: str, payload: dict, paths: RuntimePaths = RuntimePaths()) -> dict:
    records = _read(paths)
    current = _existing(records, owner_uid, event_id, str(payload.get("revision", "")), "saving")
    # Only the edited fields change; notes, reminders and the rest stay as they were.
    ics = nextcloud._edit_ical(current["ics"].encode("utf-8"), payload, current["uid"]).decode("utf-8")
    records[owner_uid]["events"][event_id] = _record(
        ics, current["uid"], href=current["href"], from_nextcloud=current["from_nextcloud"], modified=True
    )
    _write(records, paths)
    return {"ok": True, "id": event_id}


@serialized(_LOCK)
def delete_event(owner_uid: str, event_id: str, revision: str = "", paths: RuntimePaths = RuntimePaths()) -> dict:
    records = _read(paths)
    current = _existing(records, owner_uid, event_id, revision, "deleting")
    bucket = records[owner_uid]
    del bucket["events"][event_id]
    if current["from_nextcloud"]:
        # Remove it from Nextcloud too when the calendar is connected again.
        bucket["deleted"].append(current["href"])
    _write(records, paths)
    return {"ok": True}


@serialized(_LOCK)
def import_from_nextcloud(owner_uid: str, resources: list[dict[str, str]], paths: RuntimePaths = RuntimePaths()) -> int:
    """Keep a copy of a Nextcloud calendar before it is disconnected.

    ``resources`` are ``{"href": file name, "ics": calendar text}`` items from
    ``nextcloud_calendar.export_events``. Events already held locally win.
    """
    records = _read(paths)
    owned = _bucket(records, owner_uid)["events"]
    held = {record["uid"] for record in owned.values()}
    copied = 0
    for resource in resources:
        if len(owned) >= _MAX_EVENTS_PER_OWNER:
            break
        try:
            calendar = Calendar.from_ical(resource["ics"])
            uid = next((str(event.get("UID", "")) for event in calendar.walk("VEVENT") if event.get("UID")), "")
            if not uid or uid in held:
                continue
            record = _record(resource["ics"], uid, href=resource["href"], from_nextcloud=True)
        except (CalendarError, ValueError, KeyError, TypeError):
            continue
        owned[ID_PREFIX + secrets.token_hex(10)] = record
        held.add(uid)
        copied += 1
    if copied:
        _write(records, paths)
    return copied


@serialized(_LOCK)
def push_to_nextcloud(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> int:
    """Send local changes to the connected Nextcloud calendar, then forget them.

    New events and untouched copies are created with ``If-None-Match: *``:
    a copy that is still in Nextcloud, or an event an interrupted attempt
    already sent, is left alone, so nothing is ever duplicated. Events edited
    here replace the Nextcloud version, and events deleted here are deleted
    there. Anything that fails stays local and is tried again on the next read.
    """
    records = _read(paths)
    bucket = records.get(owner_uid)
    if not bucket or not (bucket.get("events") or bucket.get("deleted")):
        return 0
    _bucket(records, owner_uid)
    _state, _metadata, secret, calendar = nextcloud._calendar_context(owner_uid, paths)
    moved = 0
    try:
        for href in list(bucket["deleted"]):
            response = nextcloud._dav("DELETE", calendar["href"] + href, secret)
            if response.status_code not in {200, 204, 404}:
                return moved
            bucket["deleted"].remove(href)
            moved += 1
        for event_id, record in list(bucket["events"].items()):
            headers = {"Content-Type": "text/calendar"}
            if not record["modified"]:
                headers["If-None-Match"] = "*"
            response = nextcloud._dav(
                "PUT", calendar["href"] + record["href"], secret, content=record["ics"].encode("utf-8"), headers=headers
            )
            # 412: the event is already in Nextcloud.
            if response.status_code not in {200, 201, 204, 412}:
                return moved
            del bucket["events"][event_id]
            moved += 1
    finally:
        if moved:
            _write(records, paths)
            nextcloud._clear_event_caches(owner_uid)
    return moved
