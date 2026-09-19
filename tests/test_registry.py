"""Registry invariants: only curated, internally safe Compose definitions load."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl.registry import RegistryError, load
from ctl.backups import Retention
from ctl.runtime import RuntimePaths
from ctl.service_state import public_url, status as service_status


class RegistryTests(unittest.TestCase):
    def test_checked_in_registry_marks_surfsense_planned_and_local_account(self):
        registry = load()
        self.assertEqual(registry.get("surfsense").availability, "blocked")
        self.assertEqual(registry.get("surfsense").auth, "local")
        self.assertIn("not true SSO", registry.get("surfsense").identity_note)
        self.assertFalse(registry.get("vaultwarden").mcp.get("exposed", False))

    def test_rejects_compose_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "services.yaml"
            path.write_text("""schema_version: 2\nservices:\n  - id: bad\n    maturity: supported\n    name: Bad\n    category: test\n    lifecycle: optional\n    compose_dir: ../outside\n    https_port: 1\n    health: {kind: tcp, port: 1}\n    auth: proxy\n    profiles: [cpu]\n""", encoding="utf-8")
            with self.assertRaisesRegex(RegistryError, "unsafe compose_dir"):
                load(path)

    def test_tailnet_url_never_falls_back_to_localhost(self):
        service = load().get("litellm")
        self.assertEqual(public_url(service, ""), "")
        self.assertEqual(public_url(service, "mu3lab.example.ts.net"), "")
        ingress = load().get("ingress")
        self.assertEqual(public_url(ingress, "mu3lab.example.ts.net"),
                         "https://mu3lab.example.ts.net:8446")

    def test_foundation_images_are_pinned_and_planned_services_are_not_routable(self):
        registry = load()
        self.assertTrue(registry.get("vaultwarden").images)
        self.assertFalse(registry.get("open-webui").route == "ready")

    def test_runtime_paths_are_outside_checkout(self):
        paths = RuntimePaths()
        self.assertEqual(paths.root, Path("/srv/mu3lab"))
        self.assertEqual(paths.backups, Path("/srv/mu3lab/backups"))

    def test_backup_policy_matches_the_product_default(self):
        self.assertEqual(Retention().restic_args(),
                         ["--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "12"])

    def test_core_compose_files_are_valid_and_pin_identity_images(self):
        root = Path(__file__).resolve().parents[1]
        for rel in ("core/authentik/docker-compose.yml", "core/vaultwarden/docker-compose.yml"):
            with self.subTest(rel=rel):
                compose = yaml.safe_load((root / rel).read_text(encoding="utf-8"))
                self.assertIn("services", compose)
        authentik = (root / "core/authentik/.env.example").read_text(encoding="utf-8")
        self.assertNotIn("AUTHENTIK_TAG=latest", authentik)

    def test_core_suite_has_a_compose_manifest_and_pinned_registry_images(self):
        registry = load()
        root = Path(__file__).resolve().parents[1]
        core = [service for service in registry.services if service.required and service.stage == "core"]
        self.assertEqual({service.id for service in core},
                         {"ollama", "freellmapi", "litellm", "open-webui"})
        for service in core:
            self.assertTrue((service.compose_path(root) / "docker-compose.yml").is_file(), service.id)
            self.assertTrue(service.images, service.id)
            self.assertTrue(all(":latest" not in image and ":main" not in image for image in service.images))

    def test_ingress_matches_tailnet_host_headers_on_loopback(self):
        caddyfile = (Path(__file__).resolve().parents[1] / "core/ingress/Caddyfile").read_text(
            encoding="utf-8"
        )
        self.assertIn(":19460 {", caddyfile)
        self.assertIn("bind 127.0.0.1", caddyfile)
        self.assertNotIn("http://127.0.0.1:19460 {", caddyfile)
        self.assertIn("X-Mu3Lab-Proxy-Token", caddyfile)

    def test_ingress_health_uses_dedicated_caddy_endpoint(self):
        self.assertTrue(load().get("ingress").health["url"].endswith("/__mu3lab_caddy_health"))

    def test_dashboard_catalog_uses_curated_service_ids(self):
        root = Path(__file__).resolve().parents[1]
        catalog = yaml.safe_load((root / "catalog.yaml").read_text(encoding="utf-8"))
        registry = load()
        profile_ids = {service_id for profile in catalog["profiles"]
                       for service_id in profile["services"]}
        self.assertTrue(profile_ids.issubset({service.id for service in registry.services}))

    def test_healthy_service_without_private_route_needs_setup(self):
        service = load().get("open-webui")
        root = Path(__file__).resolve().parents[1]
        with patch("ctl.service_state._compose_state", return_value="running"), \
             patch("ctl.service_state._healthy", return_value=(True, "HTTP 200")), \
             patch("ctl.service_state._tailnet_route_present", return_value=False):
            state = service_status(service, "", root)
        self.assertEqual(state["lifecycle_state"], "needs_setup")
        self.assertEqual(state["health_state"], "healthy")
        self.assertEqual(state["route_state"], "pending")
        self.assertEqual(state["user_action"], "Private HTTPS route pending")

    def test_healthy_internal_service_does_not_require_a_browser_route(self):
        service = load().get("litellm")
        root = Path(__file__).resolve().parents[1]
        with patch("ctl.service_state._compose_state", return_value="running"), \
             patch("ctl.service_state._healthy", return_value=(True, "HTTP 200")):
            state = service_status(service, "", root)
        self.assertEqual(state["lifecycle_state"], "ready")
        self.assertEqual(state["route_state"], "not_required")
        self.assertFalse(state["route_ready"])
