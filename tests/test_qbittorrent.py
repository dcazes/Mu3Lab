"""qBittorrent: its VPN is an optional Compose part, and its chat tools refuse what they must never do."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ctl.engine.compose import Compose, option_env, option_files
from tests.test_media_connectors import adapter

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/qbittorrent"


class VpnOptionTests(unittest.TestCase):
    def project(self, env: str) -> Path:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder))
        for name in ("docker-compose.yml", "docker-compose.option-vpn.yml"):
            (folder / name).write_text((APP / name).read_text())
        (folder / ".env").write_text(env)
        return folder

    def test_the_vpn_switch_names_its_compose_part(self):
        self.assertEqual(option_env(APP / "docker-compose.option-vpn.yml"), "MU3LAB_OPTION_VPN")

    def test_the_vpn_part_is_used_only_while_switched_on(self):
        self.assertEqual(option_files(self.project("MU3LAB_OPTION_VPN=false\n")), [])
        self.assertEqual(option_files(self.project("")), [])
        on = self.project("MU3LAB_OPTION_VPN=true\n")
        self.assertEqual(option_files(on), [on / "docker-compose.option-vpn.yml"])

    def test_compose_gets_the_vpn_part_once(self):
        on = self.project("MU3LAB_OPTION_VPN=true\n")
        part = on / "docker-compose.option-vpn.yml"
        files = Compose(on).files(extra=[part])
        self.assertEqual(files, [on / "docker-compose.yml", part])


class ToolLimitTests(unittest.TestCase):
    def setUp(self):
        self.adapter = adapter("qbittorrent")

    def test_removing_never_asks_for_the_files_to_be_deleted(self):
        sent = []
        self.adapter._post_form = lambda path, values: sent.append((path, values)) or "Ok."
        self.adapter._qbittorrent("remove_torrents", {"hashes": ["A" * 40]})
        self.assertEqual(sent, [("/api/v2/torrents/delete", {"hashes": "a" * 40, "deleteFiles": "false"})])

    def test_only_listed_hashes_are_accepted(self):
        for hashes in (["all"], ["a" * 39], [], ["a" * 40] * 51, "a" * 40):
            with self.subTest(hashes=hashes), self.assertRaises(ValueError):
                self.adapter._qbittorrent("pause_torrents", {"hashes": hashes})

    def test_only_one_magnet_or_web_link_is_added(self):
        for link in ("file:///etc/passwd", "magnet:?xt=a\nhttp://other", "ftp://example.test/x.torrent"):
            with self.subTest(link=link), self.assertRaises(ValueError):
                self.adapter._qbittorrent("add_torrent", {"link": link})


if __name__ == "__main__":
    unittest.main()


def _sorter():
    import importlib.util

    spec = importlib.util.spec_from_file_location("qbittorrent_finished", APP / "finished.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SortingTests(unittest.TestCase):
    """A finished video with no category moves on to where Jellyfin looks."""

    def setUp(self):
        self.sort = _sorter()

    def test_an_episode_goes_to_its_show_and_season(self):
        files = [{"name": "Love.Is.Blind.S11E12.480p.x264-mSD[EZTVx.to].mkv", "size": 666}]
        self.assertEqual(
            self.sort.destination("Love.Is.Blind.S11E12.480p.x264-mSD[EZTVx.to]", files),
            "/media/shows/Love Is Blind/Season 11",
        )

    def test_season_names_and_years_follow_jellyfins_layout(self):
        self.assertEqual(self.sort.show_and_season("Doctor.Who.2005.S01.1080p"), ("Doctor Who (2005)", 1))
        self.assertEqual(self.sort.show_and_season("[Group] Frieren Season 2 (1080p)"), ("Frieren", 2))
        self.assertEqual(self.sort.show_and_season("The Office US 3x05 HDTV"), ("The Office US", 3))

    def test_a_film_goes_to_movies(self):
        for name in ("Se7en.1995.1080p.BluRay", "Blade.Runner.2049.2017.2160p", "Film.1996.1920x1080"):
            self.assertIsNone(self.sort.show_and_season(name), name)
        files = [{"name": "Film/Film.mkv", "size": 900}, {"name": "Film/Film.nfo", "size": 5}]
        self.assertEqual(self.sort.destination("Film.2020.1080p", files), "/media/movies")

    def test_a_folder_named_without_a_season_uses_its_biggest_video(self):
        files = [{"name": "Pack/Show.Name.S03E01.mkv", "size": 900}, {"name": "Pack/sample.mkv", "size": 9}]
        self.assertEqual(self.sort.destination("Pack", files), "/media/shows/Show Name/Season 03")

    def test_anything_but_video_stays_in_downloads(self):
        self.assertIsNone(self.sort.destination("Album", [{"name": "Album/01.flac", "size": 900}]))
        self.assertIsNone(self.sort.destination("Empty", []))

    def test_a_finished_torrent_is_stopped_filed_and_removed_with_its_files_kept(self):
        where = {"save_path": "/media/downloads"}
        calls = []

        def call(path, data=None):
            calls.append((path.split("?")[0], data))
            if path.startswith("torrents/info"):
                return [{"hash": "a" * 40, "name": "Show.S01E01", "state": "stoppedUP", "category": "", **where}]
            if path.startswith("torrents/files"):
                return [{"name": "Show.S01E01.mkv", "size": 900}]
            if path == "torrents/setLocation":
                where["save_path"] = data["location"]
            return ""

        self.sort.call = call
        self.sort.log = lambda message: None
        self.sort.finish({"hash": "a" * 40, "name": "Show.S01E01", "category": "", **where}, libraries=True)
        writes = [(path, data) for path, data in calls if data is not None]
        self.assertEqual(
            writes,
            [
                ("torrents/stop", {"hashes": "a" * 40}),
                ("torrents/setLocation", {"hashes": "a" * 40, "location": "/media/shows/Show/Season 01"}),
                ("torrents/delete", {"hashes": "a" * 40, "deleteFiles": "false"}),
            ],
        )
