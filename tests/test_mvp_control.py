"""MVP contracts for global configuration, app installation, and MCP scope."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.mcp_catalog import load as load_mcp_catalog
from ctl.registry import load
from ctl.routes import render
from ctl.runtime import RuntimePaths
from ctl.service_ops import allowed_actions, execute_claimed


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
                         {"surfsense", "mealie", "actual-budget", "immich", "adventurelog", "paperless-ngx"})
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
        excluded = {"vaultwarden", "ingress", "authentik", "ollama", "litellm", "open-webui"}
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

    def test_surfsense_route_is_authentik_gated_before_local_login(self):
        block = render("{\n  admin off\n}\n", [load().get("surfsense")])
        self.assertIn(":19464 {", block)
        self.assertIn("forward_auth 127.0.0.1:9001", block)
        self.assertIn("reverse_proxy 127.0.0.1:3929", block)


class InstallationWorkflowTests(unittest.TestCase):
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
