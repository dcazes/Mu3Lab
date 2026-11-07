"""Chat says which apps it can reach right now, and why not, instead of guessing.

Mu3Lab writes each assistant's live status into its instructions, keeps out-of-reach assistants in
Open WebUI's model menu marked as such, and installs a filter that checks every message.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ctl import chat_ops, mcp_ops
from ctl.integrations import openwebui
from ctl.integrations.openwebui_filter import MARK, Filter
from ctl.lifecycle import app_control


def assistants(installation: str = "running", connector: str = "live", *, gateway: bool = True) -> dict:
    """Everyone's Open WebUI assistants, by app, with Mealie in the given state."""
    state = Mock()
    state.mcp_server.side_effect = lambda connector_id: (
        {"enabled": True, "state": connector} if connector_id.startswith("mealie") else None
    )
    state.app_installation.side_effect = lambda app_id: {"state": installation} if app_id == "mealie" else None
    with (
        patch("ctl.chat_ops.ControlState.runtime", return_value=state),
        patch("ctl.contextforge_ops.virtual_server", return_value="srv"),
        patch("ctl.contextforge.endpoint", return_value="http://contextforge/servers/srv/mcp"),
        patch("ctl.calendar_tools.assistants", return_value=[]),
    ):
        return {item["id"]: item for item in chat_ops.desired_assistants(gateway=gateway)}


class AssistantStatusTests(unittest.TestCase):
    def test_a_running_app_with_a_live_connector_is_ready_and_says_nothing_extra(self):
        mealie = assistants()["mealie"]
        self.assertEqual((mealie["status"], mealie["active"]), ("ready", True))
        self.assertNotIn("Right now", mealie["instructions"])
        # Even when ready, a tool that did not connect is never replaced by a guess.
        self.assertIn("If you have no Mealie tools in this chat, they did not connect", mealie["instructions"])

    def test_a_stopped_app_is_named_first_with_how_to_start_it(self):
        mealie = assistants("stopped", "stopped")["mealie"]
        self.assertEqual((mealie["status"], mealie["active"]), ("stopped", False))
        instructions = mealie["instructions"]
        self.assertIn("## Right now: Mealie is out of reach\nMealie is stopped", instructions)
        self.assertIn("open Mealie under Apps and choose Start", instructions)
        # Open WebUI fills in the person's role, so the assistant knows whether they can start it themselves.
        self.assertIn('the role "{{USER_ROLE}}"', instructions)
        self.assertLess(instructions.index("## Right now"), instructions.index("## Mealie"))

    def test_while_contextforge_is_down_no_assistant_is_ready(self):
        mealie = assistants(gateway=False)["mealie"]
        self.assertEqual((mealie["status"], mealie["active"]), ("gateway_down", False))
        self.assertIn("ContextForge, the part of Mu3Lab that connects chat", mealie["instructions"])
        self.assertIn("open ContextForge under Apps and choose Start", mealie["instructions"])

    def test_a_connector_that_needs_attention_says_so(self):
        mealie = assistants(connector="degraded")["mealie"]
        self.assertEqual(mealie["status"], "connector_down")
        self.assertIn("its chat connector is degraded", mealie["instructions"])

    def test_one_persons_assistants_know_whether_they_can_fix_it(self):
        state = Mock()
        state.mcp_server.side_effect = lambda connector_id: (
            {"enabled": True, "state": "stopped"} if connector_id.startswith("mealie") else None
        )
        state.app_installation.side_effect = lambda app_id: {"state": "stopped"} if app_id == "mealie" else None
        with (
            patch("ctl.chat_ops.ControlState.runtime", return_value=state),
            patch("ctl.contextforge_ops.virtual_server", return_value="srv"),
            patch("ctl.contextforge.endpoint", return_value="http://contextforge/servers/srv/mcp"),
            patch("ctl.calendar_tools.assistants", return_value=[]),
        ):
            member = chat_ops.desired_assistants("u1", "m@example.com", gateway=True)
        mealie = next(item for item in member if item["id"] == "mealie")
        self.assertIn("is not an administrator, so they need to ask one", mealie["instructions"])
        # No ContextForge key is fetched for an assistant that cannot reach its app.
        self.assertNotIn("token", mealie)


