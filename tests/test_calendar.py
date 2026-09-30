from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

from ctl import calendar_secrets
from ctl.control_state import ControlState
from ctl.nextcloud_calendar import (
    CalendarError,
    auto_connect,
    cancel_authorization,
    create_event,
    delete_event,
    discover,
    events,
    poll_authorization,
    start_authorization,
    update_event,
)
from ctl.runtime import RuntimePaths


class CalendarSecretTests(unittest.TestCase):
    def test_credentials_are_encrypted_and_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            calendar_secrets.save("owner-a", "alice", "private-password", paths)
            self.assertEqual(calendar_secrets.get("owner-a", paths)["username"], "alice")
            self.assertIsNone(calendar_secrets.get("owner-b", paths))
            ciphertext = (paths.runtime / "calendar-connections.enc").read_bytes()
            self.assertNotIn(b"private-password", ciphertext)
            calendar_secrets.delete("owner-a", paths)
            self.assertIsNone(calendar_secrets.get("owner-a", paths))


class CalDavTests(unittest.TestCase):
    @staticmethod
    def principal_response(username: str = "alice") -> httpx.Response:
        xml = f"""<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:propstat><d:prop><c:calendar-home-set><d:href>/remote.php/dav/calendars/{username}/</d:href></c:calendar-home-set></d:prop></d:propstat></d:response></d:multistatus>""".encode()
        return httpx.Response(207, content=xml)

    def test_discovery_rejects_href_outside_users_calendar_home(self):
        xml = b"""<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>/remote.php/dav/calendars/bob/private/</d:href><d:propstat><d:prop><d:displayname>Private</d:displayname><d:resourcetype><d:collection/><c:calendar/></d:resourcetype></d:prop></d:propstat></d:response></d:multistatus>"""
        response = httpx.Response(207, content=xml)
        with (
            patch("ctl.nextcloud_calendar.httpx.request", side_effect=[self.principal_response(), response]),
            self.assertRaises(CalendarError),
        ):
            discover("alice", "secret")

    def test_event_projection_omits_sensitive_fields_and_limits_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            href = "/remote.php/dav/calendars/alice/personal/"
            calendars = [{"id": "calendar-a", "name": "Personal", "href": href}]
            state.set_calendar_connection("owner", "al•••e", calendars, "calendar-a")
            calendar_secrets.save("owner", "alice", "secret", paths)
            start = datetime.now(UTC) + timedelta(days=1)
            ics = "\r\n".join(
                [
                    "BEGIN:VCALENDAR",
                    "VERSION:2.0",
                    "BEGIN:VEVENT",
                    "UID:private-id",
                    f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
                    f"DTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}",
                    "SUMMARY:Planning",
                    "DESCRIPTION:must not leave backend",
                    "ATTENDEE:mailto:a@example.com",
                    "END:VEVENT",
                    "END:VCALENDAR",
                    "",
                ]
            )
            xml = f"""<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>{href}private-id.ics</d:href><d:propstat><d:prop><d:getetag>"event-one"</d:getetag><c:calendar-data><![CDATA[{ics}]]></c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>""".encode()
            response = httpx.Response(207, content=xml)
            with patch("ctl.nextcloud_calendar.httpx.request", return_value=response):
                result = events("owner", paths)
            self.assertEqual(result["events"][0]["title"], "Planning")
            self.assertTrue(result["events"][0]["revision"])
            self.assertNotEqual(result["events"][0]["revision"], '"event-one"')
            self.assertNotIn("description", result["events"][0])
            self.assertNotIn("attendees", result["events"][0])

    def test_event_projection_uses_the_requested_calendar_view_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            href = "/remote.php/dav/calendars/alice/personal/"
            state.set_calendar_connection(
                "range-owner", "al•••e", [{"id": "calendar-range", "name": "Personal", "href": href}], "calendar-range"
            )
            calendar_secrets.save("range-owner", "alice", "secret", paths)
            start = datetime(2026, 11, 1, tzinfo=UTC)
            end = start + timedelta(days=31)
            ics = "\r\n".join(
                [
                    "BEGIN:VCALENDAR",
                    "VERSION:2.0",
                    "BEGIN:VEVENT",
                    "UID:range-event",
                    "DTSTART:20261102T100000Z",
                    "DTEND:20261102T110000Z",
                    "SUMMARY:Visible",
                    "END:VEVENT",
                    "END:VCALENDAR",
                    "",
                ]
            )
            xml = f"""<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>{href}range-event.ics</d:href><d:propstat><d:prop><d:getetag>"range-one"</d:getetag><c:calendar-data><![CDATA[{ics}]]></c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>""".encode()
            with patch(
                "ctl.nextcloud_calendar.httpx.request", return_value=httpx.Response(207, content=xml)
            ) as request:
                result = events("range-owner", paths, start=start, end=end, limit=100)
            self.assertEqual([item["title"] for item in result["events"]], ["Visible"])
            query = request.call_args.kwargs["content"]
            self.assertIn("20261101T000000Z", query)
            self.assertIn("20261202T000000Z", query)

    def test_every_event_resource_is_expanded_with_the_requested_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            href = "/remote.php/dav/calendars/alice/personal/"
            state.set_calendar_connection(
                "multi-owner", "al•••e", [{"id": "calendar-multi", "name": "Personal", "href": href}], "calendar-multi"
            )
            calendar_secrets.save("multi-owner", "alice", "secret", paths)
            start = datetime(2026, 11, 1, tzinfo=UTC)

            def resource(uid: str, day: int) -> str:
                ics = "\r\n".join(
                    [
                        "BEGIN:VCALENDAR",
                        "VERSION:2.0",
                        "BEGIN:VEVENT",
                        f"UID:{uid}",
                        f"DTSTART:202611{day:02d}T100000Z",
                        f"DTEND:202611{day:02d}T110000Z",
                        f"SUMMARY:{uid}",
                        "END:VEVENT",
                        "END:VCALENDAR",
                        "",
                    ]
                )
                return f"""<d:response><d:href>{href}{uid}.ics</d:href><d:propstat><d:prop><d:getetag>"{uid}"</d:getetag><c:calendar-data><![CDATA[{ics}]]></c:calendar-data></d:prop></d:propstat></d:response>"""

            xml = (
                '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                + resource("second-event", 20)
                + resource("first-event", 2)
                + "</d:multistatus>"
            ).encode()
            with patch("ctl.nextcloud_calendar.httpx.request", return_value=httpx.Response(207, content=xml)):
                result = events("multi-owner", paths, start=start, end=start + timedelta(days=30), limit=100)
            self.assertEqual([item["title"] for item in result["events"]], ["first-event", "second-event"])

    def test_event_mutations_use_the_selected_owner_calendar(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            href = "/remote.php/dav/calendars/alice/personal/"
            state.set_calendar_connection(
                "owner-write", "al•••e", [{"id": "calendar-write", "name": "Personal", "href": href}], "calendar-write"
            )
            calendar_secrets.save("owner-write", "alice", "secret", paths)
            payload = {
                "title": "Private meeting",
                "start": "2026-10-01T10:00:00+00:00",
                "end": "2026-10-01T11:00:00+00:00",
            }
            with patch("ctl.nextcloud_calendar.httpx.request", return_value=httpx.Response(201)) as request:
                created = create_event("owner-write", payload, paths)
            self.assertTrue(created["ok"])
            self.assertEqual(request.call_args.args[0], "PUT")
            self.assertIn("/remote.php/dav/calendars/alice/personal/", request.call_args.args[1])

    def test_event_update_and_delete_reject_stale_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            href = "/remote.php/dav/calendars/alice/personal/"
            state.set_calendar_connection(
                "owner-revision",
                "al•••e",
                [{"id": "calendar-revision", "name": "Personal", "href": href}],
                "calendar-revision",
            )
            calendar_secrets.save("owner-revision", "alice", "secret", paths)
            start = datetime.now(UTC) + timedelta(days=1)
            ics = "\r\n".join(
                [
                    "BEGIN:VCALENDAR",
                    "VERSION:2.0",
                    "BEGIN:VEVENT",
                    "UID:revision-id",
                    f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
                    f"DTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}",
                    "SUMMARY:Original",
                    "END:VEVENT",
                    "END:VCALENDAR",
                    "",
                ]
            )
            xml = f"""<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>{href}revision-id.ics</d:href><d:propstat><d:prop><d:getetag>"revision-one"</d:getetag><c:calendar-data><![CDATA[{ics}]]></c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>""".encode()
            with patch("ctl.nextcloud_calendar.httpx.request", return_value=httpx.Response(207, content=xml)):
                event_id = events("owner-revision", paths)["events"][0]["id"]
            stale = {
                "title": "Changed",
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "revision": "wrong",
            }
            with patch("ctl.nextcloud_calendar.httpx.request", return_value=httpx.Response(207, content=xml)):
                with self.assertRaisesRegex(CalendarError, "changed in another"):
                    update_event("owner-revision", event_id, stale, paths)
                with self.assertRaisesRegex(CalendarError, "changed in another"):
                    delete_event("owner-revision", event_id, "wrong", paths)

    def test_all_day_recurrence_preserves_dates_and_applies_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            href = "/remote.php/dav/calendars/alice/personal/"
            calendars = [{"id": "calendar-b", "name": "Personal", "href": href}]
            state.set_calendar_connection("recurring-owner", "al•••e", calendars, "calendar-b")
            calendar_secrets.save("recurring-owner", "alice", "secret", paths)
            start = (datetime.now(UTC) + timedelta(days=2)).date()
            excluded = start + timedelta(days=1)
            ics = "\r\n".join(
                [
                    "BEGIN:VCALENDAR",
                    "VERSION:2.0",
                    "BEGIN:VEVENT",
                    "UID:all-day-series",
                    f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                    f"DTEND;VALUE=DATE:{(start + timedelta(days=1)).strftime('%Y%m%d')}",
                    "RRULE:FREQ=DAILY;COUNT=3",
                    f"EXDATE;VALUE=DATE:{excluded.strftime('%Y%m%d')}",
                    "SUMMARY:Private day",
                    "DESCRIPTION:hidden",
                    "END:VEVENT",
                    "END:VCALENDAR",
                    "",
                ]
            )
            xml = f"""<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>{href}all-day-series.ics</d:href><d:propstat><d:prop><d:getetag>"event-two"</d:getetag><c:calendar-data><![CDATA[{ics}]]></c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>""".encode()
            with patch("ctl.nextcloud_calendar.httpx.request", return_value=httpx.Response(207, content=xml)):
                result = events("recurring-owner", paths)
            self.assertEqual(len(result["events"]), 2)
            self.assertTrue(all(item["all_day"] for item in result["events"]))
            self.assertTrue(all("T" not in item["start"] for item in result["events"]))
            self.assertNotIn(excluded.isoformat(), {item["start"] for item in result["events"]})

    def test_login_flow_connects_without_returning_or_logging_app_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            project = paths.projects / "nextcloud"
            project.mkdir(parents=True)
            (project / ".env").write_text(
                "NEXTCLOUD_OVERWRITECLIURL=https://mu3lab.example.ts.net:8453\n", encoding="utf-8"
            )
            start_response = httpx.Response(
                200,
                json={
                    "login": "https://mu3lab.example.ts.net:8453/index.php/login/v2/flow",
                    "poll": {
                        "endpoint": "https://mu3lab.example.ts.net:8453/index.php/login/v2/poll",
                        "token": "private-token",
                    },
                },
            )
            with patch("ctl.nextcloud_calendar.httpx.post", return_value=start_response):
                started = start_authorization("owner", paths)
            self.assertEqual(started["state"], "awaiting_user")
            self.assertNotIn("private-token", str(started))
            completed = httpx.Response(
                200,
                json={
                    "server": "https://mu3lab.example.ts.net:8453",
                    "loginName": "display-name",
                    "appPassword": "private-password",
                },
            )
            calendars = [{"id": "calendar", "name": "Personal", "href": "/remote.php/dav/calendars/alice/personal/"}]
            with (
                patch("ctl.nextcloud_calendar.httpx.post", return_value=completed),
                patch("ctl.nextcloud_calendar._canonical_username", return_value="alice"),
                patch("ctl.nextcloud_calendar.discover", return_value=calendars),
            ):
                result = poll_authorization("owner", str(started["authorization_id"]), paths)
            self.assertEqual(result["state"], "connected")
            self.assertNotIn("private-password", str(result))
            self.assertEqual(calendar_secrets.get("owner", paths)["username"], "alice")
            with self.assertRaises(CalendarError):
                cancel_authorization("owner", str(started["authorization_id"]))

    def test_login_flow_pending_response_keeps_opaque_authorization_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            project = paths.projects / "nextcloud"
            project.mkdir(parents=True)
            (project / ".env").write_text(
                "NEXTCLOUD_OVERWRITECLIURL=https://mu3lab.example.ts.net:8453\n", encoding="utf-8"
            )
            start_response = httpx.Response(
                200,
                json={
                    "login": "https://mu3lab.example.ts.net:8453/index.php/login/v2/flow",
                    "poll": {
                        "endpoint": "https://mu3lab.example.ts.net:8453/index.php/login/v2/poll",
                        "token": "private-token",
                    },
                },
            )
            with patch("ctl.nextcloud_calendar.httpx.post", return_value=start_response):
                started = start_authorization("owner", paths)
            with patch("ctl.nextcloud_calendar.httpx.post", return_value=httpx.Response(404)):
                pending = poll_authorization("owner", str(started["authorization_id"]), paths)
            self.assertEqual(pending["state"], "pending")
            self.assertEqual(pending["authorization_id"], started["authorization_id"])
            self.assertNotIn("private-token", str(pending))

    def test_auto_connect_stores_cli_token_without_returning_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            project = paths.projects / "nextcloud"
            project.mkdir(parents=True)
            (project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            calendars = [{"id": "calendar", "name": "Personal", "href": "/remote.php/dav/calendars/akadmin/personal/"}]
            with (
                patch(
                    "ctl.actions.compose_exec",
                    return_value=(0, "No password provided.\napp password:\ncli-generated-secret-1234567890"),
                ),
                patch("ctl.nextcloud_calendar.discover", return_value=calendars),
            ):
                result = auto_connect("owner", "akadmin", paths)
            self.assertEqual(result["state"], "connected")
            self.assertNotIn("cli-generated-secret", str(result))
            self.assertEqual(calendar_secrets.get("owner", paths)["username"], "akadmin")


def _connected(tmp: str, owner: str) -> tuple[RuntimePaths, str]:
    paths = RuntimePaths(Path(tmp))
    state = ControlState(paths.runtime / "control-plane.sqlite3")
    href = "/remote.php/dav/calendars/alice/personal/"
    state.set_calendar_connection(owner, "al•••e", [{"id": "cal", "name": "Personal", "href": href}], "cal")
    calendar_secrets.save(owner, "alice", "secret", paths)
    return paths, href


def _multistatus(href: str, uid: str, ics: str, etag: str = '"one"') -> bytes:
    return (
        f'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response>'
        f"<d:href>{href}{uid}.ics</d:href><d:propstat><d:prop><d:getetag>{etag}</d:getetag>"
        f"<c:calendar-data><![CDATA[{ics}]]></c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>"
    ).encode()


def _event_ics(uid: str, start: datetime, extra: list[str] | None = None) -> str:
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "BEGIN:VTIMEZONE",
            "TZID:Europe/Paris",
            "BEGIN:STANDARD",
            "DTSTART:19701025T030000",
            "TZOFFSETFROM:+0200",
            "TZOFFSETTO:+0100",
            "END:STANDARD",
            "END:VTIMEZONE",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}",
            "SUMMARY:Dentist",
            *(extra or []),
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )


