"""Read-only CalDAV projection for the Home upcoming-events widget."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
from icalendar import Calendar, Event
import recurring_ical_events

from ctl import calendar_secrets
from ctl.control_state import ControlState
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

BASE = "http://127.0.0.1:8085"
DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
_CACHE: dict[str, tuple[datetime, dict]] = {}
_EVENT_CACHE: dict[str, tuple[datetime, dict[str, dict[str, str]]]] = {}
_AUTHORIZATIONS: dict[str, dict[str, str | float]] = {}
_AUTH_LOCK = threading.RLock()
_VERIFIED_GROUPS: dict[str, list[str]] = {}
_AUTH_TTL_SECONDS = 20 * 60


class CalendarError(ValueError):
    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


def _safe_href(username: str, href: str) -> str:
    parsed = urlsplit(href)
    path = unquote(parsed.path)
    prefix = f"/remote.php/dav/calendars/{username}/"
    # A DAV server may send a relative URL, but it may never cause Mu3Lab to
    # contact another host or escape the selected user's calendar collection.
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or
            not path.startswith(prefix) or ".." in path.split("/") or "//" in path):
        raise CalendarError("unavailable", "Nextcloud returned an unsafe calendar path.")
    return path


def _expected_origin(paths: RuntimePaths = RuntimePaths()) -> str:
    values = read_runtime_env(paths.projects / "nextcloud" / ".env")
    origin = str(values.get("NEXTCLOUD_OVERWRITECLIURL", "")).rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise CalendarError("not_installed", "Nextcloud's private HTTPS route is not configured yet.")
    return f"https://{parsed.netloc}"


def _local_login_path(value: str, origin: str, *, allow_poll: bool = False) -> str:
    """Validate a Login Flow URL and convert it to a safe localhost path."""
    parsed = urlsplit(value)
    expected = urlsplit(origin)
    if (parsed.scheme != "https" or parsed.netloc != expected.netloc or
            parsed.username or parsed.password or not parsed.path.startswith("/")):
        raise CalendarError("unavailable", "Nextcloud returned an unexpected authorization URL.")
    if not allow_poll and not parsed.path.startswith("/index.php/login/v2/"):
        raise CalendarError("unavailable", "Nextcloud returned an invalid login-flow URL.")
    if allow_poll and not parsed.path.startswith("/index.php/login/v2/"):
        raise CalendarError("unavailable", "Nextcloud returned an invalid login-flow poll endpoint.")
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def _canonical_username(username: str, app_password: str) -> str:
    try:
        response = httpx.get(f"{BASE}/ocs/v2.php/cloud/user", params={"format": "json"},
                             headers={"OCS-APIRequest": "true"}, auth=(username, app_password), timeout=20)
    except httpx.HTTPError as exc:
        raise CalendarError("unavailable", "Nextcloud calendar is temporarily unavailable.") from exc
    if response.status_code in {401, 403}:
        raise CalendarError("authentication_expired", "Nextcloud rejected the generated app credential.")
    try:
        value = response.json()
        profile = value["ocs"]["data"]
        canonical = str(profile["id"])
        groups = [str(item) for item in profile.get("groups", [])]
    except (TypeError, ValueError, KeyError) as exc:
        raise CalendarError("unavailable", "Nextcloud did not identify the signed-in calendar user.") from exc
    if not canonical or len(canonical) > 256:
        raise CalendarError("unavailable", "Nextcloud returned an invalid calendar user identifier.")
    with _AUTH_LOCK:
        _VERIFIED_GROUPS[canonical] = groups
    return canonical


def _enforce_sso_only(paths: RuntimePaths) -> bool:
    """Disable browser password login only after owner/admin evidence exists."""
    from ctl import actions

    project = paths.projects / "nextcloud"
    if not (project / "docker-compose.yml").is_file():
        return False
    command = ["runuser", "-u", "www-data", "--", "php", "occ", "config:app:set",
               "--type=string", "--value=0", "user_oidc", "allow_multiple_user_backends"]
    rc, _output = actions.compose_exec(project, "app", command, lambda _line: None, timeout=120)
    return rc == 0


def start_authorization(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> dict:
    """Start official Login Flow v2 without exposing an app password to the UI."""
    if not owner_uid or len(owner_uid) > 256:
        raise CalendarError("not_connected", "An authenticated subject is required.")
    origin = _expected_origin(paths)
    try:
        response = httpx.post(f"{BASE}/index.php/login/v2", timeout=20)
    except httpx.HTTPError as exc:
        raise CalendarError("unavailable", "Nextcloud calendar is temporarily unavailable.") from exc
    if response.status_code not in {200, 201}:
        raise CalendarError("unavailable", f"Nextcloud authorization returned HTTP {response.status_code}.")
    try:
        payload = response.json()
        login = str(payload["login"])
        poll = payload["poll"]
        endpoint = str(poll["endpoint"])
        token = str(poll["token"])
    except (TypeError, ValueError, KeyError) as exc:
        raise CalendarError("unavailable", "Nextcloud returned an invalid authorization response.") from exc
    _local_login_path(login, origin)
    local_endpoint = _local_login_path(endpoint, origin, allow_poll=True)
    if not token or len(token) > 4096:
        raise CalendarError("unavailable", "Nextcloud returned an invalid authorization token.")
    authorization_id = uuid.uuid4().hex
    expires_at = time.time() + _AUTH_TTL_SECONDS
    with _AUTH_LOCK:
        _AUTHORIZATIONS[authorization_id] = {
            "owner_uid": owner_uid, "poll_path": local_endpoint, "token": token,
            "origin": origin, "expires_at": expires_at,
        }
    # Return the verified public URL, never the server-side poll token.
    return {"ok": True, "state": "awaiting_user", "authorization_id": authorization_id,
            "login_url": login, "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
            "poll_after_ms": 2000}


def cancel_authorization(owner_uid: str, authorization_id: str) -> None:
    with _AUTH_LOCK:
        pending = _AUTHORIZATIONS.get(authorization_id)
        if not pending or pending.get("owner_uid") != owner_uid:
            raise CalendarError("not_found", "Calendar authorization was not found.")
        _AUTHORIZATIONS.pop(authorization_id, None)


def poll_authorization(owner_uid: str, authorization_id: str,
                       paths: RuntimePaths = RuntimePaths()) -> dict:
    with _AUTH_LOCK:
        pending = dict(_AUTHORIZATIONS.get(authorization_id) or {})
    if not pending or pending.get("owner_uid") != owner_uid:
        raise CalendarError("not_found", "Calendar authorization was not found.")
    if float(pending.get("expires_at", 0)) <= time.time():
        with _AUTH_LOCK:
            _AUTHORIZATIONS.pop(authorization_id, None)
        return {"ok": True, "state": "expired"}
    try:
        response = httpx.post(f"{BASE}{pending['poll_path']}", data={"token": str(pending["token"])}, timeout=20)
    except httpx.HTTPError as exc:
        raise CalendarError("unavailable", "Nextcloud calendar is temporarily unavailable.") from exc
    if response.status_code == 404:
        # Preserve the opaque authorization id while the browser waits.  The
        # dashboard must keep polling the same server-side record, but it
        # never receives the Login Flow token itself.
        return {
            "ok": True,
            "state": "pending",
            "authorization_id": authorization_id,
            "expires_at": datetime.fromtimestamp(float(pending["expires_at"]), UTC).isoformat(),
            "poll_after_ms": 2000,
        }
    if response.status_code != 200:
        with _AUTH_LOCK:
            _AUTHORIZATIONS.pop(authorization_id, None)
        return {"ok": True, "state": "failed", "error": "Nextcloud authorization was not approved."}
    try:
        payload = response.json()
        server = str(payload["server"])
        login_name = str(payload["loginName"])
        app_password = str(payload["appPassword"])
    except (TypeError, ValueError, KeyError) as exc:
        raise CalendarError("unavailable", "Nextcloud returned an invalid completed authorization.") from exc
    if urlsplit(server).scheme != "https" or urlsplit(server).netloc != urlsplit(str(pending["origin"])).netloc:
        raise CalendarError("unavailable", "Nextcloud returned an unexpected authorization server.")
    canonical = _canonical_username(login_name, app_password)
    try:
        result = connect(owner_uid, canonical, app_password, paths)
        state = ControlState.runtime(paths)
        initialization = state.initialization("nextcloud") if state else None
        with _AUTH_LOCK:
            groups = _VERIFIED_GROUPS.pop(canonical, [])
        if (state and initialization and initialization.get("owner_uid") == owner_uid
                and "admin" in groups and _enforce_sso_only(paths)):
            state.set_service_identity(
                "nextcloud", "native_oidc", "ready", owner_uid=owner_uid,
                detail="Authentik owner, Nextcloud administrator role, and CalDAV access verified.",
                verified=True)
    finally:
        # The password must never remain in the temporary approval record.
        with _AUTH_LOCK:
            _VERIFIED_GROUPS.pop(canonical, None)
            _AUTHORIZATIONS.pop(authorization_id, None)
    return {"ok": True, "state": "connected", "connection": result}


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
    _EVENT_CACHE.pop(owner_uid, None)
    return public_connection(owner_uid, state)


def disconnect(owner_uid: str, paths: RuntimePaths = RuntimePaths()) -> dict:
    """Revoke the generated credential where possible, then forget it locally."""
    warning = ""
    secret = calendar_secrets.get(owner_uid, paths)
    if secret:
        try:
            response = httpx.delete(f"{BASE}/ocs/v2.php/core/apppassword",
                                    params={"format": "json"},
                                    headers={"OCS-APIRequest": "true"},
                                    auth=(secret["username"], secret["app_password"]), timeout=20)
            if response.status_code not in {200, 204}:
                warning = "Nextcloud could not confirm remote app-password revocation; the local connection was removed."
        except httpx.HTTPError:
            warning = "Nextcloud was unavailable, so remote app-password revocation could not be confirmed; the local connection was removed."
    calendar_secrets.delete(owner_uid, paths)
    state = ControlState.runtime(paths)
    if state:
        state.delete_calendar_connection(owner_uid)
    _CACHE.pop(owner_uid, None)
    _EVENT_CACHE.pop(owner_uid, None)
    return {"ok": True, "warning": warning}


def _iso(value) -> tuple[str, bool]:
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat(), False
    if isinstance(value, date):
        return value.isoformat(), True
    raise ValueError("unsupported calendar time")


def _event_id(uid: str, occurrence: str = "") -> str:
    """Opaque per-occurrence ID; recurring instances must not collide."""
    return hashlib.sha256(f"{uid}|{occurrence}".encode()).hexdigest()[:20]


def _revision(etag: str) -> str:
    """Do not expose a server ETag while retaining optimistic concurrency."""
    return hashlib.sha256(etag.encode()).hexdigest()[:24] if etag else ""


def _calendar_context(owner_uid: str, paths: RuntimePaths) -> tuple[ControlState, dict, dict, dict]:
    state = ControlState.runtime(paths)
    metadata = state.calendar_connection(owner_uid) if state else None
    secret = calendar_secrets.get(owner_uid, paths)
    if not state or not metadata or not secret:
        raise CalendarError("not_connected", "Connect a Nextcloud calendar first.")
    calendar = next((item for item in metadata.get("calendars", []) if item.get("id") == metadata.get("selected_calendar_id")), None)
    if not calendar:
        raise CalendarError("not_connected", "Select a Nextcloud calendar first.")
    calendar = dict(calendar)
    calendar["href"] = _safe_href(secret["username"], str(calendar.get("href", "")))
    return state, metadata, secret, calendar


def _write_ical(payload: dict, uid: str) -> bytes:
    title = str(payload.get("title", "")).strip()
    if not title:
        raise CalendarError("invalid_event", "An event title is required.")
    all_day = bool(payload.get("all_day", False))
    try:
        start = date.fromisoformat(str(payload.get("start", ""))[:10]) if all_day else datetime.fromisoformat(str(payload.get("start", "")).replace("Z", "+00:00"))
        finish = date.fromisoformat(str(payload.get("end", ""))[:10]) if all_day else datetime.fromisoformat(str(payload.get("end", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarError("invalid_event", "Event times must use ISO 8601 format.") from exc
    if isinstance(start, datetime) and start.tzinfo is None: start = start.replace(tzinfo=UTC)
    if isinstance(finish, datetime) and finish.tzinfo is None: finish = finish.replace(tzinfo=UTC)
    if finish <= start:
        raise CalendarError("invalid_event", "The event end must be after its start.")
    calendar = Calendar(); calendar.add("prodid", "-//Mu3Lab//Calendar//EN"); calendar.add("version", "2.0")
    event = Event(); event.add("uid", uid); event.add("summary", title[:256]); event.add("dtstart", start); event.add("dtend", finish)
    if str(payload.get("location", "")).strip(): event.add("location", str(payload["location"]).strip()[:512])
    if str(payload.get("notes", "")).strip(): event.add("description", str(payload["notes"]).strip()[:4000])
    calendar.add_component(event)
    return calendar.to_ical()


def _dav(method: str, href: str, secret: dict[str, str], *, content: bytes | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
    try:
        return httpx.request(method, BASE + href, content=content, headers=headers or {}, auth=(secret["username"], secret["app_password"]), timeout=25)
    except httpx.HTTPError as exc:
        raise CalendarError("unavailable", "Nextcloud calendar is temporarily unavailable.") from exc


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
    body = f"""<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range start="{now.strftime('%Y%m%dT%H%M%SZ')}" end="{end.strftime('%Y%m%dT%H%M%SZ')}"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"""
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
    resources: dict[str, dict[str, str]] = {}
    for response_node in root.findall(f"{{{DAV}}}response"):
        node = response_node.find(f".//{{{CALDAV}}}calendar-data")
        href_node = response_node.find(f"{{{DAV}}}href")
        if node is None or not node.text or href_node is None or not href_node.text:
            continue
        try:
            parsed = Calendar.from_ical(node.text)
            occurrences = recurring_ical_events.of(parsed).between(now, end)
            series_recurring = any(component.get("RRULE") is not None for component in parsed.walk("VEVENT"))
        except (ValueError, TypeError) as exc:
            raise CalendarError("unavailable", "Nextcloud returned an invalid calendar event.") from exc
        for event in occurrences:
            start, all_day = _iso(event.decoded("DTSTART"))
            raw_end = event.decoded("DTEND") if event.get("DTEND") else event.decoded("DTSTART")
            finish, _ = _iso(raw_end)
            uid = str(event.get("UID", ""))
            if not uid:
                continue
            # recurring_ical_events may add a recurrence-id to its projected
            # copy of an otherwise ordinary VEVENT.  The source calendar's
            # RRULE is the reliable signal for Home's simple-edit boundary.
            recurring = series_recurring
            event_id = _event_id(uid, start)
            etag_node = response_node.find(f".//{{{DAV}}}getetag")
            resources[event_id] = {"href": _safe_href(secret["username"], href_node.text), "uid": uid,
                                   "etag": str(etag_node.text or "") if etag_node is not None else "",
                                   "editable": "false" if recurring else "true"}
            rows.append({"id": event_id,
                         "title": str(event.get("SUMMARY", "Untitled event"))[:256],
                         "start": start, "end": finish, "all_day": all_day,
                         "editable": not recurring,
                         "revision": _revision(resources[event_id]["etag"]) if not recurring else ""})
    rows.sort(key=lambda item: item["start"])
    result = {"ok": True, "state": "connected",
              "calendar": {"id": calendar["id"], "name": calendar["name"]},
              "fetched_at": now.isoformat(), "events": rows[:5]}
    state.set_calendar_connection(owner_uid, metadata["username_hint"], metadata["calendars"],
                                  metadata["selected_calendar_id"], success=True)
    _CACHE[owner_uid] = (now, result)
    _EVENT_CACHE[owner_uid] = (now, resources)
    return result


def _resource(owner_uid: str, event_id: str, paths: RuntimePaths) -> tuple[dict[str, str], dict[str, str]]:
    events(owner_uid, paths)
    cached = _EVENT_CACHE.get(owner_uid)
    resource = cached[1].get(event_id) if cached else None
    secret = calendar_secrets.get(owner_uid, paths)
    if not resource or not secret:
        raise CalendarError("not_found", "The calendar event is no longer available. Refresh and try again.")
    return secret, resource


def create_event(owner_uid: str, payload: dict, paths: RuntimePaths = RuntimePaths()) -> dict:
    _state, _metadata, secret, calendar = _calendar_context(owner_uid, paths)
    uid = f"{uuid.uuid4()}@mu3lab"
    response = _dav("PUT", calendar["href"] + f"{uid}.ics", secret, content=_write_ical(payload, uid),
                    headers={"Content-Type": "text/calendar", "If-None-Match": "*"})
    if response.status_code not in {200, 201, 204}:
        raise CalendarError("unavailable", f"Nextcloud could not create the event (HTTP {response.status_code}).")
    _CACHE.pop(owner_uid, None); _EVENT_CACHE.pop(owner_uid, None)
    return {"ok": True, "id": _event_id(uid, str(payload.get("start", "")))}


def update_event(owner_uid: str, event_id: str, payload: dict, paths: RuntimePaths = RuntimePaths()) -> dict:
    secret, resource = _resource(owner_uid, event_id, paths)
    if resource.get("editable") != "true":
        raise CalendarError("recurring_event", "Recurring events must be edited in Nextcloud Calendar.")
    revision = str(payload.get("revision", ""))
    if not revision or revision != _revision(resource["etag"]):
        raise CalendarError("event_changed", "This event changed in another calendar client. Refresh before saving.")
    response = _dav("PUT", resource["href"], secret, content=_write_ical(payload, resource["uid"]),
                    headers={"Content-Type": "text/calendar", "If-Match": resource["etag"] or "*"})
    if response.status_code == 412:
        raise CalendarError("event_changed", "This event changed in another calendar client. Refresh before saving.")
    if response.status_code not in {200, 201, 204}:
        raise CalendarError("unavailable", f"Nextcloud could not update the event (HTTP {response.status_code}).")
    _CACHE.pop(owner_uid, None); _EVENT_CACHE.pop(owner_uid, None)
    return {"ok": True, "id": event_id}


def delete_event(owner_uid: str, event_id: str, revision: str = "", paths: RuntimePaths = RuntimePaths()) -> dict:
    secret, resource = _resource(owner_uid, event_id, paths)
    if resource.get("editable") != "true":
        raise CalendarError("recurring_event", "Recurring events must be deleted in Nextcloud Calendar.")
    if not revision or revision != _revision(resource["etag"]):
        raise CalendarError("event_changed", "This event changed in another calendar client. Refresh before deleting.")
    response = _dav("DELETE", resource["href"], secret, headers={"If-Match": resource["etag"] or "*"})
    if response.status_code == 412:
        raise CalendarError("event_changed", "This event changed in another calendar client. Refresh before deleting.")
    if response.status_code not in {200, 204}:
        raise CalendarError("unavailable", f"Nextcloud could not delete the event (HTTP {response.status_code}).")
    _CACHE.pop(owner_uid, None); _EVENT_CACHE.pop(owner_uid, None)
    return {"ok": True}
