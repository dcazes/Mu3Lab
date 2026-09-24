"""MVP contracts for global configuration, app installation, and MCP scope."""

from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.mcp_catalog import load as load_mcp_catalog
from ctl.registry import load
from ctl.routes import render
from ctl.runtime import RuntimePaths
from ctl.service_ops import _configure_nextcloud, allowed_actions, execute_claimed


ROOT = Path(__file__).resolve().parents[1]


class ControlStateTests(unittest.TestCase):
    def test_compute_mode_and_installation_survive_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "control.sqlite3"
            state = ControlState(database)
            state.set_compute_mode("nvidia", "owner")
            state.set_installation("mealie", "running", job_id="job-1",
                                   manifest_version="3", image_digests={"mealie": "sha256:abc"},
                                   route_state="ready")
            reopened = ControlState(database)
            self.assertEqual(reopened.system_config()["compute_mode"], "nvidia")
            self.assertEqual(reopened.installation("mealie")["image_digests"]["mealie"],
                             "sha256:abc")

    def test_invalid_compute_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "compute_mode"):
                ControlState(Path(tmp) / "control.sqlite3").set_compute_mode("per-app", "owner")


class RegistryV3Tests(unittest.TestCase):
    def test_supported_optional_apps_have_curated_compose_contracts(self):
        registry = load()
        optional = [service for service in registry.services if service.stage == "optional"]
        self.assertEqual({service.id for service in optional},
                         {"surfsense", "mealie", "actual-budget", "immich", "adventurelog",
                          "paperless-ngx", "nextcloud", "firecrawl", "baby-buddy"})
        for service in optional:
            with self.subTest(service=service.id):
                self.assertTrue((service.compose_path(ROOT) / "docker-compose.yml").is_file())
                self.assertIsNotNone(service.private_https_port)
                self.assertIsNotNone(service.proxy_port)
                self.assertNotIn("install", service.profiles)

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
        base = (ROOT / "core/ingress/Caddyfile.authenticated").read_text(encoding="utf-8")
        rendered = render(base, [load().get("mealie")])
        self.assertIn(":19471 {", rendered)
        self.assertIn(":19472 {", rendered)
        self.assertIn(":19467 {", rendered)

    def test_core_route_pending_offers_repair_not_retry_install(self):
        self.assertEqual(allowed_actions(load().get("litellm"), "needs_setup"),
                         ["repair", "restart"])

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
        self.assertIn("header_up -Remote-User", block)
        self.assertIn("header_up Remote-User {http.request.header.X-Authentik-Username}", block)
        self.assertIn("reverse_proxy 127.0.0.1:8002", block)

    def test_authentik_embedded_oidc_is_limited_to_the_dashboard_origin(self):
        caddy = (ROOT / "core/ingress/Caddyfile.authenticated").read_text(encoding="utf-8")
        self.assertIn(
            "@embedded_oidc path /application/o/authorize/* /if/flow/*", caddy)
        self.assertIn("handle @embedded_oidc", caddy)
        self.assertIn("header_down -X-Frame-Options", caddy)
        self.assertIn(
            "frame-ancestors 'self' https://{http.request.host}:8446", caddy)
        embedded = caddy.split("handle @embedded_oidc", 1)[1].split("\n\thandle {", 1)[0]
        fallback = caddy.split("handle @embedded_oidc", 1)[1].split("\n\thandle {", 1)[1]
        self.assertIn("header_down -X-Frame-Options", embedded)
        self.assertNotIn("header_down -X-Frame-Options", fallback.split("\n}", 1)[0])
        self.assertNotIn("Access-Control-Allow-Origin", embedded)

