"""Curated lifecycle execution never accepts browser-supplied commands or paths."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.jobs import JobStore
from ctl.registry import load
from ctl.service_ops import allowed_actions, execute_claimed


class ServiceOperationTests(unittest.TestCase):
    def test_always_on_service_cannot_be_stopped(self):
        service = load().get("ingress")
        self.assertNotIn("stop", allowed_actions(service, "ready"))

    def test_blocked_service_has_no_actions(self):
        self.assertEqual(allowed_actions(load().get("mealie"), "blocked"), [])

    def test_stopped_service_offers_start_without_restart(self):
        self.assertEqual(allowed_actions(load().get("actual-budget"), "stopped"), ["start"])

    def test_worker_resolves_curated_path_and_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            created = store.create(kind="lifecycle", service_id="ollama",
                                   action="start", actor="owner")
            claimed = store.claim("worker")
            assert claimed is not None
            with patch("ctl.service_ops.actions.compose_action", return_value=(0, "started")) as action, \
                 patch("ctl.service_ops._wait_healthy", return_value=(True, "HTTP 200")):
                execute_claimed(store, claimed, "worker", Path(__file__).resolve().parents[1])
            project, verb, _logger = action.call_args.args
            self.assertEqual(project.name, "ollama")
            self.assertEqual(verb, "start")
            final = next(item for item in store.jobs() if item["id"] == created["id"])
            self.assertEqual(final["state"], "succeeded")

    def test_worker_rejects_unregistered_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            store.create(kind="lifecycle", service_id="ollama",
                         action="shell", actor="owner")
            claimed = store.claim("worker")
            assert claimed is not None
            execute_claimed(store, claimed, "worker", Path(__file__).resolve().parents[1])
            self.assertEqual(store.jobs()[0]["error_code"], "unsupported_action")

    def test_core_restart_passes_runtime_environment_to_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            created = store.create(kind="lifecycle", service_id="litellm",
                                   action="restart", actor="owner")
            claimed = store.claim("worker")
            assert claimed is not None
            with patch("ctl.service_ops.ControlState.runtime", return_value=None), \
                 patch("ctl.service_ops.actions.compose_action", return_value=(0, "restarted")) as action, \
                 patch("ctl.service_ops._wait_healthy", return_value=(True, "HTTP 200")):
                execute_claimed(store, claimed, "worker", Path(__file__).resolve().parents[1])
            self.assertEqual(store.get(created["id"])["state"], "succeeded")
            env = action.call_args.kwargs["env"]
            self.assertEqual(env["MU3LAB_ENV_FILE"], "/srv/mu3lab/projects/litellm/.env")
            self.assertEqual(env["MU3LAB_LITELLM_CONFIG"], "/srv/mu3lab/projects/litellm/config.yaml")


if __name__ == "__main__":
    unittest.main()