class OpenWebUiMenuTests(unittest.TestCase):
    """What Open WebUI is given; checked live against Open WebUI itself (no imitation of its API here)."""

    def test_an_out_of_reach_assistant_is_marked_in_the_menu(self):
        self.assertEqual(openwebui.menu_name("Mealie", "stopped"), "Mealie (stopped)")
        self.assertEqual(openwebui.menu_name("Mealie", "gateway_down"), "Mealie (unavailable)")
        self.assertEqual(openwebui.menu_name("Mealie", "ready"), "Mealie")
        # A title with a note of its own keeps one pair of brackets.
        self.assertEqual(openwebui.menu_name("qBittorrent (admins)", "stopped"), "qBittorrent (admins, stopped)")

    def test_the_main_assistant_lists_a_stopped_app_with_how_to_start_it(self):
        main = openwebui.main_model([assistants("stopped", "stopped")["mealie"]])["params"]["system"]
        self.assertIn("- Mealie (stopped): ", main)
        self.assertIn("Out of reach right now: Mealie is stopped.", main)

    def test_the_main_assistant_says_once_that_contextforge_is_down(self):
        main = openwebui.main_model([assistants(gateway=False)["mealie"]], gateway_down="ContextForge")["params"][
            "system"
        ]
        self.assertIn("- Mealie (unavailable): ", main)
        self.assertIn("Right now ContextForge, the part of Mu3Lab that connects chat", main)
        self.assertNotIn("Out of reach right now", main)

    def test_a_ready_assistant_is_listed_plainly(self):
        main = openwebui.main_model([assistants()["mealie"]])["params"]["system"]
        self.assertIn("- Mealie: ", main)
        self.assertNotIn("Out of reach", main)


def run(coroutine):
    return asyncio.run(coroutine)


# The longest tool name ContextForge gave each assistant on the live install, 2026-11-05.
LONGEST_TOOLS = {
    "immich--p3ea0b54595": "immich-photo-manager-remove-assets-from-album",
    "calendar--p3ea0b54595": "mu3lab-calendar-p3ea0b54595-create-event",
    "nextcloud--p3ea0b54595": "nextcloud-context-agent-write-text-file",
    "grocy": "grocy-community-recipes-shopping-add-missing-products",
    "audiobookshelf": "audiobookshelf-adapter-list-recent-items",
}


class ToolNameTests(unittest.TestCase):
    """Open WebUI cuts a tool name past 64 characters to a code the model must copy exactly."""

    def test_every_tool_name_fits_without_being_cut(self):
        for item_id, tool in LONGEST_TOOLS.items():
            with self.subTest(item_id):
                self.assertLessEqual(len(f"{openwebui.connection_id(item_id)}_{tool}"), 64)

    def test_connections_are_short_and_still_tell_people_apart(self):
        self.assertEqual(openwebui.connection_id("grocy"), "m3-grocy")
        self.assertEqual(openwebui.connection_id("immich--p3ea0b54595"), "m3-immich-b54595")
        self.assertEqual(openwebui.tool_id("grocy"), "server:mcp:m3-grocy")


class DateTests(unittest.TestCase):
    def test_open_webui_assistants_are_told_the_date_lobechat_ones_are_not(self):
        self.assertIn("{{CURRENT_DATE}}", assistants()["mealie"]["instructions"])
        self.assertIn("{{CURRENT_TIMEZONE}}", openwebui.main_model([])["params"]["system"])
        state = Mock()
        state.mcp_server.return_value = {"enabled": True, "state": "live"}
        state.app_installation.return_value = {"state": "running"}
        with (
            patch("ctl.chat_ops.ControlState.runtime", return_value=state),
            patch("ctl.contextforge_ops.virtual_server", return_value="srv"),
            patch("ctl.contextforge_ops.person_key", return_value="key"),
            patch("ctl.contextforge.endpoint", return_value="http://contextforge/servers/srv/mcp"),
            patch("ctl.calendar_tools.assistants", return_value=[]),
        ):
            lobechat = chat_ops.desired_assistants("u1", "m@example.com", gateway=True)
        self.assertNotIn("{{CURRENT_DATE}}", next(item for item in lobechat if item["id"] == "mealie")["instructions"])