class InstallationWorkflowTests(unittest.TestCase):
    def test_nextcloud_accepts_newer_compatible_app_store_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "nextcloud"
            project.mkdir()
            app_list = {"enabled": {"calendar": "6.6.1", "user_oidc": "8.11.0"}}
            outputs = [
                (0, "already installed"), (0, "enabled"),
                (0, "already installed"), (0, "enabled"),
                (0, json.dumps(app_list)), (0, "provider configured"),
                (0, "mu3lab clientid=mu3lab-nextcloud"),
                (0, "auto_provision enabled"),
                (0, "soft_auto_provision enabled"),
                (0, "Config value allow_multiple_user_backends set to 1"),
                (0, "Config value allow_local_remote_servers set to 1"),
            ]
            with patch("ctl.service_ops.read_runtime_env", return_value={
                "NEXTCLOUD_OIDC_CLIENT_ID": "mu3lab-nextcloud",
                "NEXTCLOUD_OIDC_CLIENT_SECRET": "secret",
                "NEXTCLOUD_OVERWRITEHOST": "mu3lab.example.ts.net:8453",
            }), patch("ctl.service_ops.actions.compose_exec", side_effect=outputs):
                ok, detail = _configure_nextcloud(project, lambda _line: None)
            self.assertTrue(ok)
            self.assertIn("calendar 6.6.1", detail)

    def test_nextcloud_bootstrap_does_not_wait_on_uninstalled_healthcheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime-root")
            paths.projects.mkdir(parents=True)
            paths.data.mkdir(parents=True)
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            store = JobStore(paths.runtime / "control-plane.sqlite3")
            created = store.create(kind="lifecycle", service_id="nextcloud",
                                   action="install", actor="owner")
            claimed = store.claim("worker")
            identity = {"owner_uid": "owner", "email": "owner@example.test",
                        "username": "owner", "display_name": "Owner"}
            with patch("ctl.service_ops.RuntimePaths", return_value=paths), \
                 patch("ctl.service_ops.ControlState.runtime", return_value=state), \
                 patch("ctl.service_ops.workflow_secrets.job_identity", return_value=identity), \
                 patch("ctl.service_ops.workflow_secrets.create_handoff", return_value={
                     "id": "handoff", "created_at": "now", "expires_at": "later"}), \
                 patch("ctl.service_ops.actions.compose_config", return_value=(0, "")), \
                 patch("ctl.service_ops.actions.compose_pull", return_value=(0, "pulled")), \
                 patch("ctl.service_ops.actions.docker_image_digest",
                       return_value=(0, "example@sha256:abc")), \
                 patch("ctl.service_ops.actions.compose_up",
                       side_effect=[(0, "started"), (0, "recreated")]) as up, \
                 patch("ctl.service_ops.actions.compose_exec", return_value=(0, "installed")), \
                 patch("ctl.service_ops._nextcloud_installed",
                       side_effect=[False, False, True]), \
                 patch("ctl.service_ops._verify_bootstrap_account", return_value=(True, "ok")), \
                 patch("ctl.service_ops._wait_healthy", return_value=(True, "HTTP 200")), \
                 patch("ctl.service_ops._configure_nextcloud", return_value=(True, "configured")), \
                 patch("ctl.service_ops.apply_route", return_value=(True, "ready")), \
                 patch("ctl.service_ops.workflow_secrets.save_job_identity"):
                execute_claimed(store, claimed, "worker", ROOT)
            final = store.get(created["id"])
            self.assertEqual(final["state"], "succeeded")
            self.assertIsNone(up.call_args_list[0].kwargs["wait_timeout"])

    def test_optional_install_executes_the_bounded_stage_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime-root")
            paths.projects.mkdir(parents=True)
            paths.data.mkdir(parents=True)
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            store = JobStore(paths.runtime / "control-plane.sqlite3")
            created = store.create(kind="lifecycle", service_id="mealie",
                                   action="install", actor="owner")
            claimed = store.claim("worker")
            self.assertIsNotNone(claimed)
            with patch("ctl.service_ops.RuntimePaths", return_value=paths), \
                 patch("ctl.service_ops.ControlState.runtime", return_value=state), \
                 patch("ctl.service_ops.actions.compose_config", return_value=(0, "")), \
                 patch("ctl.service_ops.actions.compose_pull", return_value=(0, "pulled")), \
                 patch("ctl.service_ops.actions.docker_image_digest",
                       return_value=(0, 'ghcr.io/mealie-recipes/mealie@sha256:abc')), \
                patch("ctl.service_ops.actions.compose_up", return_value=(0, "started")), \
                 patch("ctl.service_ops._wait_healthy", return_value=(True, "HTTP 200")), \
                 patch("ctl.service_ops.apply_route", return_value=(True, "ready")), \
                 patch("ctl.service_state.tailnet_dns_name", return_value=""):
                execute_claimed(store, claimed, "worker", ROOT)
            final = next(job for job in store.jobs() if job["id"] == created["id"])
            self.assertEqual(final["state"], "succeeded")
            self.assertEqual(state.installation("mealie")["state"], "running")
            stages = "\n".join(event["detail"] for event in store.events(created["id"]))
            for stage in ("validate_service", "materialize_runtime", "pull_images", "resolve_digests",
                          "start_service", "verify_application", "configure_route", "finalize"):
                self.assertIn(stage, stages)

    def test_surfsense_install_allows_bounded_migration_and_zero_cache_startup(self):
        compose = (ROOT / "apps/surfsense/docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("condition: service_completed_successfully", compose)
        self.assertIn('SANDBOX_ENABLED: "FALSE"', compose)
        self.assertNotIn("docker.sock", compose)


if __name__ == "__main__":
    unittest.main()
