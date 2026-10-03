"""The maintainer's approval moves only the app's own pins, in both files that hold them."""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.lifecycle import app_releases
from ctl.registry import load
from tools import approve_release

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "f" * 64


class PlanTests(unittest.TestCase):
    def plan(self, service_id: str, version: str, overrides=None) -> dict[str, str]:
        return approve_release.plan(load().get(service_id), version, overrides or {})

    def test_only_the_apps_own_images_move(self):
        self.assertEqual(
            self.plan("immich", "v3.3.0"),
            {
                "immich-server": "ghcr.io/immich-app/immich-server:v3.3.0",
                "immich-machine-learning": "ghcr.io/immich-app/immich-machine-learning:v3.3.0",
            },
        )

    def test_tag_prefixes_and_suffixes_are_kept(self):
        self.assertEqual(set(self.plan("nextcloud", "v34.0.1").values()), {"nextcloud:34.0.1-apache"})
        self.assertEqual(
            self.plan("baby-buddy", "v2.8.0"), {"baby-buddy": "lscr.io/linuxserver/babybuddy:version-v2.8.0"}
        )

    def test_apps_whose_tags_do_not_follow_releases_need_named_images(self):
        # As if Firecrawl were approved as v2.11.0 while its image is tagged 2.11.448-production.
        firecrawl = dataclasses.replace(
            load().get("firecrawl"), update={"repository": "firecrawl/firecrawl", "approved_version": "v2.11.0"}
        )
        self.assertEqual(approve_release.plan(firecrawl, "v2.12.0", {}), {})
        named = approve_release.plan(firecrawl, "v2.12.0", {"api": "ghcr.io/firecrawl/firecrawl:2.12.1-production"})
        self.assertEqual(named, {"api": "ghcr.io/firecrawl/firecrawl:2.12.1-production"})

    def test_a_moving_latest_tag_is_recorded_by_digest_alone(self):
        with patch.object(approve_release.actions, "docker_cmd", return_value=(0, f'{{"digest": "{DIGEST}"}}')):
            self.assertEqual(
                approve_release.resolve_digest("ghcr.io/firecrawl/nuq-postgres:latest"),
                f"ghcr.io/firecrawl/nuq-postgres@{DIGEST}",
            )
            self.assertEqual(approve_release.resolve_digest("nextcloud:35.0.1"), f"nextcloud:35.0.1@{DIGEST}")

    def test_a_misnamed_service_is_refused(self):
        with self.assertRaises(SystemExit):
            self.plan("mealie", "v3.23.0", {"not-a-service": "x:1"})


class RewriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "apps", self.root / "apps")
        patcher = patch.object(approve_release, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def test_the_compose_file_and_manifest_version_move_together(self):
        service = load(self.root / "apps").get("surfsense")
        new_backend = f"ghcr.io/modsetter/surfsense-backend:0.0.41@{DIGEST}"
        new_web = f"ghcr.io/modsetter/surfsense-web:0.0.41@{DIGEST}"
        approve_release.rewrite(service, "v0.0.41", {"backend": new_backend, "frontend": new_web})
        registry = load(self.root / "apps")
        updated = registry.get("surfsense")
        self.assertEqual(updated.update["approved_version"], "v0.0.41")
        release = app_releases.approved(updated, self.root)
        self.assertEqual(release.images["backend"], new_backend)
        self.assertEqual(release.images["frontend"], new_web)
        # Every backend container uses the one image, so all of them move.
        self.assertEqual(
            {release.images[name] for name in ("migrations", "celery_worker", "celery_beat")}, {new_backend}
        )
        # Databases keep their pins, and so does every other app.
        self.assertTrue(release.images["db"].startswith("pgvector/pgvector:pg17@"))
        for other in registry.services:
            if other.id != "surfsense":
                self.assertEqual(other.update, load().get(other.id).update, other.id)


if __name__ == "__main__":
    unittest.main()