class CalendarEditPreservationTests(unittest.TestCase):
    def test_title_edit_keeps_notes_location_reminders_attendees_and_timezones(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, href = _connected(tmp, "owner-keep")
            start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)
            ics = _event_ics(
                "keep-id",
                start,
                [
                    "DESCRIPTION:Bring the insurance card",
                    "LOCATION:12 Main Street",
                    "ORGANIZER:mailto:alice@example.test",
                    "ATTENDEE;CN=Bob:mailto:bob@example.test",
                    "X-CUSTOM:kept",
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    "DESCRIPTION:Reminder",
                    "TRIGGER:-PT30M",
                    "END:VALARM",
                ],
            )
            with patch(
                "ctl.nextcloud_calendar.httpx.request",
                return_value=httpx.Response(207, content=_multistatus(href, "keep-id", ics)),
            ):
                listed = events("owner-keep", paths)["events"][0]
            replies = [
                httpx.Response(200, content=ics.encode(), headers={"ETag": '"one"'}),
                httpx.Response(204),
            ]
            with patch("ctl.nextcloud_calendar.httpx.request", side_effect=replies) as request:
                update_event(
                    "owner-keep",
                    listed["id"],
                    {
                        "title": "Dentist (moved)",
                        "start": (start + timedelta(hours=2)).isoformat(),
                        "end": (start + timedelta(hours=3)).isoformat(),
                        "all_day": False,
                        "revision": listed["revision"],
                    },
                    paths,
                )
            put = request.call_args_list[1]
            body = put.kwargs["content"].decode()
            self.assertEqual(put.kwargs["headers"]["If-Match"], '"one"')
            for kept in (
                "Bring the insurance card",
                "12 Main Street",
                "ATTENDEE;CN=Bob",
                "ORGANIZER",
                "BEGIN:VALARM",
                "TRIGGER:-PT30M",
                "BEGIN:VTIMEZONE",
                "X-CUSTOM:kept",
            ):
                self.assertIn(kept, body)
            self.assertIn("SUMMARY:Dentist (moved)", body)
            self.assertIn("SEQUENCE:1", body)
            self.assertEqual(body.count("SUMMARY:"), 1)

    def test_edit_refuses_when_the_event_changed_since_it_was_shown(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, href = _connected(tmp, "owner-race")
            start = datetime.now(UTC) + timedelta(days=2)
            ics = _event_ics("race-id", start)
            with patch(
                "ctl.nextcloud_calendar.httpx.request",
                return_value=httpx.Response(207, content=_multistatus(href, "race-id", ics)),
            ):
                listed = events("owner-race", paths)["events"][0]
            newer = httpx.Response(200, content=ics.encode(), headers={"ETag": '"two"'})
            with patch("ctl.nextcloud_calendar.httpx.request", return_value=newer) as request:
                with self.assertRaisesRegex(CalendarError, "changed in another"):
                    update_event(
                        "owner-race",
                        listed["id"],
                        {
                            "title": "x",
                            "start": start.isoformat(),
                            "end": (start + timedelta(hours=1)).isoformat(),
                            "revision": listed["revision"],
                        },
                        paths,
                    )
            self.assertEqual([call.args[0] for call in request.call_args_list], ["GET"])


class CalendarCacheTests(unittest.TestCase):
    def test_home_preview_does_not_hide_month_events_from_editing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, href = _connected(tmp, "owner-views")
            far = datetime.now(UTC).replace(microsecond=0) + timedelta(days=45)
            far_ics = _event_ics("far-id", far)
            month_start = far - timedelta(days=10)
            with patch(
                "ctl.nextcloud_calendar.httpx.request",
                return_value=httpx.Response(207, content=_multistatus(href, "far-id", far_ics)),
            ):
                month = events("owner-views", paths, start=month_start, end=month_start + timedelta(days=30), limit=100)
            empty = b'<d:multistatus xmlns:d="DAV:"></d:multistatus>'
            with patch("ctl.nextcloud_calendar.httpx.request", return_value=httpx.Response(207, content=empty)):
                events("owner-views", paths)  # Home preview: next 30 days only
            # The month view is served from cache again; its event must still be editable.
            events("owner-views", paths, start=month_start, end=month_start + timedelta(days=30), limit=100)
            listed = month["events"][0]
            replies = [httpx.Response(200, content=far_ics.encode(), headers={"ETag": '"one"'}), httpx.Response(204)]
            with patch("ctl.nextcloud_calendar.httpx.request", side_effect=replies):
                result = update_event(
                    "owner-views",
                    listed["id"],
                    {
                        "title": "Later",
                        "start": far.isoformat(),
                        "end": (far + timedelta(hours=1)).isoformat(),
                        "revision": listed["revision"],
                    },
                    paths,
                )
        self.assertTrue(result["ok"])

    def test_expired_and_excess_ranges_are_evicted(self):
        from ctl import nextcloud_calendar

        now = datetime.now(UTC)
        nextcloud_calendar._CACHE.clear()
        for index in range(nextcloud_calendar._MAX_RANGES + 10):
            nextcloud_calendar._CACHE[f"o:{index}"] = (now - timedelta(seconds=index), {}, {})
        nextcloud_calendar._CACHE["o:old"] = (now - timedelta(hours=1), {}, {})
        nextcloud_calendar._prune_ranges(now)
        self.assertLessEqual(len(nextcloud_calendar._CACHE), nextcloud_calendar._MAX_RANGES)
        self.assertNotIn("o:old", nextcloud_calendar._CACHE)
        self.assertIn("o:0", nextcloud_calendar._CACHE)
        nextcloud_calendar._CACHE.clear()


if __name__ == "__main__":
    unittest.main()
