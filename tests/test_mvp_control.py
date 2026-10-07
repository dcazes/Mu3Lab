"""MVP contracts for global configuration, app installation, and MCP scope."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ctl.control_state import ControlState
from ctl.mcp_catalog import load as load_mcp_catalog
from ctl.registry import load
from ctl.routes import base_matches, rebase, render
from ctl.service_ops import allowed_actions

ROOT = Path(__file__).resolve().parents[1]


class ControlStateTests(unittest.TestCase):
    def test_compute_mode_and_installation_survive_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "control.sqlite3"
            state = ControlState(database)
            state.set_compute_mode("nvidia", "owner")
            state.set_installation(
                "mealie",
                "running",
                job_id="job-1",
                manifest_version="3",
                image_digests={"mealie": "sha256:abc"},
                route_state="ready",
            )
            reopened = ControlState(database)
            self.assertEqual(reopened.system_config()["compute_mode"], "nvidia")
            self.assertEqual(reopened.installation("mealie")["image_digests"]["mealie"], "sha256:abc")

    def test_invalid_compute_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "compute_mode"):
            ControlState(Path(tmp) / "control.sqlite3").set_compute_mode("per-app", "owner")


class RegistryV3Tests(unittest.TestCase):
    def test_supported_optional_apps_have_curated_compose_contracts(self):
        registry = load()
        optional = [service for service in registry.services if service.stage == "optional"]
        self.assertEqual(
            {service.id for service in optional},
            {
                "surfsense",
                "mealie",
                "actual-budget",
                "immich",
                "adventurelog",
                "paperless-ngx",
                "nextcloud",
                "baby-buddy",
                "grocy",
                "beaver-habits",
                "outline",
                "audiobookshelf",
                "romm",
                "photon",
                "dawarich",
                "open-webui",
                "speaches",
            },
        )
        for service in optional:
            with self.subTest(service=service.id):
                self.assertTrue((service.compose_path(ROOT) / "docker-compose.yml").is_file())
                # Apps people open get a private address; internal services (speech, maps) have none.
                routed = service.manifest.ui.available
                self.assertEqual(service.private_https_port is not None, routed)
                self.assertEqual(service.proxy_port is not None, routed)

    def test_install_is_the_only_initial_optional_action(self):
        self.assertEqual(allowed_actions(load().get("mealie"), "not_installed"), ["install"])

    def test_mcp_catalog_never_contains_vaultwarden_or_infrastructure(self):
        registry = load()
        servers = load_mcp_catalog(registry)
        excluded = {"vaultwarden", "ingress", "authentik", "ollama", "litellm", "lobehub"}
        self.assertFalse(excluded.intersection(server.service_id for server in servers))
        preferred: dict[str, int] = {}
        for server in servers:
            preferred[server.service_id] = preferred.get(server.service_id, 0) + int(server.preferred)
        self.assertTrue(all(count <= 1 for count in preferred.values()))

    def test_generated_route_section_is_idempotent(self):
        service = load().get("mealie")
        base = "{\n  admin off\n}\n"
        first = render(base, [service])
        second = render(first, [service])
        self.assertEqual(first, second)
        self.assertEqual(second.count(":19467 {"), 1)

    def test_optional_route_regeneration_preserves_core_ui_routes(self):
        base = (ROOT / "apps/ingress/Caddyfile.authenticated").read_text(encoding="utf-8")
        rendered = render(base, [load().get(app_id) for app_id in ("mealie", "litellm", "freellmapi")])
        self.assertIn(":19471 {", rendered)
        self.assertIn(":19472 {", rendered)
        self.assertIn(":19467 {", rendered)

    def test_an_updated_base_keeps_the_deployed_app_routes(self):
        deployed = render("{\n  admin off\n}\n", [load().get("mealie")])
        new_base = "{\n  admin off\n  auto_https off\n}\n"
        self.assertFalse(base_matches(new_base, deployed))
        updated = rebase(new_base, deployed)
        self.assertTrue(base_matches(new_base, updated))
        self.assertIn("auto_https off", updated)
        self.assertEqual(updated.count(":19467 {"), 1)
        self.assertEqual(rebase(new_base, updated), updated)

    def test_dashboard_sign_in_returns_to_the_dashboard_port(self):
        caddy = (ROOT / "apps/ingress/Caddyfile.authenticated").read_text(encoding="utf-8")
        dashboard = caddy.split(":19460 {", 1)[1].split(":19461 {", 1)[0]
        self.assertIn("header_up Host {http.request.hostport}", dashboard)
        self.assertIn("header_up X-Forwarded-Host {http.request.hostport}", dashboard)

    def test_core_route_pending_offers_restart_not_retry_install(self):
        self.assertEqual(allowed_actions(load().get("litellm"), "needs_setup"), ["restart"])

    def test_litellm_forward_auth_preserves_its_private_port(self):
        caddy = (ROOT / "apps/ingress/Caddyfile.authenticated").read_text(encoding="utf-8")
        route = render(caddy, [load().get("litellm")]).split(":19471 {", 1)[1]
        self.assertIn("header_up Host {http.request.hostport}", route)
        self.assertIn("header_up X-Forwarded-Host {http.request.hostport}", route)

    def test_surfsense_route_is_authentik_gated_before_local_login(self):
        block = render("{\n  admin off\n}\n", [load().get("surfsense")])
        self.assertIn(":19464 {", block)
        self.assertIn("forward_auth 127.0.0.1:9001", block)
        self.assertIn("reverse_proxy 127.0.0.1:3929", block)

    def test_firecrawl_route_is_private_without_authentik_gate(self):
        block = render("{\n  admin off\n}\n", [load().get("firecrawl")])
        self.assertIn(":19473 {", block)
        self.assertIn("reverse_proxy 127.0.0.1:3002", block)
        self.assertNotIn("forward_auth", block)

    def test_baby_buddy_route_uses_authentik_verified_remote_user(self):
        block = render("{\n  admin off\n}\n", [load().get("baby-buddy")])
        self.assertIn(":19475 {", block)
        self.assertIn("forward_auth 127.0.0.1:9001", block)
        self.assertIn("copy_headers X-Authentik-Username", block)
        self.assertIn("header_up Remote-User {http.request.header.X-Authentik-Username}", block)
        # Caddy runs deletes after sets, so a delete here would drop the identity.
        gated = block.split("forward_auth", 1)[1]
        self.assertNotIn("header_up -Remote-User", gated)
        self.assertIn("reverse_proxy 127.0.0.1:8002", block)

    def test_authentik_embedded_oidc_is_limited_to_the_dashboard_origin(self):
        caddy = (ROOT / "apps/ingress/Caddyfile.authenticated").read_text(encoding="utf-8")
        self.assertIn("@embedded_oidc path /application/o/authorize/* /if/flow/*", caddy)
        self.assertIn("handle @embedded_oidc", caddy)
        self.assertIn("header_down -X-Frame-Options", caddy)
        self.assertIn("frame-ancestors 'self' https://{http.request.host}:8446", caddy)
        embedded = caddy.split("handle @embedded_oidc", 1)[1].split("\n\thandle {", 1)[0]
        fallback = caddy.split("handle @embedded_oidc", 1)[1].split("\n\thandle {", 1)[1]
        self.assertIn("header_down -X-Frame-Options", embedded)
        self.assertNotIn("header_down -X-Frame-Options", fallback.split("\n}", 1)[0])
        self.assertNotIn("Access-Control-Allow-Origin", embedded)


class InstallationWorkflowTests(unittest.TestCase):
    def test_surfsense_install_allows_bounded_migration_and_zero_cache_startup(self):
        compose = (ROOT / "apps/surfsense/docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("condition: service_completed_successfully", compose)
        self.assertIn('SANDBOX_ENABLED: "FALSE"', compose)
        self.assertNotIn("docker.sock", compose)


if __name__ == "__main__":
    unittest.main()