class CalendarTimeTests(unittest.TestCase):
    def test_a_time_without_a_zone_means_the_household_clock(self):
        from ctl.api.routes import chat_calendar

        with patch("ctl.hostinfo.timezone", return_value="America/Toronto"):
            self.assertEqual(chat_calendar._local("2026-11-06T10:00:00"), "2026-11-06T10:00:00-05:00")
            # An offset, UTC, or an all-day date is left as sent.
            self.assertEqual(chat_calendar._local("2026-11-06T10:00:00+01:00"), "2026-11-06T10:00:00+01:00")
            self.assertEqual(chat_calendar._local("2026-11-06T15:00:00Z"), "2026-11-06T15:00:00Z")
            self.assertEqual(chat_calendar._local("2026-11-06"), "2026-11-06")

    def test_an_offset_is_stored_as_utc_not_as_an_unknown_zone(self):
        from icalendar import Calendar

        from ctl.nextcloud_calendar import _write_ical

        ics = _write_ical({"title": "t", "start": "2026-11-06T10:00:00-05:00", "end": "2026-11-06T10:30:00-05:00"}, "u")
        start = Calendar.from_ical(ics).walk("VEVENT")[0].get("DTSTART")
        self.assertEqual(start.to_ical(), b"20261106T150000Z")

    def test_events_read_back_on_the_household_clock(self):
        from zoneinfo import ZoneInfo

        from ctl.api import models
        from ctl.api.routes import chat_calendar

        event = models.CalendarEvent(
            id="e", title="t", start="2026-11-06T15:00:00+00:00", end="2026-11-06T15:30:00+00:00", all_day=False
        )
        shown = chat_calendar._shown_locally(event, ZoneInfo("America/Toronto"))
        self.assertEqual((shown.start, shown.end), ("2026-11-06T10:00:00-05:00", "2026-11-06T10:30:00-05:00"))


class FilterWordingTests(unittest.TestCase):
    def test_an_apps_own_spelling_is_kept_and_your_calendar_opens_a_sentence(self):
        from ctl import agent_context

        stopped = agent_context.filter_entry("qBittorrent", "stopped", gateway="ContextForge")
        self.assertTrue(stopped["line"].startswith("qBittorrent is stopped"))
        calendar = agent_context.filter_entry("your calendar", "gateway_down", gateway="ContextForge")
        self.assertTrue(calendar["line"].startswith("ContextForge, the part of Mu3Lab"))
        failed = agent_context.filter_entry("your calendar", "ready", gateway="ContextForge")
        self.assertTrue(failed["line"].startswith("Your calendar's tools did not connect"))


