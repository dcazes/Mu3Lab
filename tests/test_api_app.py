"""The control plane serves the dashboard bundle without shadowing the API."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from ctl.api import create_app


class DashboardServingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dist = Path(tmp.name) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "assets" / "app.js").write_text("console.log('mu3lab')", encoding="utf-8")
        (dist / "index.html").write_text('<script src="/assets/app.js"></script>', encoding="utf-8")
        (dist / "sw.js").write_text("self.addEventListener('fetch', () => {})", encoding="utf-8")
        (dist / "manifest.webmanifest").write_text('{"name": "Mu3Lab"}', encoding="utf-8")
        (dist.parent / "secret.txt").write_text("outside the bundle", encoding="utf-8")
        self.client = TestClient(create_app(dist))

    def test_client_routes_fall_back_to_the_dashboard(self):
        for path in ("/", "/apps/mealie", "/connections/providers"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("/assets/app.js", response.text)

    def test_assets_are_served(self):
        self.assertEqual(self.client.get("/assets/app.js").text, "console.log('mu3lab')")

    def test_pwa_files_are_served_with_their_types(self):
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.headers["content-type"], "application/manifest+json")
        worker = self.client.get("/sw.js")
        self.assertIn("javascript", worker.headers["content-type"])
        self.assertEqual(worker.headers["cache-control"], "no-cache")

    def test_files_outside_the_bundle_are_never_served(self):
        for path in ("/../secret.txt", "/%2e%2e/secret.txt"):
            with self.subTest(path=path):
                self.assertNotIn("outside the bundle", self.client.get(path).text)

    def test_unknown_api_paths_are_json_not_html(self):
        response = self.client.get("/api/v1/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["ok"], False)

    def test_health_is_available_without_identity(self):
        self.assertTrue(self.client.get("/api/health").json()["ok"])


if __name__ == "__main__":
    unittest.main()
