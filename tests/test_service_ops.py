"""Curated lifecycle execution never accepts browser-supplied commands or paths."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.control_state import ControlState
from ctl.engine.hooks import SetupNeeded
from ctl.jobs import JobStore
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.service_ops import allowed_actions, execute_claimed, project_path
from ctl.setup_checks import waiting
from tests.support import runtime_paths


class ServiceOperationTests(unittest.TestCase):
    def test_always_on_service_cannot_be_stopped(self):
        service = load().get("ingress")
        self.assertNotIn("stop", allowed_actions(service, "ready"))

    def test_only_sign_in_and_the_private_proxy_are_always_on(self):
        registry = load()
        always_on = {service.id for service in registry.services if service.lifecycle == "always_on"}
        self.assertEqual(always_on, {"authentik", "ingress"})
        # Stopping the vault reaches every Bitwarden app, so its confirmation says so.
        self.assertIn("Bitwarden", registry.get("vaultwarden").stop_warning)
        self.assertIn("stop", allowed_actions(registry.get("firecrawl"), "ready"))

    def test_blocked_service_has_no_actions(self):
        self.assertEqual(allowed_actions(load().get("mealie"), "blocked"), [])

    def test_stopped_service_offers_start_without_restart(self):
        self.assertEqual(allowed_actions(load().get("actual-budget"), "stopped"), ["start", "uninstall"])

    def test_app_waiting_on_setup_offers_start_to_check_again(self):
        self.assertEqual(allowed_actions(load().get("lobehub"), "setup_required"), ["start", "uninstall"])

    def test_start_checks_setup_again_and_stays_stopped_until_nothing_is_missing(self):
        sentence = "LobeChat is set to use outside providers, but none is connected."
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            store = JobStore(paths.runtime / "control.sqlite")
            project = paths.projects / "lobehub"
            project.mkdir(parents=True)
            (project / "docker-compose.yml").write_text("services: {}")

            def start(missing):
                created = store.create(kind="lifecycle", service_id="lobehub", action="start", actor="owner")
                claimed = store.claim("worker")
                assert claimed is not None
                with (
                    runtime_paths(paths),
                    patch("ctl.service_ops.tailnet_dns_name", return_value="host.test"),
                    patch("ctl.service_ops.setup_checks.first_missing", return_value=missing),
                    patch("ctl.service_ops.actions.compose_action", return_value=(0, "started")) as action,
                    patch("ctl.service_ops.wait_healthy", return_value=(True, "HTTP 200")),
                ):
                    execute_claimed(store, claimed, "worker", Path(__file__).resolve().parents[1])
                return store.get(created["id"]), action

            job, action = start(SetupNeeded("chat_check", "no_outside_provider", sentence))
            self.assertEqual(
                (job["state"], job["error_code"], job["detail"]), ("failed", "no_outside_provider", sentence)
            )
            action.assert_not_called()
            self.assertEqual(waiting("lobehub", paths), sentence)
            job, action = start(None)
            self.assertEqual(job["state"], "succeeded")
            action.assert_called_once()
            self.assertEqual(waiting("lobehub", paths), "")

    def test_only_catalog_apps_can_be_uninstalled(self):
        self.assertIn("uninstall", allowed_actions(load().get("nextcloud"), "ready"))
        self.assertIn("uninstall", allowed_actions(load().get("nextcloud"), "failed"))
        self.assertNotIn("uninstall", allowed_actions(load().get("nextcloud"), "planned"))
        self.assertNotIn("uninstall", allowed_actions(load().get("ollama"), "ready"))
        self.assertNotIn("uninstall", allowed_actions(load().get("open-webui"), "stopped"))

    def test_core_apps_never_offer_uninstall_and_respect_stoppable(self):
        for service in load().services:
            if service.stage != "core":
                continue
            for state in ("ready", "stopped", "failed", "planned"):
                with self.subTest(app=service.id, state=state):
                    actions = allowed_actions(service, state)
                    self.assertNotIn("uninstall", actions)
                    self.assertNotIn("install", actions)
                    if not service.manifest.service.stoppable:
                        self.assertNotIn("stop", actions)

    def test_stopped_core_chat_starts_from_runtime_project(self):
        service = load().get("open-webui")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            project = paths.projects / service.id
            project.mkdir(parents=True)
            (project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            with runtime_paths(paths):
                selected = project_path(service, Path(__file__).resolve().parents[1])
        self.assertEqual(selected, project)
        self.assertEqual(allowed_actions(service, "stopped"), ["start"])

    def test_worker_resolves_curated_path_and_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            created = store.create(kind="lifecycle", service_id="ollama", action="start", actor="owner")
            claimed = store.claim("worker")
            assert claimed is not None
            paths = RuntimePaths(Path(tmp))
            project = paths.projects / "ollama"
            project.mkdir(parents=True)
            (project / "docker-compose.yml").write_text("services: {}")
            with (
                runtime_paths(paths),
                patch("ctl.service_ops.actions.compose_action", return_value=(0, "started")) as action,
                patch("ctl.service_ops.wait_healthy", return_value=(True, "HTTP 200")),
            ):
                execute_claimed(store, claimed, "worker", Path(__file__).resolve().parents[1])
            project, verb, _logger = action.call_args.args
            self.assertEqual(project.name, "ollama")
            self.assertEqual(verb, "start")
            final = next(item for item in store.jobs() if item["id"] == created["id"])
            self.assertEqual(final["state"], "succeeded")

    def test_start_first_starts_the_stopped_apps_it_needs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            store = JobStore(paths.runtime / "control.sqlite")
            state = ControlState(paths.runtime / "state.sqlite")
            state.set_installation("speaches", "stopped")
            state.set_installation("livekit", "stopped")
            for app in ("speaches", "livekit"):
                (paths.projects / app).mkdir(parents=True)
                (paths.projects / app / "docker-compose.yml").write_text("services: {}")

            def start(healthy):
                created = store.create(kind="lifecycle", service_id="livekit", action="start", actor="owner")
                claimed = store.claim("worker")
                assert claimed is not None
                with (
                    runtime_paths(paths),
                    patch("ctl.service_ops.ControlState.runtime", return_value=state),
                    patch("ctl.service_ops.tailnet_dns_name", return_value="host.test"),
                    patch("ctl.service_ops.setup_checks.first_missing", return_value=None),
                    patch("ctl.service_ops.sync_application", return_value=True),
                    patch("ctl.service_ops.actions.compose_action", return_value=(0, "started")) as action,
                    patch("ctl.service_ops.wait_healthy", side_effect=healthy),
                ):
                    execute_claimed(store, claimed, "worker", Path(__file__).resolve().parents[1])
                return store.get(created["id"]), [call.args[0].name for call in action.call_args_list]

            job, started = start(lambda service: (service.id != "speaches", "not ready"))
            self.assertEqual((job["state"], job["error_code"]), ("failed", "dependency_start_failed"))
            self.assertIn("Speaches", job["detail"])
            self.assertEqual(started, ["speaches"])
            self.assertEqual(state.installation("livekit")["state"], "stopped")
            job, started = start(lambda _service: (True, "HTTP 200"))
            self.assertEqual(job["state"], "succeeded")
            self.assertEqual(started, ["speaches", "livekit"])
            self.assertEqual(state.installation("speaches")["state"], "running")

    def test_worker_rejects_unregistered_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            store.create(kind="lifecycle", service_id="ollama", action="shell", actor="owner")
            claimed = store.claim("worker")
            assert claimed is not None
            execute_claimed(store, claimed, "worker", Path(__file__).resolve().parents[1])
            self.assertEqual(store.jobs()[0]["error_code"], "unsupported_action")

    def test_core_restart_uses_generated_project_without_environment_plumbing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            created = store.create(kind="lifecycle", service_id="litellm", action="restart", actor="owner")
            claimed = store.claim("worker")
            assert claimed is not None
            paths = RuntimePaths(Path(tmp))
            project = paths.projects / "litellm"
            project.mkdir(parents=True)
            (project / "docker-compose.yml").write_text("services: {}")
            with (
                runtime_paths(paths),
                patch("ctl.service_ops.ControlState.runtime", return_value=None),
                patch("ctl.service_ops.actions.compose_action", return_value=(0, "restarted")) as action,
                patch("ctl.service_ops.wait_healthy", return_value=(True, "HTTP 200")),
            ):
                execute_claimed(store, claimed, "worker", Path(__file__).resolve().parents[1])
            self.assertEqual(store.get(created["id"])["state"], "succeeded")
            self.assertEqual(action.call_args.args[0], project)
            self.assertIsNone(action.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
