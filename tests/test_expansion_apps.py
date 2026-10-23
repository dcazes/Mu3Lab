"""App-specific setup for the 2026 expansion apps (the parts that run in Mu3Lab, not in containers)."""

from __future__ import annotations

import unittest
from pathlib import Path


from ctl.manifest.catalog import load

ROOT = Path(__file__).resolve().parents[1]


class ManifestContractTests(unittest.TestCase):
    def test_internal_services_have_no_screen_and_no_sign_in(self):
        catalog = load()
        for app_id in ("speaches",):
            with self.subTest(app=app_id):
                manifest = catalog.get(app_id).manifest
                self.assertIsNone(manifest.route)
                self.assertFalse(manifest.ui.available)
                self.assertEqual(manifest.sign_in.method, "none")

    def test_photon_comes_inside_dawarich_and_stays_out_of_its_backups(self):
        catalog = load()
        self.assertNotIn("photon", {app.id for app in catalog.apps})
        dawarich = catalog.get("dawarich").manifest
        compose = (ROOT / "apps" / "dawarich" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("PHOTON_API_HOST: photon:2322", compose)
        self.assertEqual(dawarich.backup.skip, ("dawarich-photon",))
        self.assertIn("photon", dawarich.ui.parts)

    def test_owner_accounts_are_created_at_install_not_on_first_sign_in(self):
        catalog = load()
        for app_id in ("outline", "dawarich", "open-webui"):
            with self.subTest(app=app_id):
                manifest = catalog.get(app_id).manifest
                assert manifest.sign_in.oidc is not None
                self.assertEqual(manifest.sign_in.oidc.initial_owner_env, "")
                scripts = [rule.with_ for rule in manifest.rules if rule.rule == "container_script"]
                self.assertEqual(scripts[0]["at"], "after_healthy")
                self.assertIn("{{owner_json}}", scripts[0]["args"])

    def test_app_scripts_report_with_their_markers(self):
        folder = Path(__file__).resolve().parents[1] / "apps"
        markers = {
            "beaver-habits/scripts/sync-people.py": "MU3LAB_BEAVER_PEOPLE_OK",
            "outline/scripts/adopt-owner.js": "MU3LAB_OUTLINE_OWNER_OK",
            "dawarich/scripts/adopt-owner.rb": "MU3LAB_DAWARICH_OWNER_OK",
            "open-webui/scripts/adopt-owner.py": "MU3LAB_OPEN_WEBUI_OWNER_OK",
        }
        for script, marker in markers.items():
            with self.subTest(script=script):
                self.assertIn(marker, (folder / script).read_text())
