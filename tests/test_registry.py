"""Registry invariants: only curated, internally safe Compose definitions load."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl.registry import RegistryError, load
from ctl.backups import Retention
from ctl.runtime import RuntimePaths
from ctl.service_state import _compose_state, public_url, status as service_status


class RegistryTests(unittest.TestCase):
    def test_successful_one_shot_migration_does_not_make_app_look_stopped(self):
        def fake_run(*_args, **_kwargs):
            return SimpleNamespace(returncode=0, stdout=(
                '{"State":"running","Status":"Up 10 minutes (healthy)"}\n'
                '{"State":"exited","Status":"Exited (0) 10 minutes ago"}\n'))

        self.assertEqual(_compose_state(Path('/srv/mu3lab/projects/surfsense/docker-compose.yml'), fake_run), 'running')

    def test_checked_in_registry_marks_surfsense_installable_and_local_account(self):
        registry = load()
        self.assertEqual(registry.get("surfsense").availability, "available")
        self.assertEqual(registry.get("surfsense").stage, "optional")
        self.assertEqual(registry.get("surfsense").auth, "local")
        self.assertIn("not SSO", registry.get("surfsense").identity_note)
        compose = registry.get("surfsense").compose_path(Path(__file__).resolve().parents[1]) / "docker-compose.yml"
        compose_text = compose.read_text(encoding="utf-8")
        self.assertNotIn("docker.sock", compose_text)
        image_lines = [line.strip() for line in compose_text.splitlines() if line.strip().startswith("image:")]
        self.assertTrue(image_lines)
        self.assertTrue(all("@sha256:" in line for line in image_lines))
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
        self.assertEqual(public_url(ingress, "mu3lab.example.ts.net"), "")

    def test_browser_ui_contract_distinguishes_services_from_internal_apis(self):
        registry = load()
        self.assertFalse(registry.get("ingress").ui["available"])
        self.assertFalse(registry.get("ollama").ui["available"])
        self.assertTrue(registry.get("authentik").ui["available"])
        self.assertTrue(registry.get("litellm").ui["available"])
        self.assertTrue(registry.get("freellmapi").ui["available"])
        self.assertTrue(registry.get("nextcloud").ui["available"])

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

    def test_nextcloud_is_curated_productivity_with_unique_private_ports(self):
        registry = load()
        service = registry.get("nextcloud")
        self.assertEqual(service.stage, "optional")
        self.assertEqual(service.category, "productivity")
        self.assertEqual(service.private_https_port, 8453)
        self.assertEqual(service.proxy_port, 19470)
        self.assertEqual(service.account["mode"], "environment_bootstrap")
        ports = [item.private_https_port for item in registry.services if item.private_https_port]
        proxies = [item.proxy_port for item in registry.services if item.proxy_port]
        self.assertEqual(len(ports), len(set(ports)))
        self.assertEqual(len(proxies), len(set(proxies)))

    def test_nextcloud_materialization_generates_private_runtime_secrets_and_oidc(self):
        from ctl.secrets import read_runtime_env
        from ctl.service_ops import _fresh_account_storage, _materialize
        root = Path(__file__).resolve().parents[1]
        service = load().get("nextcloud")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            with patch("ctl.service_ops.RuntimePaths", return_value=paths), \
                 patch("ctl.service_state.tailnet_dns_name", return_value="mu3lab.example.ts.net"):
                project = _materialize(service, root)
                self.assertTrue(_fresh_account_storage("nextcloud"))
            values = read_runtime_env(project / ".env")
            self.assertEqual(values["NEXTCLOUD_OIDC_CLIENT_ID"], "mu3lab-nextcloud")
            self.assertTrue(values["NEXTCLOUD_DB_PASSWORD"])
            self.assertTrue(values["NEXTCLOUD_REDIS_PASSWORD"])
            self.assertEqual((project / ".env").stat().st_mode & 0o777, 0o600)
            blueprint = paths.projects / "authentik" / "blueprints" / "mu3lab-nextcloud.yaml"
            self.assertIn("/apps/user_oidc/code", blueprint.read_text(encoding="utf-8"))
            # A config.php is not proof of installation: Nextcloud writes it
            # before committing the database. The installer must retry safely.
            config = paths.data / "nextcloud" / "html" / "config" / "config.php"
            config.parent.mkdir(parents=True)
            config.write_text("<?php", encoding="utf-8")
            with patch("ctl.service_ops.RuntimePaths", return_value=paths):
                self.assertTrue(_fresh_account_storage("nextcloud"))

    def test_multi_container_apps_keep_generic_backing_hostnames_private(self):
        root = Path(__file__).resolve().parents[1]
        generic_aliases: set[str] = set()
        private_networks = {"adventurelog": "adventurelog_internal", "paperless-ngx": "paperless_internal",
                            "nextcloud": "nextcloud_internal", "surfsense": "surfsense_internal",
                            "immich": "immich_internal"}
        for service_id, private_network in private_networks.items():
            compose = yaml.safe_load((root / load().get(service_id).compose_dir / "docker-compose.yml").read_text(encoding="utf-8"))
            self.assertIn(private_network, compose["networks"], service_id)
            for _name, contract in compose["services"].items():
                networks = contract.get("networks", [])
                backend = networks.get("mu3lab_backend", {}) if isinstance(networks, dict) else {}
                generic_aliases.update(backend.get("aliases", []) if isinstance(backend, dict) else [])
        self.assertFalse({"db", "redis", "broker", "app"}.intersection(generic_aliases))

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

    def test_healthy_litellm_dashboard_requires_its_private_route(self):
        service = load().get("litellm")
        root = Path(__file__).resolve().parents[1]
        with patch("ctl.service_state._compose_state", return_value="running"), \
             patch("ctl.service_state._healthy", return_value=(True, "HTTP 200")), \
             patch("ctl.service_state._tailnet_route_present", return_value=False):
            state = service_status(service, "", root)
        self.assertEqual(state["lifecycle_state"], "needs_setup")
        self.assertEqual(state["route_state"], "pending")
        self.assertFalse(state["route_ready"])
