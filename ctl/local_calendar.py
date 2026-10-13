"""Owner-scoped calendar kept by Mu3Lab itself whenever Nextcloud is not connected.

The dashboard calendar works before Nextcloud exists and after it is
disconnected or uninstalled: events are stored here, encrypted at rest, and
shown and edited the same way as Nextcloud events. Nothing syncs to phones
from this store.

Each event is kept as its full iCalendar text, so events copied out of
Nextcloud keep their notes, reminders, attendees and repeat rules, and go back
unchanged. ``import_from_nextcloud`` copies a calendar in before it is
disconnected, keeping each event's Nextcloud version (ETag);
``push_to_nextcloud`` sends everything back once it is connected again and
forgets the local copy, so the calendar always ends up in one place.

Sending back never overwrites a change made in Nextcloud meanwhile: an event
edited here goes back only if Nextcloud still has the version it was copied
from, and otherwise stays here marked as a conflict until the person chooses
which version to keep. An event deleted here is deleted there only if nobody
changed it since. Reading the calendar never sends anything; an explicit,
authenticated sync does (``api/routes/calendar.py``).

Each person's events are stored and locked separately, so one person's
unreachable Nextcloud never holds up anyone else's calendar.
"""

from __future__ import annotations

import functools
import hashlib
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import recurring_ical_events
from icalendar import Calendar

from ctl import nextcloud_calendar as nextcloud
from ctl.nextcloud_calendar import CalendarError
from ctl.runtime import RuntimePaths
from ctl.secret_file import locked
from ctl.store.secrets import SecretError, SecretStore

ID_PREFIX = "local-"
_MAX_EVENTS_PER_OWNER = 5000
# A local edit to an event Nextcloud changed since it was copied, or deleted there.
CHANGED_IN_NEXTCLOUD = "changed_in_nextcloud"
DELETED_IN_NEXTCLOUD = "deleted_in_nextcloud"
_CONFLICT_TEXT = {
    CHANGED_IN_NEXTCLOUD: "This event was also changed in Nextcloud. Choose which version to keep.",
    DELETED_IN_NEXTCLOUD: "This event was deleted in Nextcloud. Keep your version, or let it go.",
}


def _lock_path(owner_uid: str, paths: RuntimePaths):
    digest = hashlib.sha256(owner_uid.encode("utf-8")).hexdigest()[:24]
    return paths.runtime / "local-calendar" / f"{digest}.lock"


def _per_owner(function: Callable) -> Callable:
    """Run under this owner's own lock; other people's calendars are never held up."""

    @functools.wraps(function)
    def wrapper(owner_uid: str, *args, **kwargs):
        paths = kwargs.get("paths") or next((arg for arg in args if isinstance(arg, RuntimePaths)), RuntimePaths())
        with locked(_lock_path(owner_uid, paths)):
            return function(owner_uid, *args, **kwargs)

    return wrapper


def _load(owner_uid: str, paths: RuntimePaths) -> dict:
    try:
        bucket = SecretStore(paths).get("local-calendar", owner_uid) or {}
    except SecretError as exc:
        raise CalendarError("unavailable", "The Mu3Lab calendar could not be read.") from exc
    bucket.setdefault("events", {})
    # Older stores kept deletions as bare file names, with no version to check.
    bucket["deleted"] = [
        item if isinstance(item, dict) else {"href": str(item), "etag": "", "title": ""}
        for item in bucket.get("deleted", [])
    ]
    return bucket


def _save(owner_uid: str, bucket: dict, paths: RuntimePaths) -> None:
    store = SecretStore(paths)
    if bucket.get("events") or bucket.get("deleted"):
        store.put("local-calendar", owner_uid, bucket)
    else:
        store.delete("local-calendar", owner_uid)


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


def _record(
    ics: str,
    uid: str,
    *,
    href: str = "",
    from_nextcloud: bool = False,
    modified: bool = False,
    etag: str = "",
    conflict: str = "",
) -> dict:
    return {
        "uid": uid,
        "ics": ics,
        "href": href or f"{uid}.ics",
        "from_nextcloud": from_nextcloud,
        "modified": modified,
        # The Nextcloud version this copy came from; edits go back only onto that version.
        "etag": etag,
        "conflict": conflict,
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
            "conflict": _CONFLICT_TEXT.get(record.get("conflict", ""), ""),
        }
    ]


