"""Each person's Home board is checked before it is saved, so what is read back can always be drawn."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ctl import camera, thermostat
from ctl.home_board import STARTER, BoardError, _upgrade, clean, placement_of
from ctl.runtime import RuntimePaths
from ctl.store.secrets import SecretStore


class BoardTests(unittest.TestCase):
    def test_the_starter_board_keeps_its_shape(self):
        shape = [(w["kind"], w["x"], w["y"], w["w"], w["h"]) for w in clean(STARTER, is_admin=False)]
        self.assertEqual(
            shape,
            [("weather", 0, 0, 6, 2), ("apps", 6, 0, 6, 2), ("ask", 0, 2, 6, 4), ("calendar", 6, 2, 6, 4)],
        )

    def test_unknown_kinds_go_and_sizes_stay_within_each_kinds_limits(self):
        board = clean(
            [
                {"id": "a", "kind": "camera-of-the-future", "w": 2, "h": 2},
                {"id": "b", "kind": "ask", "w": 9, "h": 1},
                {"id": "c", "kind": "weather", "w": "wide", "h": None},
            ],
            is_admin=False,
        )
        # Ask is at least 2 rows tall; a size that is not a number becomes the kind's starting size.
        self.assertEqual([(w["kind"], w["w"], w["h"]) for w in board], [("ask", 9, 2), ("weather", 6, 2)])

    def test_widgets_never_overlap_and_unplaced_ones_take_the_first_free_space(self):
        board = clean(
            [
                {"id": "a", "kind": "weather", "x": 0, "y": 0, "w": 6, "h": 2},
                {"id": "b", "kind": "weather", "x": 3, "y": 1, "w": 6, "h": 2},
                {"id": "c", "kind": "weather", "w": 6, "h": 2},
                {"id": "d", "kind": "weather", "x": 20, "y": 0, "w": 6, "h": 2},
            ],
            is_admin=False,
        )
        spots = {w["id"]: (w["x"], w["y"]) for w in board}
        # b overlapped a and c had no place: both go to the first free space, in order; d is pulled onto the board.
        self.assertEqual(spots, {"a": (0, 0), "d": (6, 0), "b": (0, 2), "c": (6, 2)})

    def test_a_board_from_before_positions_keeps_its_arrangement(self):
        legacy = [{"id": "w", "kind": "weather", "w": 2, "h": 1}, {"id": "a", "kind": "ask", "w": 2, "h": 2}]
        board = clean(_upgrade(legacy), is_admin=False)
        self.assertEqual([(w["x"], w["y"], w["w"], w["h"]) for w in board], [(0, 0, 6, 2), (6, 0, 6, 4)])

    def test_placement_and_hide_on_phone_have_safe_defaults(self):
        self.assertEqual(placement_of("gravity"), "gravity")
        self.assertEqual(placement_of("sideways"), "free")
        board = clean([{"id": "a", "kind": "ask", "hide_on_phone": "yes"}], is_admin=False)
        self.assertFalse(board[0]["hide_on_phone"])

    def test_settings_keep_only_what_each_kind_understands(self):
        board = clean(
            [
                {
                    "id": "s",
                    "kind": "stocks",
                    "settings": {"symbols": ["aapl", "AAPL", "rm -rf /", "vfv.to"], "colour": "red"},
                },
                {
                    "id": "w",
                    "kind": "weather",
                    "settings": {"units": "kelvin", "place": {"latitude": 99, "longitude": 0}},
                },
                {
                    "id": "p",
                    "kind": "weather",
                    "settings": {
                        "units": "fahrenheit",
                        "place": {"name": "Toronto", "latitude": 43.7, "longitude": -79.4},
                    },
                },
                {"id": "c", "kind": "calendar", "settings": {"days": 365, "view": "year"}},
            ],
            is_admin=False,
        )
        self.assertEqual(board[0]["settings"], {"symbols": ["AAPL", "VFV.TO"]})
        # An impossible place is dropped, and unknown units fall back to Celsius.
        self.assertEqual(board[1]["settings"], {"units": "celsius"})
        self.assertEqual(board[2]["settings"]["place"], {"name": "Toronto", "latitude": 43.7, "longitude": -79.4})
        self.assertEqual(board[3]["settings"], {"view": "auto", "week_start": "sunday"})

    def test_ids_are_made_unique_and_safe(self):
        board = clean(
            [{"id": "same", "kind": "stocks"}, {"id": "same", "kind": "stocks"}, {"id": "<script>", "kind": "ask"}],
            is_admin=False,
        )
        ids = [widget["id"] for widget in board]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(ids[0], "same")
        self.assertTrue(ids[2].startswith("ask-"))

    def test_a_board_that_is_not_a_list_or_too_long_is_refused(self):
        with self.assertRaises(BoardError):
            clean({"widgets": []}, is_admin=False)
        with self.assertRaises(BoardError):
            clean([{"kind": "ask"}] * 25, is_admin=False)

    def test_the_camera_widget_is_for_administrators_only(self):
        widgets = [{"id": "c", "kind": "camera"}, {"id": "t", "kind": "thermostat"}]
        self.assertEqual([w["kind"] for w in clean(widgets, is_admin=False)], ["thermostat"])
        self.assertEqual([w["kind"] for w in clean(widgets, is_admin=True)], ["camera", "thermostat"])


class CameraSettingsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = SecretStore(RuntimePaths(Path(folder.name)))
        (Path(folder.name) / "state").mkdir()

    def test_the_password_is_kept_but_never_shown_and_a_blank_one_keeps_it(self):
        shown = camera.save({"host": "192.168.1.40", "username": "viewer", "password": "p@ss/word"}, self.store)
        self.assertTrue(shown["has_password"])
        self.assertNotIn("password", shown)
        camera.save({"host": "192.168.1.41", "username": "viewer", "password": ""}, self.store)
        self.assertEqual(camera.saved(self.store)["password"], "p@ss/word")
        # Characters that mean something in an address are escaped in the stream address.
        source = camera._source(camera.saved(self.store), "sd_path")
        self.assertEqual(source, "rtsp://viewer:p%40ss%2Fword@192.168.1.41:554/h264Preview_01_sub")

    def test_an_address_or_path_that_could_reach_elsewhere_is_refused(self):
        for values in (
            {"host": "cam.local/../x"},
            {"host": "192.168.1.40", "sd_path": "a b"},
            {"host": "192.168.1.40", "port": "rtsp"},
            {"host": ""},
        ):
            with self.subTest(values=values), self.assertRaises(camera.CameraError):
                camera.save(values, self.store)


class ThermostatCodeTests(unittest.TestCase):
    def test_a_homekit_code_is_eight_digits_in_any_spacing(self):
        self.assertEqual(thermostat._code("12345678"), "123-45-678")
        self.assertEqual(thermostat._code("123 45 678"), "123-45-678")
        with self.assertRaises(thermostat.ThermostatError):
            thermostat._code("1234-567")

    def test_nothing_paired_reads_as_not_paired(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SecretStore(RuntimePaths(Path(folder)))
            self.assertIsNone(thermostat.paired(store))


if __name__ == "__main__":
    unittest.main()
