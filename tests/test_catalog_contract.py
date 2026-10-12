"""The catalog refuses an app whose deployable contract does not add up (review R25).

Each test copies the real catalog, breaks one thing, and expects ``load`` to
name it, so a mistake is caught before anything is installed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from ctl.manifest import catalog

ROOT = Path(__file__).resolve().parents[1]


class CatalogContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copytree(ROOT / "apps", self.root / "apps", ignore=shutil.ignore_patterns("node_modules", ".env"))
        shutil.copytree(ROOT / "platform", self.root / "platform")
        self.apps = self.root / "apps"

    def edit(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def refused(self, message: str) -> None:
        with self.assertRaisesRegex(catalog.CatalogError, message):
            catalog.load(self.apps)

    def test_the_real_catalog_passes(self):
        self.assertGreater(len(catalog.load(self.apps).apps), 20)

    def test_a_malformed_or_missing_digest_is_refused(self):
        compose = "apps/speaches/docker-compose.yml"
        image = next(iter(catalog.compose_images(self.root / compose).values()))
        self.edit(compose, image, image[:-1])  # 63 hex characters
        self.refused("speaches.*sha256 digest")

    def test_a_connector_image_that_is_neither_built_nor_pinned_is_refused(self):
        compose = "apps/immich/connectors/immich-photo-manager/docker-compose.yml"
        image = next(iter(catalog.compose_images(self.root / compose).values()))
        self.edit(compose, image, image.split("@", 1)[0])
        self.refused("immich-photo-manager.*sha256 digest")

    def test_an_app_port_used_by_a_connector_is_refused(self):
        self.edit("apps/speaches/app.yaml", "local_port: 8092", "local_port: 8814")
        self.edit("apps/speaches/docker-compose.yml", "127.0.0.1:8092:", "127.0.0.1:8814:")
        self.refused("both use port 8814")

    def test_an_app_port_used_by_another_apps_caddy_listener_is_refused(self):
        self.edit("apps/speaches/app.yaml", "local_port: 8092", "local_port: 19467")
        self.edit("apps/speaches/docker-compose.yml", "127.0.0.1:8092:", "127.0.0.1:19467:")
        self.refused("both use port 19467")

    def test_the_dashboards_port_is_reserved(self):
        self.edit("apps/speaches/app.yaml", "local_port: 8092", "local_port: 8787")
        self.edit("apps/speaches/docker-compose.yml", "127.0.0.1:8092:", "127.0.0.1:8787:")
        self.refused("dashboard")

    def test_a_compose_port_that_differs_from_the_manifest_is_refused(self):
        self.edit("apps/speaches/docker-compose.yml", "127.0.0.1:8092:", "127.0.0.1:2323:")
        self.refused("publishes port 2323, but service.local_port is 8092")

    def test_a_port_open_beyond_loopback_is_refused(self):
        self.edit("apps/speaches/docker-compose.yml", "127.0.0.1:8092:8000", "8092:8000")
        self.refused("127.0.0.1 only")

    def test_a_connector_whose_port_differs_from_its_health_check_is_refused(self):
        self.edit("apps/mealie/connectors/mealie-community/docker-compose.yml", "127.0.0.1:8814:", "127.0.0.1:8899:")
        self.refused("mealie-community.*local_health uses port 8814")

    def test_a_changed_compose_file_is_seen_by_the_cached_catalog(self):
        self.assertTrue(catalog.cached(self.apps))
        self.edit("apps/speaches/docker-compose.yml", "127.0.0.1:8092:", "127.0.0.1:2323:")
        later = time.time() + 5
        os.utime(self.apps / "speaches/docker-compose.yml", (later, later))
        with self.assertRaises(catalog.CatalogError):
            catalog.cached(self.apps)


if __name__ == "__main__":
    unittest.main()
