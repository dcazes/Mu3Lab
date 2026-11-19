"""Prowlarr, Sonarr, Radarr and Seerr: who may reach whom, what qBittorrent leaves to them, and the quality rules."""

from __future__ import annotations

import importlib.util
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
    def test_only_the_download_managers_join_qbittorrents_own_network(self):
        # qBittorrent skips its login on its own network, so only the apps that manage its
        # downloads may join it: Sonarr, Radarr and Cleanuparr.
        joined = sorted(
            compose.parent.name
            for compose in (ROOT / "apps").glob("*/docker-compose.yml")
            if QBITTORRENT_NETWORK in compose.read_text(encoding="utf-8")
        )
        self.assertEqual(joined, ["cleanuparr", "radarr", "sonarr"])

    def test_the_media_apps_meet_on_an_internal_network(self):
        self.assertIn("mu3lab_media", preflight.MU3LAB_NETWORKS)
        self.assertIn("mu3lab_media", preflight.INTERNAL_NETWORKS)
        for app in ("prowlarr", "sonarr", "radarr", "seerr", "cleanuparr"):
            with self.subTest(app=app):
                self.assertTrue(_networks(app)["mu3lab_media"]["external"])

    def test_cleanuparr_listens_on_loopback_only(self):
        # It skips its own login for local addresses: the home network must never reach it.
        service = yaml.safe_load((ROOT / "apps/cleanuparr/docker-compose.yml").read_text(encoding="utf-8"))
        self.assertEqual(service["services"]["cleanuparr"]["ports"], ["127.0.0.1:11011:11011"])

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


class _Loader(yaml.SafeLoader):
    """Reads Recyclarr's files, where ``!env_var NAME`` stands for a secret from the environment."""


_Loader.add_constructor("!env_var", lambda loader, node: f"env:{loader.construct_scalar(node)}")


def _recyclarr(app: str) -> dict:
    document = yaml.load((ROOT / "apps" / app / "recyclarr.yml").read_text(encoding="utf-8"), Loader=_Loader)
    return document[app]["mu3lab"]


class QualityTests(unittest.TestCase):
    def test_the_profile_is_the_owners_choice(self):
        for app in ("sonarr", "radarr"):
            with self.subTest(app=app):
                (profile,) = _recyclarr(app)["quality_profiles"]
                # Seerr finds the profile by this name.
                self.assertEqual(profile["name"], "Mu3Lab")
                self.assertEqual([tier["name"] for tier in profile["qualities"]], ["4K", "1080p", "720p", "DVD"])
                self.assertFalse(profile["upgrade"]["allowed"], "the first good copy is kept")
                self.assertEqual(profile["min_format_score"], 0)
                for name in (name for tier in profile["qualities"] for name in tier["qualities"]):
                    self.assertNotRegex(name, "(?i)cam|telesync|remux|br-disk|raw")

    def test_unwanted_releases_score_below_the_minimum(self):
        for app in ("sonarr", "radarr"):
            with self.subTest(app=app):
                (rule,) = _recyclarr(app)["custom_formats"]
                self.assertEqual(len(rule["trash_ids"]), len(set(rule["trash_ids"])))
                self.assertEqual(rule["assign_scores_to"], [{"name": "Mu3Lab", "score": -10000}])

    def test_film_preferred_sizes_follow_the_tiers(self):
        sizes = {item["name"]: item["preferred"] for item in _recyclarr("radarr")["quality_definition"]["qualities"]}
        self.assertEqual({sizes[name] for name in sizes if "2160p" in name}, {170})
        self.assertEqual({sizes[name] for name in sizes if "1080p" in name}, {85})
        self.assertEqual({sizes[name] for name in sizes if "720p" in name}, {42})

    def test_tv_is_capped_at_about_35_gb_a_season(self):
        rules = _recyclarr("sonarr")
        sizes = rules["quality_definition"]["qualities"]
        allowed = {name for tier in rules["quality_profiles"][0]["qualities"] for name in tier["qualities"]}
        capped = {item["name"] for item in sizes}
        # Every allowed quality but the DVD tier (no TRaSH sizes, and small anyway) is capped.
        self.assertEqual(allowed - capped, {"DVD", "Bluray-576p", "Bluray-480p"})
        for item in sizes:
            self.assertEqual(item["max"], 65)
            self.assertLessEqual(item["preferred"], item["max"])
        # TRaSH's minimum for 4K Blu-ray copies (95 MB a minute) is above the cap.
        self.assertNotIn("Bluray-2160p", allowed)

    def test_trash_guides_are_pinned_to_one_commit(self):
        for app in ("sonarr", "radarr"):
            with self.subTest(app=app):
                settings = yaml.safe_load((ROOT / "apps" / app / "recyclarr-settings.yml").read_text(encoding="utf-8"))
                (provider,) = settings["resource_providers"]
                self.assertRegex(provider["reference"], "^[0-9a-f]{40}$")
                self.assertTrue(provider["replace_default"])

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