class StatusFilterTests(unittest.TestCase):
    def setUp(self):
        self.filter = Filter()
        self.filter.valves.apps = json.dumps(
            {
                "mu3lab-app-mealie": {
                    "name": "Mealie",
                    "person": "",
                    "note": "Mealie is stopped, so Mealie's tools did not connect.",
                    "line": "Mealie is stopped, so this chat can't reach Mealie right now.",
                },
                "mu3lab-app-calendar--abc": {
                    "name": "your calendar",
                    "person": "someone@example.com",
                    "note": "Their calendar did not connect.",
                    "line": "Their calendar is out of reach.",
                },
            }
        )
        self.events: list[dict] = []

    async def emit(self, event: dict) -> None:
        self.events.append(event)

    def send(self, connected: dict, *, role: str = "user", message: str = "m1") -> dict:
        body = {"messages": [{"role": "system", "content": "You are the Mealie assistant."}]}
        model = {"info": {"meta": {"toolIds": ["server:mcp:mu3lab-app-mealie", "server:mcp:mu3lab-app-calendar--abc"]}}}
        metadata = {"mcp_clients": connected, "message_id": message, "tool_ids": []}
        return run(self.filter.request(body, {"role": role, "email": "me@example.com"}, model, metadata, self.emit))

    def test_tools_that_did_not_connect_are_told_to_the_model_and_shown_to_the_person(self):
        body = self.send({})
        system = body["messages"][0]["content"]
        self.assertIn(MARK, system)
        self.assertIn("Mealie is stopped, so Mealie's tools did not connect.", system)
        self.assertIn("not an administrator", system)
        # Someone else's calendar is not this person's to miss.
        self.assertNotIn("Their calendar", system)
        self.assertEqual(
            self.events,
            [
                {
                    "type": "status",
                    "data": {
                        "description": "Mealie is stopped, so this chat can't reach Mealie right now.",
                        "done": True,
                    },
                }
            ],
        )

    def test_connected_tools_change_nothing(self):
        body = self.send({"mu3lab-app-mealie": object()})
        self.assertEqual(body["messages"][0]["content"], "You are the Mealie assistant.")
        self.assertEqual(self.events, [])

    def test_the_tool_loop_adds_nothing_twice(self):
        self.send({})
        self.send({})
        self.assertEqual(len(self.events), 1)

    def test_an_administrator_is_told_they_can_fix_it(self):
        body = self.send({}, role="admin")
        self.assertIn("is an administrator, so they can do this themselves", body["messages"][0]["content"])

    def test_it_never_stops_a_chat(self):
        self.filter.valves.apps = "not json"
        body = {"messages": "broken"}
        self.assertIs(run(self.filter.request(body, None, None, None, None)), body)

        async def failing(_event):
            raise RuntimeError("socket closed")

        self.filter.valves.apps = json.dumps({"mu3lab-app-mealie": {"name": "Mealie", "note": "n", "line": "l"}})
        body = {"messages": [{"role": "system", "content": ""}]}
        model = {"info": {"meta": {"toolIds": ["server:mcp:mu3lab-app-mealie"]}}}
        self.assertIs(run(self.filter.request(body, {}, model, {}, failing)), body)

    def test_open_webui_can_read_its_header(self):
        source = openwebui.FILTER_SOURCE.read_text()
        self.assertTrue(source.startswith('"""\ntitle: Mu3Lab app status\n'))
        self.assertTrue(openwebui.FILTER_ID.isidentifier())


class ChatFollowsLifecycleTests(unittest.TestCase):
    def test_taking_a_connector_down_updates_chat_even_when_contextforge_is_stopped(self):
        server = SimpleNamespace(per_person=False)
        with (
            patch("ctl.job_guard.checkpoint"),
            patch("ctl.contextforge_ops.disconnect", return_value=(False, "ContextForge could not be reached.")),
            patch("ctl.chat_ops.sync_agents", return_value=(True, "ready")) as sync,
        ):
            self.assertEqual(mcp_ops.unbind(server, print), (False, "ContextForge could not be reached."))
        sync.assert_called_once()

    def test_stopping_contextforge_updates_every_assistant(self):
        service = SimpleNamespace(manifest=SimpleNamespace(capabilities=["tool_gateway"]))
        with (
            patch("ctl.chat_ops.sync_agents", return_value=(True, "ok")) as sync,
            patch("ctl.mcp_ops.reconcile_lifecycle") as reconcile,
        ):
            app_control.refresh_chat(service, Mock(), print, running=False)
            sync.assert_called_once()
            reconcile.assert_not_called()
            # Once it is back, connectors that could not reach it meanwhile are linked again first.
            app_control.refresh_chat(service, Mock(), print, running=True)
            reconcile.assert_called_once()

    def test_a_stopped_chat_app_is_not_synced(self):
        service = SimpleNamespace(manifest=SimpleNamespace(capabilities=["chat"]))
        with patch("ctl.chat_ops.sync_agents", return_value=(True, "ok")) as sync:
            app_control.refresh_chat(service, Mock(), print, running=False)
            sync.assert_not_called()
            app_control.refresh_chat(service, Mock(), print, running=True)
            sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