@_per_owner
def has_events(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> bool:
    """True while anything is still kept here: events not yet in Nextcloud, or deletions to send."""
    bucket = _load(owner_uid, paths)
    return bool(bucket["events"] or bucket["deleted"])


@_per_owner
def pending_upload(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> bool:
    """True when a sync has something it can send: anything kept here that is not a conflict."""
    bucket = _load(owner_uid, paths)
    return bool(bucket["deleted"]) or any(not record.get("conflict") for record in bucket["events"].values())


@_per_owner
def list_events(
    owner_uid: str, start: datetime, end: datetime, limit: int, paths: RuntimePaths = RuntimePaths()
) -> list[dict]:
    rows = []
    for event_id, record in _load(owner_uid, paths)["events"].items():
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


@_per_owner
def create_event(owner_uid: str, payload: dict, paths: RuntimePaths = RuntimePaths()) -> dict:
    uid = f"{uuid.uuid4()}@mu3lab"
    record = _record(nextcloud._write_ical(payload, uid).decode("utf-8"), uid)
    bucket = _load(owner_uid, paths)
    if len(bucket["events"]) >= _MAX_EVENTS_PER_OWNER:
        raise CalendarError("invalid_event", "The calendar is full. Connect Nextcloud to keep more events.")
    event_id = ID_PREFIX + secrets.token_hex(10)
    bucket["events"][event_id] = record
    _save(owner_uid, bucket, paths)
    return {"ok": True, "id": event_id}


def _existing(bucket: dict, event_id: str, revision: str, verb: str) -> dict:
    record = bucket["events"].get(event_id)
    if not record:
        raise CalendarError("not_found", "The calendar event is no longer available. Refresh and try again.")
    if record["recurring"]:
        raise CalendarError("recurring_event", "Repeating events can be changed once Nextcloud is connected.")
    if not revision or revision != record["revision"]:
        raise CalendarError("event_changed", f"This event changed in another window. Refresh before {verb}.")
    return record


@_per_owner
def update_event(owner_uid: str, event_id: str, payload: dict, paths: RuntimePaths = RuntimePaths()) -> dict:
    bucket = _load(owner_uid, paths)
    current = _existing(bucket, event_id, str(payload.get("revision", "")), "saving")
    # Only the edited fields change; notes, reminders and the rest stay as they were.
    ics = nextcloud._edit_ical(current["ics"].encode("utf-8"), payload, current["uid"]).decode("utf-8")
    bucket["events"][event_id] = _record(
        ics,
        current["uid"],
        href=current["href"],
        from_nextcloud=current["from_nextcloud"],
        modified=True,
        etag=current.get("etag", ""),
        conflict=current.get("conflict", ""),
    )
    _save(owner_uid, bucket, paths)
    return {"ok": True, "id": event_id}


@_per_owner
def delete_event(owner_uid: str, event_id: str, revision: str = "", paths: RuntimePaths = RuntimePaths()) -> dict:
    bucket = _load(owner_uid, paths)
    current = _existing(bucket, event_id, revision, "deleting")
    del bucket["events"][event_id]
    if current["from_nextcloud"] and current.get("conflict") != DELETED_IN_NEXTCLOUD:
        # Remove it from Nextcloud too when the calendar is connected again, if nobody changed it there.
        bucket["deleted"].append({"href": current["href"], "etag": current.get("etag", ""), "title": current["title"]})
    _save(owner_uid, bucket, paths)
    return {"ok": True}


@_per_owner
def import_from_nextcloud(owner_uid: str, resources: list[dict[str, str]], paths: RuntimePaths = RuntimePaths()) -> int:
    """Keep a copy of a Nextcloud calendar before it is disconnected.

    ``resources`` are ``{"href": file name, "ics": calendar text, "etag": version}``
    items from ``nextcloud_calendar.export_events``. Events already held locally win.
    """
    bucket = _load(owner_uid, paths)
    owned = bucket["events"]
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
            record = _record(
                resource["ics"], uid, href=resource["href"], from_nextcloud=True, etag=resource.get("etag", "")
            )
        except (CalendarError, ValueError, KeyError, TypeError):
            continue
        owned[ID_PREFIX + secrets.token_hex(10)] = record
        held.add(uid)
        copied += 1
    if copied:
        _save(owner_uid, bucket, paths)
    return copied


def _put(calendar: dict, secret: dict, record: dict, headers: dict[str, str]):
    return nextcloud._dav(
        "PUT",
        calendar["href"] + record["href"],
        secret,
        content=record["ics"].encode("utf-8"),
        headers={"Content-Type": "text/calendar", **headers},
    )


@_per_owner
def push_to_nextcloud(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> dict:
    """Send what is kept here to the connected Nextcloud calendar, then forget it.

    - New events and untouched copies are created with ``If-None-Match: *``.
      A 412 means it is already there: a copy that was never removed, or an
      event an interrupted attempt already sent (new events' file names are
      Mu3Lab's own UUIDs, so nothing else can hold them). Nothing is duplicated.
    - An edited copy goes back with ``If-Match`` on the version it was copied
      from. If Nextcloud changed or deleted it since, the edit stays here as a
      conflict for the person to settle (``resolve_conflict``).
    - A deletion is sent with ``If-Match``; if the event changed in Nextcloud
      since, it is kept there and reported.
    - Anything that fails for another reason stays here for the next sync.

    Returns ``{"moved", "conflicts", "kept"}``: the number sent, and the titles
    of new conflicts and of events kept because they changed in Nextcloud.
    """
    bucket = _load(owner_uid, paths)
    report: dict = {"moved": 0, "conflicts": [], "kept": []}
    if not (bucket["events"] or bucket["deleted"]):
        return report
    _state, _metadata, secret, calendar = nextcloud._calendar_context(owner_uid, paths)
    changed = False
    try:
        for item in list(bucket["deleted"]):
            if not item["etag"]:
                # Legacy tombstones cannot prove which remote version was deleted.
                report["kept"].append(item["title"] or item["href"])
                continue
            headers = {"If-Match": item["etag"]}
            response = nextcloud._dav("DELETE", calendar["href"] + item["href"], secret, headers=headers)
            if response.status_code == 412:
                # Changed in Nextcloud after it was copied: theirs is newer, so it stays.
                report["kept"].append(item["title"] or item["href"])
            elif response.status_code in {200, 204, 404}:
                report["moved"] += 1
            else:
                return report
            bucket["deleted"].remove(item)
            changed = True
        for event_id, record in list(bucket["events"].items()):
            if record.get("conflict"):
                continue  # waits for the person's choice
            edited_copy = record["from_nextcloud"] and record["modified"]
            if edited_copy and record.get("etag"):
                headers = {"If-Match": record["etag"]}
            elif edited_copy:
                record["conflict"] = CHANGED_IN_NEXTCLOUD
                report["conflicts"].append(record["title"])
                changed = True
                continue
            else:
                headers = {"If-None-Match": "*"}
            response = _put(calendar, secret, record, headers)
            if edited_copy and response.status_code in {404, 412}:
                record["conflict"] = DELETED_IN_NEXTCLOUD if response.status_code == 404 else CHANGED_IN_NEXTCLOUD
                report["conflicts"].append(record["title"])
                changed = True
                continue
            if response.status_code not in {200, 201, 204, 412}:
                return report
            del bucket["events"][event_id]
            changed = True
            report["moved"] += 1
    finally:
        if changed:
            _save(owner_uid, bucket, paths)
            nextcloud._clear_event_caches(owner_uid)
    return report


@_per_owner
def resolve_conflict(owner_uid: str, event_id: str, keep: str, paths: RuntimePaths = RuntimePaths()) -> dict:
    """Settle a conflict: ``mine`` replaces Nextcloud's version, ``server`` drops the local edit."""
    if keep not in {"mine", "server"}:
        raise CalendarError("invalid_event", "Choose which version to keep.")
    bucket = _load(owner_uid, paths)
    record = bucket["events"].get(event_id)
    if not record or not record.get("conflict"):
        raise CalendarError("not_found", "This event no longer needs a choice. Refresh the calendar.")
    if keep == "mine":
        _state, _metadata, secret, calendar = nextcloud._calendar_context(owner_uid, paths)
        current = nextcloud._dav("GET", calendar["href"] + record["href"], secret)
        if current.status_code == 404:
            headers = {"If-None-Match": "*"}
        elif current.status_code == 200 and current.headers.get("ETag"):
            headers = {"If-Match": current.headers["ETag"]}
        else:
            raise CalendarError("unavailable", "Nextcloud's current event version could not be checked.")
        response = _put(calendar, secret, record, headers)
        if response.status_code in {404, 412}:
            raise CalendarError("conflict", "Nextcloud changed again. Your local edit was kept; review it again.")
        if response.status_code not in {200, 201, 204}:
            raise CalendarError("unavailable", "Nextcloud did not accept the event. Try again in a moment.")
    del bucket["events"][event_id]
    _save(owner_uid, bucket, paths)
    nextcloud._clear_event_caches(owner_uid)
    return {"ok": True}
