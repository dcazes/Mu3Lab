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
