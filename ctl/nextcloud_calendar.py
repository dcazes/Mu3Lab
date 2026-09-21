"""Read-only CalDAV projection for the Home upcoming-events widget."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, unquote, urlsplit

import httpx
from icalendar import Calendar
import recurring_ical_events

from ctl import calendar_secrets
from ctl.control_state import ControlState
from ctl.runtime import RuntimePaths

BASE = "http://127.0.0.1:8085"
DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
_CACHE: dict[str, tuple[datetime, dict]] = {}


class CalendarError(ValueError):
    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


def _safe_href(username: str, href: str) -> str:
    parsed = urlsplit(href)
    path = unquote(parsed.path)
    prefix = f"/remote.php/dav/calendars/{username}/"
    if parsed.scheme or parsed.netloc or not path.startswith(prefix) or ".." in path.split("/"):
        raise CalendarError("unavailable", "Nextcloud returned an unsafe calendar path.")
    return path


def discover(username: str, app_password: str) -> list[dict[str, str]]:
    principal = f"{BASE}/remote.php/dav/principals/users/{quote(username, safe='')}/"
    home_query = """<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>"""
    try:
        response = httpx.request("PROPFIND", principal, content=home_query,
                                 headers={"Depth": "0", "Content-Type": "application/xml"},
                                 auth=(username, app_password), timeout=20)
    except httpx.HTTPError as exc:
        raise CalendarError("unavailable", "Nextcloud calendar is temporarily unavailable.") from exc
    if response.status_code in {401, 403}:
        raise CalendarError("authentication_expired", "Nextcloud rejected the username or app password.")
    if response.status_code != 207:
        raise CalendarError("unavailable", f"Nextcloud principal discovery returned HTTP {response.status_code}.")
    try:
        principal_xml = ET.fromstring(response.content)
        home_node = principal_xml.find(f".//{{{CALDAV}}}calendar-home-set/{{{DAV}}}href")
        if home_node is None or not home_node.text:
            raise CalendarError("unavailable", "Nextcloud did not return a calendar home.")
        home = _safe_href(username, home_node.text)
    except ET.ParseError as exc:
        raise CalendarError("unavailable", "Nextcloud returned invalid principal discovery data.") from exc

    body = """<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:displayname/><d:resourcetype/><c:calendar-description/></d:prop></d:propfind>"""
    try:
        response = httpx.request("PROPFIND", BASE + home, content=body,
                                 headers={"Depth": "1", "Content-Type": "application/xml"},
                                 auth=(username, app_password), timeout=20)
    except httpx.HTTPError as exc:
        raise CalendarError("unavailable", "Nextcloud calendar is temporarily unavailable.") from exc
    if response.status_code in {401, 403}:
        raise CalendarError("authentication_expired", "Nextcloud rejected the username or app password.")
    if response.status_code != 207:
        raise CalendarError("unavailable", f"Nextcloud calendar discovery returned HTTP {response.status_code}.")
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise CalendarError("unavailable", "Nextcloud returned invalid calendar discovery data.") from exc
    calendars: list[dict[str, str]] = []
    for item in root.findall(f"{{{DAV}}}response"):
        resource = item.find(f".//{{{DAV}}}resourcetype")
        if resource is None or resource.find(f"{{{CALDAV}}}calendar") is None:
            continue
        href_node = item.find(f"{{{DAV}}}href")
        if href_node is None or not href_node.text:
            continue
        href = _safe_href(username, href_node.text)
        display = item.find(f".//{{{DAV}}}displayname")
        name = (display.text or "Calendar").strip() if display is not None else "Calendar"
        calendars.append({"id": hashlib.sha256(href.encode()).hexdigest()[:16],
                          "name": name[:128], "href": href})
    if not calendars:
        raise CalendarError("unavailable", "No readable Nextcloud calendars were found.")
    return calendars


def public_connection(owner_uid: str, state: ControlState) -> dict:
    metadata = state.calendar_connection(owner_uid)
    if not metadata:
        return {"ok": True, "state": "not_connected", "username_hint": "",
                "selected_calendar_id": "", "calendars": [], "last_success_at": "", "error": ""}
    calendars = [{"id": item.get("id", ""), "name": item.get("name", "")}
                 for item in metadata.get("calendars", [])]
    return {"ok": True, "state": metadata["state"], "username_hint": metadata["username_hint"],
            "selected_calendar_id": metadata["selected_calendar_id"], "calendars": calendars,
            "last_success_at": metadata["last_success_at"], "error": metadata["last_error"]}


def connect(owner_uid: str, username: str, app_password: str,
            paths: RuntimePaths = RuntimePaths()) -> dict:
    calendars = discover(username, app_password)
    selected = next((item for item in calendars if item["name"].lower() in {"personal", username.lower()}), calendars[0])
    calendar_secrets.save(owner_uid, username, app_password, paths)
    state = ControlState.runtime(paths)
    if state is None:
        calendar_secrets.delete(owner_uid, paths)
        raise CalendarError("unavailable", "Calendar state storage is unavailable.")
    hint = (username[:2] + "•••" + username[-1:]) if len(username) > 3 else "•••"
    state.set_calendar_connection(owner_uid, hint, calendars, selected["id"], success=True)
    _CACHE.pop(owner_uid, None)
    return public_connection(owner_uid, state)


def select(owner_uid: str, calendar_id: str, state: ControlState) -> dict:
    metadata = state.calendar_connection(owner_uid)
    if not metadata or calendar_id not in {item.get("id") for item in metadata.get("calendars", [])}:
        raise CalendarError("not_connected", "Select a calendar returned by Nextcloud discovery.")
    state.set_calendar_connection(owner_uid, metadata["username_hint"], metadata["calendars"], calendar_id)
    _CACHE.pop(owner_uid, None)
    return public_connection(owner_uid, state)


def disconnect(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> None:
    calendar_secrets.delete(owner_uid, paths)
    state = ControlState.runtime(paths)
    if state:
        state.delete_calendar_connection(owner_uid)
    _CACHE.pop(owner_uid, None)


def _iso(value) -> tuple[str, bool]:
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat(), False
    if isinstance(value, date):
        return value.isoformat(), True
    raise ValueError("unsupported calendar time")


def events(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> dict:
    cached = _CACHE.get(owner_uid)
    now = datetime.now(UTC)
    if cached and now - cached[0] < timedelta(minutes=5):
        return cached[1]
    state = ControlState.runtime(paths)
    metadata = state.calendar_connection(owner_uid) if state else None
    secret = calendar_secrets.get(owner_uid, paths)
    if not state or not metadata or not secret:
        raise CalendarError("not_connected", "Connect a Nextcloud calendar first.")
    calendar = next((item for item in metadata.get("calendars", [])
                     if item.get("id") == metadata.get("selected_calendar_id")), None)
    if not calendar:
        raise CalendarError("not_connected", "Select a Nextcloud calendar first.")
    href = _safe_href(secret["username"], str(calendar.get("href", "")))
    end = now + timedelta(days=30)
    body = f"""<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range start="{now.strftime('%Y%m%dT%H%M%SZ')}" end="{end.strftime('%Y%m%dT%H%M%SZ')}"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"""
    try:
        response = httpx.request("REPORT", BASE + href, content=body,
                                 headers={"Depth": "1", "Content-Type": "application/xml"},
                                 auth=(secret["username"], secret["app_password"]), timeout=25)
    except httpx.HTTPError as exc:
        raise CalendarError("unavailable", "Nextcloud calendar is temporarily unavailable.") from exc
    if response.status_code in {401, 403}:
        state.set_calendar_connection(owner_uid, metadata["username_hint"], metadata["calendars"],
                                      metadata["selected_calendar_id"], state="authentication_expired",
                                      error="Nextcloud rejected the saved app password.")
        raise CalendarError("authentication_expired", "Nextcloud rejected the saved app password.")
    if response.status_code != 207:
        raise CalendarError("unavailable", f"Nextcloud returned HTTP {response.status_code} while reading events.")
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise CalendarError("unavailable", "Nextcloud returned invalid calendar event data.") from exc
    rows: list[dict] = []
    for node in root.findall(f".//{{{CALDAV}}}calendar-data"):
        if not node.text:
            continue
        try:
            parsed = Calendar.from_ical(node.text)
            occurrences = recurring_ical_events.of(parsed).between(now, end)
        except (ValueError, TypeError) as exc:
            raise CalendarError("unavailable", "Nextcloud returned an invalid calendar event.") from exc
        for event in occurrences:
            start, all_day = _iso(event.decoded("DTSTART"))
            raw_end = event.decoded("DTEND") if event.get("DTEND") else event.decoded("DTSTART")
            finish, _ = _iso(raw_end)
            uid = str(event.get("UID", ""))
            rows.append({"id": hashlib.sha256(f"{uid}:{start}".encode()).hexdigest()[:20],
                         "title": str(event.get("SUMMARY", "Untitled event"))[:256],
                         "start": start, "end": finish, "all_day": all_day})
    rows.sort(key=lambda item: item["start"])
    result = {"ok": True, "state": "connected",
              "calendar": {"id": calendar["id"], "name": calendar["name"]},
              "fetched_at": now.isoformat(), "events": rows[:5]}
    state.set_calendar_connection(owner_uid, metadata["username_hint"], metadata["calendars"],
                                  metadata["selected_calendar_id"], success=True)
    _CACHE[owner_uid] = (now, result)
    return result
