from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

from ctl import calendar_secrets
from ctl.control_state import ControlState
from ctl.nextcloud_calendar import CalendarError, create_event, delete_event, discover, events, update_event
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
        xml = f'''<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:propstat><d:prop><c:calendar-home-set><d:href>/remote.php/dav/calendars/{username}/</d:href></c:calendar-home-set></d:prop></d:propstat></d:response></d:multistatus>'''.encode()
        return httpx.Response(207, content=xml)

    def test_discovery_rejects_href_outside_users_calendar_home(self):
        xml = b'''<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>/remote.php/dav/calendars/bob/private/</d:href><d:propstat><d:prop><d:displayname>Private</d:displayname><d:resourcetype><d:collection/><c:calendar/></d:resourcetype></d:prop></d:propstat></d:response></d:multistatus>'''
        response = httpx.Response(207, content=xml)
        with patch("ctl.nextcloud_calendar.httpx.request",
                   side_effect=[self.principal_response(), response]):
            with self.assertRaises(CalendarError):
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
            ics = "\r\n".join([
                "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:private-id",
                f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}",
                "SUMMARY:Planning", "DESCRIPTION:must not leave backend", "ATTENDEE:mailto:a@example.com",
                "END:VEVENT", "END:VCALENDAR", "",
            ])
            xml = f'''<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>{href}private-id.ics</d:href><d:propstat><d:prop><d:getetag>"event-one"</d:getetag><c:calendar-data><![CDATA[{ics}]]></c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>'''.encode()
            response = httpx.Response(207, content=xml)
            with patch("ctl.nextcloud_calendar.httpx.request", return_value=response):
                result = events("owner", paths)
            self.assertEqual(result["events"][0]["title"], "Planning")
            self.assertNotIn("description", result["events"][0])
            self.assertNotIn("attendees", result["events"][0])

    def test_event_mutations_use_the_selected_owner_calendar(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            href = "/remote.php/dav/calendars/alice/personal/"
            state.set_calendar_connection("owner-write", "al•••e",
                                          [{"id": "calendar-write", "name": "Personal", "href": href}],
                                          "calendar-write")
            calendar_secrets.save("owner-write", "alice", "secret", paths)
            payload = {"title": "Private meeting", "start": "2026-10-01T10:00:00+00:00",
                       "end": "2026-10-01T11:00:00+00:00"}
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
            state.set_calendar_connection("owner-revision", "al•••e",
                                          [{"id": "calendar-revision", "name": "Personal", "href": href}],
                                          "calendar-revision")
            calendar_secrets.save("owner-revision", "alice", "secret", paths)
            start = datetime.now(UTC) + timedelta(days=1)
            ics = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:revision-id",
                                 f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
                                 f"DTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}",
                                 "SUMMARY:Original", "END:VEVENT", "END:VCALENDAR", ""])
            xml = f'''<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>{href}revision-id.ics</d:href><d:propstat><d:prop><d:getetag>"revision-one"</d:getetag><c:calendar-data><![CDATA[{ics}]]></c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>'''.encode()
            with patch("ctl.nextcloud_calendar.httpx.request", return_value=httpx.Response(207, content=xml)):
                event_id = events("owner-revision", paths)["events"][0]["id"]
            stale = {"title": "Changed", "start": start.isoformat(),
                     "end": (start + timedelta(hours=1)).isoformat(), "revision": "wrong"}
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
            ics = "\r\n".join([
                "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:all-day-series",
                f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{(start + timedelta(days=1)).strftime('%Y%m%d')}",
                "RRULE:FREQ=DAILY;COUNT=3", f"EXDATE;VALUE=DATE:{excluded.strftime('%Y%m%d')}",
                "SUMMARY:Private day", "DESCRIPTION:hidden", "END:VEVENT", "END:VCALENDAR", "",
            ])
            xml = f'''<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>{href}all-day-series.ics</d:href><d:propstat><d:prop><d:getetag>"event-two"</d:getetag><c:calendar-data><![CDATA[{ics}]]></c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>'''.encode()
            with patch("ctl.nextcloud_calendar.httpx.request",
                       return_value=httpx.Response(207, content=xml)):
                result = events("recurring-owner", paths)
            self.assertEqual(len(result["events"]), 2)
            self.assertTrue(all(item["all_day"] for item in result["events"]))
            self.assertTrue(all("T" not in item["start"] for item in result["events"]))
            self.assertNotIn(excluded.isoformat(), {item["start"] for item in result["events"]})


if __name__ == "__main__":
    unittest.main()
