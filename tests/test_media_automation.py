"""Prowlarr, Sonarr, Radarr and Seerr: who may reach whom, what qBittorrent leaves to them, and the quality rules."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

import yaml

from ctl import preflight, servarr
from tests.test_qbittorrent import _sorter

ROOT = Path(__file__).resolve().parents[1]
QBITTORRENT_NETWORK = "mu3lab-qbittorrent_default"


def _module(path: Path):
    spec = importlib.util.spec_from_file_location(path.parent.name + "_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _networks(app: str) -> dict:
    return yaml.safe_load((ROOT / "apps" / app / "docker-compose.yml").read_text(encoding="utf-8"))["networks"]


class NetworkTests(unittest.TestCase):
    def test_only_sonarr_and_radarr_join_qbittorrents_own_network(self):
        # qBittorrent skips its login on its own network, so nothing else may join it.
        joined = sorted(
            compose.parent.name
            for compose in (ROOT / "apps").glob("*/docker-compose.yml")
            if QBITTORRENT_NETWORK in compose.read_text(encoding="utf-8")
        )
        self.assertEqual(joined, ["radarr", "sonarr"])

    def test_the_media_apps_meet_on_an_internal_network(self):
        self.assertIn("mu3lab_media", preflight.MU3LAB_NETWORKS)
        self.assertIn("mu3lab_media", preflight.INTERNAL_NETWORKS)
        for app in ("prowlarr", "sonarr", "radarr", "seerr"):
            with self.subTest(app=app):
                self.assertTrue(_networks(app)["mu3lab_media"]["external"])

    def test_flaresolverr_is_reachable_by_prowlarr_alone(self):
        services = yaml.safe_load((ROOT / "apps/prowlarr/docker-compose.yml").read_text(encoding="utf-8"))["services"]
        self.assertEqual(services["flaresolverr"]["networks"], ["default"])
        self.assertNotIn("ports", services["flaresolverr"])


class FinishedTorrentTests(unittest.TestCase):
    def test_a_torrent_sonarr_or_radarr_added_is_only_stopped(self):
        sort = _sorter()
        for category in ("sonarr", "radarr"):
            with self.subTest(category=category):
                calls = []
                torrent = {"hash": "b" * 40, "name": "Show.S01E01", "category": category}
                torrent["save_path"] = f"/media/downloads/{category}"

                def call(path, data=None, torrent=torrent, calls=calls):
                    calls.append((path.split("?")[0], data))
                    return [{**torrent, "state": "stoppedUP"}] if path.startswith("torrents/info") else ""

                sort.call = call
                sort.log = lambda message: None
                sort.finish(torrent, libraries=True)
                writes = [(path, data) for path, data in calls if data is not None]
                self.assertEqual(writes, [("torrents/stop", {"hashes": "b" * 40})])

    def test_programs_are_never_downloaded(self):
        excluded = set(_module(ROOT / "apps/qbittorrent/start.py").EXCLUDED)
        # What Sonarr and Radarr call executable or potentially dangerous (FileExtensions.cs).
        for extension in ("bat", "cmd", "exe", "sh", "arj", "lnk", "lzh", "ps1", "scr", "vbs", "zipx"):
            self.assertIn(f"*.{extension}", excluded)


def _schema() -> list[dict]:
    """A Radarr-like profile template: qualities worst first, some already grouped."""
    names = ["CAM", "TELESYNC", "DVD", "Bluray-576p", "HDTV-720p", "WEBDL-720p", "HDTV-1080p", "WEBDL-1080p"]
    names += ["Bluray-1080p", "Remux-1080p", "WEBDL-2160p", "Bluray-2160p", "BR-DISK"]
    quality = {name: {"id": number, "name": name} for number, name in enumerate(names)}
    flat = [{"quality": quality[name], "items": [], "allowed": False} for name in names if "WEBDL" not in name]
    group = {"id": 1002, "name": "WEB 1080p", "allowed": False, "items": [{"quality": quality["WEBDL-1080p"]}]}
    return [*flat, group, {"quality": quality["WEBDL-720p"]}, {"quality": quality["WEBDL-2160p"]}]


TIERS = [
    {"name": "1080p", "qualities": ["WEBDL-1080p", "Bluray-1080p", "HDTV-1080p"], "cutoff": True},
    {"name": "4K", "qualities": ["WEBDL-2160p", "Bluray-2160p"]},
    {"name": "720p", "qualities": ["WEBDL-720p", "HDTV-720p"]},
    {"name": "DVD", "qualities": ["DVD", "Bluray-576p", "Missing-480p"]},
]


class QualityTests(unittest.TestCase):
    def test_tiers_become_groups_best_last_with_the_cutoff_at_1080p(self):
        items, cutoff = servarr.profile_items(_schema(), TIERS)
        allowed = [item for item in items if item["allowed"]]
        self.assertEqual([item["name"] for item in allowed], ["DVD", "720p", "4K", "1080p"])
        self.assertEqual(cutoff, next(item["id"] for item in allowed if item["name"] == "1080p"))
        # A name the app does not have is skipped.
        self.assertEqual([q["quality"]["name"] for q in allowed[0]["items"]], ["DVD", "Bluray-576p"])

    def test_cams_remuxes_and_disc_images_are_never_allowed(self):
        items, _cutoff = servarr.profile_items(_schema(), TIERS)
        refused = {item["quality"]["name"] for item in items if not item["allowed"]}
        self.assertEqual(refused, {"CAM", "TELESYNC", "Remux-1080p", "BR-DISK"})
        everyone = [q["quality"]["name"] for item in items for q in item["items"]] + sorted(refused)
        self.assertEqual(len(everyone), len(set(everyone)), "each quality is listed once")

    def test_the_shipped_tables_agree_with_the_owners_choices(self):
        for app in ("sonarr", "radarr"):
            with self.subTest(app=app):
                table = json.loads((ROOT / "apps" / app / "quality.json").read_text(encoding="utf-8"))
                self.assertEqual([tier["name"] for tier in table["tiers"]], ["1080p", "4K", "720p", "DVD"])
                self.assertEqual([tier["name"] for tier in table["tiers"] if tier.get("cutoff")], ["1080p"])
                allowed = [name for tier in table["tiers"] for name in tier["qualities"]]
                for name in allowed:
                    self.assertNotRegex(name, "(?i)cam|telesync|remux|br-disk|raw")
                    low, preferred, high = table["sizes"][name]
                    self.assertGreater(low, 0, f"{name} has a minimum size, so fakes are refused")
                    self.assertLess(low, preferred)
                    self.assertLessEqual(preferred, high)
                names = {spec["name"] for spec in table["unwanted"]}
                self.assertTrue({"BR-DISK", "LQ", "LQ (Release Title)", "Upscaled"} <= names)

    def test_a_trash_custom_format_becomes_the_apps_field_list(self):
        spec = {
            "name": "LQ",
            "specifications": [
                {"name": "Group", "implementation": "ReleaseGroupSpecification", "fields": {"value": "^(X)$"}},
            ],
        }
        made = servarr.custom_format(spec)
        self.assertEqual(made["specifications"][0]["fields"], [{"name": "value", "value": "^(X)$"}])
        self.assertFalse(made["specifications"][0]["negate"])
        self.assertFalse(made["includeCustomFormatWhenRenaming"])

    def test_a_masked_secret_counts_as_unchanged(self):
        item = {"fields": [{"name": "apiKey", "value": servarr.MASK}, {"name": "host", "value": "a"}]}
        self.assertEqual(
            servarr.field_values(servarr.with_fields(item, {"host": "b"})), {"apiKey": servarr.MASK, "host": "b"}
        )


class SeerrPermissionTests(unittest.TestCase):
    def test_administrators_approve_and_are_approved_everyone_else_asks(self):
        hooks = _module(ROOT / "apps/seerr/hooks.py")
        for bit in (hooks.REQUEST, hooks.CREATE_ISSUES):
            self.assertTrue(hooks.HOUSEHOLD & bit)
        for bit in (hooks.AUTO_APPROVE, hooks.MANAGE_REQUESTS):
            self.assertTrue(hooks.ADMINISTRATOR & bit)
            self.assertFalse(hooks.HOUSEHOLD & bit)
        # Seerr's own settings stay Mu3Lab's: nobody gets Seerr's admin or settings rights.
        for bits in (hooks.HOUSEHOLD, hooks.ADMINISTRATOR):
            self.assertFalse(bits & 2)
            self.assertFalse(bits & 4)


if __name__ == "__main__":
    unittest.main()
