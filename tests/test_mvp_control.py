"""MVP contracts for global configuration, app installation, and MCP scope."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.lifecycle.nextcloud import configure_nextcloud, install_nextcloud_if_needed
from ctl.mcp_catalog import load as load_mcp_catalog
from ctl.registry import load
from ctl.routes import base_matches, rebase, render
from ctl.runtime import RuntimePaths
from ctl.service_ops import allowed_actions, execute_claimed
from tests.support import runtime_paths

ROOT = Path(__file__).resolve().parents[1]


def _is_lock_check(command) -> bool:
    return "flock" in " ".join(command)


def _nextcloud_exec_without_lock(_project, _service, command, _log, timeout=0):
    """Container exec where the entrypoint has already released its init lock."""
    return (0, "") if _is_lock_check(command) else (0, "installed")


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
                "firecrawl",
                "baby-buddy",
            },
        )
        for service in optional:
            with self.subTest(service=service.id):
                self.assertTrue((service.compose_path(ROOT) / "docker-compose.yml").is_file())
                self.assertIsNotNone(service.private_https_port)
                self.assertIsNotNone(service.proxy_port)

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
        rendered = render(base, [load().get("mealie")])
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

    def test_core_route_pending_offers_repair_not_retry_install(self):
        self.assertEqual(allowed_actions(load().get("litellm"), "needs_setup"), ["repair", "restart"])

    def test_litellm_forward_auth_preserves_its_private_port(self):
        caddy = (ROOT / "apps/ingress/Caddyfile.authenticated").read_text(encoding="utf-8")
        route = caddy.split(":19471 {", 1)[1]
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
    def test_nextcloud_accepts_newer_compatible_app_store_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "nextcloud"
            project.mkdir()
            app_list = {"enabled": {"calendar": "6.6.1", "user_oidc": "8.11.0"}}
            outputs = [
                (0, "already installed"),
                (0, "enabled"),
                (0, "already installed"),
                (0, "enabled"),
                (0, json.dumps(app_list)),
                (0, "provider configured"),
                (0, "mu3lab clientid=mu3lab-nextcloud"),
                (0, json.dumps({"id": 7, "identifier": "mu3lab"})),
                (0, "auto_provision enabled"),
                (0, "soft_auto_provision enabled"),
                (0, "Config value allow_multiple_user_backends set to 0"),
                (0, "Config value allow_local_remote_servers set to 1"),
            ]
            with (
                patch(
                    "ctl.lifecycle.nextcloud.read_runtime_env",
                    return_value={
                        "NEXTCLOUD_OIDC_CLIENT_ID": "mu3lab-nextcloud",
                        "NEXTCLOUD_OIDC_CLIENT_SECRET": "secret",
                        "NEXTCLOUD_OVERWRITEHOST": "mu3lab.example.ts.net:8453",
                    },
                ),
                patch("ctl.service_ops.actions.compose_exec", side_effect=outputs),
            ):
                ok, detail = configure_nextcloud(project, lambda _line: None)
            self.assertTrue(ok)
            self.assertIn("calendar 6.6.1", detail)

    def test_nextcloud_bootstrap_does_not_wait_on_uninstalled_healthcheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime-root")
            paths.projects.mkdir(parents=True)
            paths.data.mkdir(parents=True)
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            store = JobStore(paths.runtime / "control-plane.sqlite3")
            created = store.create(kind="lifecycle", service_id="nextcloud", action="install", actor="owner")
            claimed = store.claim("worker")
            identity = {
                "owner_uid": "owner",
                "email": "owner@example.test",
                "username": "owner",
                "display_name": "Owner",
            }
            with (
                runtime_paths(paths),
                patch("ctl.service_ops.ControlState.runtime", return_value=state),
                patch("ctl.service_ops.workflow_secrets.job_identity", return_value=identity),
                patch(
                    "ctl.service_ops.workflow_secrets.create_handoff",
                    return_value={"id": "handoff", "created_at": "now", "expires_at": "later"},
                ),
                patch("ctl.service_ops.actions.compose_config", return_value=(0, "")),
                patch("ctl.service_ops.actions.compose_image_list", return_value=(1, [])),
                patch("ctl.service_ops.actions.compose_pull", return_value=(0, "pulled")),
                patch("ctl.service_ops.actions.docker_image_digest", return_value=(0, "example@sha256:abc")),
                patch("ctl.service_ops.actions.compose_up", side_effect=[(0, "started"), (0, "recreated")]) as up,
                patch("ctl.service_ops.actions.compose_exec", side_effect=_nextcloud_exec_without_lock),
                patch("ctl.lifecycle.nextcloud.nextcloud_installed", side_effect=[False, False, False, True]),
                patch("ctl.service_ops.verify_bootstrap_account", return_value=(True, "ok")),
                patch("ctl.service_ops.wait_healthy", return_value=(True, "HTTP 200")),
                patch("ctl.service_ops.configure_nextcloud", return_value=(True, "configured")),
                patch("ctl.service_ops.apply_route", return_value=(True, "ready")),
                patch("ctl.service_ops.wait_for_provider", return_value={}),
                patch("ctl.service_ops.verify_sign_in", return_value="Sign-in through Authentik verified."),
                patch("ctl.service_ops.workflow_secrets.save_job_identity"),
            ):
                execute_claimed(store, claimed, "worker", ROOT)
            final = store.get(created["id"])
            self.assertEqual(final["state"], "succeeded")
            self.assertIsNone(up.call_args_list[0].kwargs["wait_timeout"])

    def test_nextcloud_waits_for_the_entrypoint_install_instead_of_racing_it(self):
        # The image installs itself under a lock; a second install racing it
        # fails with "permission denied for table oc_migrations".
        commands: list[list[str]] = []
        lock_checks = iter([1, 1, 0])  # flock -n fails while the entrypoint holds the lock

        def compose_exec(_project, _service, command, _log, timeout=0):
            commands.append(command)
            if _is_lock_check(command):
                return next(lock_checks), ""
            return 0, "{}"

        with (
            patch("ctl.lifecycle.nextcloud.actions.compose_exec", side_effect=compose_exec),
            patch("ctl.lifecycle.nextcloud.nextcloud_installed", side_effect=[False, True]),
            patch("ctl.lifecycle.nextcloud.time.sleep"),
        ):
            installed, _detail = install_nextcloud_if_needed(Path("/unused"), lambda _line: None)
        self.assertTrue(installed)
        self.assertEqual(sum(_is_lock_check(command) for command in commands), 3)
        self.assertFalse(any("maintenance:install" in " ".join(command) for command in commands))

    def test_a_leftover_lock_file_does_not_hold_the_install_up(self):
        # The entrypoint never deletes its lock file; only a held lock means busy.
        seen: list[list[str]] = []

        def compose_exec(_project, _service, command, _log, timeout=0):
            seen.append(command)
            return 0, "{}"

        with (
            patch("ctl.lifecycle.nextcloud.actions.compose_exec", side_effect=compose_exec),
            patch("ctl.lifecycle.nextcloud.nextcloud_installed", side_effect=[False, True]),
            patch("ctl.lifecycle.nextcloud.time.sleep") as sleep,
        ):
            installed, _detail = install_nextcloud_if_needed(Path("/unused"), lambda _line: None)
        self.assertTrue(installed)
        sleep.assert_not_called()
        self.assertIn("flock -n", " ".join(next(c for c in seen if _is_lock_check(c))))

    def test_optional_install_executes_the_bounded_stage_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime-root")
            paths.projects.mkdir(parents=True)
            paths.data.mkdir(parents=True)
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            store = JobStore(paths.runtime / "control-plane.sqlite3")
            created = store.create(kind="lifecycle", service_id="mealie", action="install", actor="owner")
            claimed = store.claim("worker")
            self.assertIsNotNone(claimed)
            with (
                runtime_paths(paths),
                patch("ctl.service_ops.ControlState.runtime", return_value=state),
                patch("ctl.service_ops.actions.compose_config", return_value=(0, "")),
                patch("ctl.service_ops.actions.compose_image_list", return_value=(1, [])),
                patch("ctl.service_ops.actions.compose_pull", return_value=(0, "pulled")),
                patch(
                    "ctl.service_ops.actions.docker_image_digest",
                    return_value=(0, "ghcr.io/mealie-recipes/mealie@sha256:abc"),
                ),
                patch("ctl.service_ops.actions.compose_up", return_value=(0, "started")),
                patch("ctl.service_ops.wait_healthy", return_value=(True, "HTTP 200")),
                patch(
                    "ctl.lifecycle.integrations.adopt_mealie_admin",
                    return_value=(True, "Mealie's administrator is your Authentik account."),
                ),
                patch("ctl.service_ops.wait_for_provider", return_value={}),
                patch("ctl.service_ops.verify_sign_in", return_value="Sign-in through Authentik verified."),
                patch("ctl.service_ops.apply_route", return_value=(True, "ready")),
                patch("ctl.service_state.tailnet_dns_name", return_value=""),
            ):
                execute_claimed(store, claimed, "worker", ROOT)
            final = next(job for job in store.jobs() if job["id"] == created["id"])
            self.assertEqual(final["state"], "succeeded")
            self.assertEqual(state.installation("mealie")["state"], "running")
            stages = "\n".join(event["detail"] for event in store.events(created["id"]))
            for stage in (
                "validate_service",
                "materialize_runtime",
                "pull_images",
                "resolve_digests",
                "authentik_ready",
                "start_service",
                "verify_application",
                "configure_route",
                "verify_sign_in",
                "finalize",
            ):
                self.assertIn(stage, stages)
            self.assertEqual(state.service_identity("mealie")["state"], "ready")

    def test_install_waits_once_more_when_a_first_boot_container_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime-root")
            paths.projects.mkdir(parents=True)
            paths.data.mkdir(parents=True)
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            store = JobStore(paths.runtime / "control-plane.sqlite3")
            created = store.create(kind="lifecycle", service_id="mealie", action="install", actor="owner")
            claimed = store.claim("worker")
            self.assertIsNotNone(claimed)
            with (
                runtime_paths(paths),
                patch("ctl.service_ops.ControlState.runtime", return_value=state),
                patch("ctl.service_ops.actions.compose_config", return_value=(0, "")),
                patch("ctl.service_ops.actions.compose_image_list", return_value=(1, [])),
                patch("ctl.service_ops.actions.compose_pull", return_value=(0, "pulled")),
                patch(
                    "ctl.service_ops.actions.docker_image_digest",
                    return_value=(0, "ghcr.io/mealie-recipes/mealie@sha256:abc"),
                ),
                patch(
                    "ctl.service_ops.actions.compose_up",
                    side_effect=[(1, "container mu3lab-mealie-app-1 is unhealthy"), (0, "started"), (0, "started")],
                ) as compose_up,
                patch("ctl.service_ops.wait_healthy", return_value=(True, "HTTP 200")),
                patch(
                    "ctl.lifecycle.integrations.adopt_mealie_admin",
                    return_value=(True, "Mealie's administrator is your Authentik account."),
                ),
                patch("ctl.service_ops.wait_for_provider", return_value={}),
                patch("ctl.service_ops.verify_sign_in", return_value="Sign-in through Authentik verified."),
                patch("ctl.service_ops.apply_route", return_value=(True, "ready")),
                patch("ctl.service_state.tailnet_dns_name", return_value=""),
            ):
                execute_claimed(store, claimed, "worker", ROOT)
            final = next(job for job in store.jobs() if job["id"] == created["id"])
            self.assertEqual(final["state"], "succeeded")
            self.assertFalse(compose_up.call_args_list[1].kwargs["recreate"])

    def test_surfsense_install_allows_bounded_migration_and_zero_cache_startup(self):
        compose = (ROOT / "apps/surfsense/docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("condition: service_completed_successfully", compose)
        self.assertIn('SANDBOX_ENABLED: "FALSE"', compose)
        self.assertNotIn("docker.sock", compose)


if __name__ == "__main__":
    unittest.main()
