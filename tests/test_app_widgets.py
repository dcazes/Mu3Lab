"""App widgets: what a reader returns is trimmed to what the board draws, and each person sees only
the widgets, and the states, that are theirs to see."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ctl import app_widgets
from ctl.manifest.catalog import cached
from ctl.manifest.models import HomeWidgetSpec


class FiguresTests(unittest.TestCase):
    def test_only_the_drawn_shape_survives(self):
        shaped = app_widgets.figures(
            {
                "stats": [
                    {"label": "Loaded", "value": 2, "tone": "good"},
                    {"label": "", "value": "dropped: no label"},
                    {"label": "Odd tone", "value": "x", "tone": "purple"},
                    "not a stat",
                ],
                "items": [
                    {"title": "llama", "progress": 1.7, "url": "javascript:alert(1)", "secret": "kept out"},
                    {"title": "half", "progress": 0.5, "url": "https://host/item"},
                    {"subtitle": "dropped: no title"},
                ],
                "note": "  spaced   out  ",
                "extra": "ignored",
            }
        )
        self.assertEqual(
            shaped["stats"],
            [
                {"label": "Loaded", "value": "2", "tone": "good"},
                {"label": "Odd tone", "value": "x", "tone": ""},
            ],
        )
        self.assertEqual([item["title"] for item in shaped["items"]], ["llama", "half"])
        # Progress stays between 0 and 1; only web and in-dashboard links are kept.
        self.assertEqual(shaped["items"][0]["progress"], 1.0)
        self.assertEqual(shaped["items"][0]["url"], "")
        self.assertNotIn("secret", shaped["items"][0])
        self.assertEqual(shaped["items"][1]["url"], "https://host/item")
        self.assertEqual(shaped["note"], "spaced out")
        self.assertNotIn("extra", shaped)

    def test_anything_but_a_dict_is_an_empty_widget(self):
        self.assertEqual(app_widgets.figures(None)["stats"], [])
        self.assertEqual(app_widgets.figures(["x"])["items"], [])


class AccessTests(unittest.TestCase):
    def test_administrators_only_widgets_are_not_found_for_others(self):
        operators = next(
            app_widgets.kind_name(app.id, spec.id)
            for app in cached().apps
            for spec in app.manifest.widgets
            if spec.audience == "operators"
        )
        self.assertIsNotNone(app_widgets.find(operators, is_admin=True))
        self.assertIsNone(app_widgets.find(operators, is_admin=False))
        self.assertIsNone(app_widgets.find("no-such-app:widget", is_admin=True))
        self.assertIsNone(app_widgets.find("not a kind", is_admin=True))


class StateTests(unittest.TestCase):
    def setUp(self):
        app = next(app for app in cached().apps if app.manifest.widgets)
        self.widget = app_widgets.Offered(app, HomeWidgetSpec(id="probe", title="Probe", description="A test."))
        app_widgets.forget(self.widget.kind)

    def test_an_app_that_cannot_answer_says_why_without_asking_it(self):
        with patch.object(app_widgets, "_reader", side_effect=AssertionError("must not be asked")):
            for service_state, expected in (
                ("not_installed", "not_installed"),
                ("starting", "starting"),
                ("stopped", "stopped"),
                ("needs_setup", "stopped"),
            ):
                with self.subTest(service_state=service_state):
                    reply = app_widgets.read(self.widget, service_state=service_state, is_admin=True)
                    self.assertEqual(reply["state"], expected)
                    self.assertTrue(reply["detail"])

    def test_figures_are_kept_until_refresh_and_a_reader_bug_shows_no_details(self):
        calls = []

        def reader(_ctx):
            calls.append(1)
            return {"stats": [{"label": "Count", "value": len(calls)}]}

        with patch.object(app_widgets, "_reader", return_value=reader):
            first = app_widgets.read(self.widget, service_state="ready", is_admin=True)
            again = app_widgets.read(self.widget, service_state="ready", is_admin=True)
            self.assertEqual(first, again)
            self.assertEqual(len(calls), 1)
            app_widgets.forget(self.widget.kind)
            self.assertEqual(
                app_widgets.read(self.widget, service_state="ready", is_admin=True)["stats"][0]["value"], "2"
            )

        def broken(_ctx):
            raise KeyError("api_key=secret")

        app_widgets.forget(self.widget.kind)
        with patch.object(app_widgets, "_reader", return_value=broken), self.assertLogs(app_widgets.LOG, "ERROR"):
            reply = app_widgets.read(self.widget, service_state="ready", is_admin=True)
        self.assertEqual(reply["state"], "error")
        self.assertNotIn("secret", reply["detail"])


class TimeWordsTests(unittest.TestCase):
    def test_ago_and_until_read_naturally(self):
        import time

        self.assertEqual(app_widgets.ago(time.time() - 30), "just now")
        self.assertEqual(app_widgets.ago(time.time() - 3 * 86400 - 60), "3 days ago")
        self.assertEqual(app_widgets.until(time.time() + 3600 * 5 + 30), "in 5 hours")
        self.assertEqual(app_widgets.ago("not a date"), "")


if __name__ == "__main__":
    unittest.main()
