"""Local AI models and the household's outside-provider switch (R31)."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import core_wiring, local_models
from ctl.api import create_app
from ctl.jobs import JobStore
from ctl.runtime import RuntimePaths
from ctl.store import records
from tests import test_maintenance

OUTSIDE = {"privacy": "outside_allowed", "chat_model": "", "embedding_model": "bge-m3"}
LOCAL = {"privacy": "local_only", "chat_model": "qwen3.5-4b", "embedding_model": "bge-m3"}


class _Runtime(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = RuntimePaths(Path(tmp.name))
        self.paths.state.mkdir(parents=True)

    def store(self, chosen: dict[str, str]) -> None:
        local_models.save(chosen, "owner", self.paths)


class SettingsTests(_Runtime):
    def test_choices_come_only_from_the_reviewed_list(self):
        self.assertEqual(local_models.validate(LOCAL), LOCAL)
        for bad in (
            {**OUTSIDE, "privacy": "anything"},
            {**OUTSIDE, "chat_model": "llama3:70b"},
            {**OUTSIDE, "embedding_model": "mxbai-embed-large"},
            {**LOCAL, "chat_model": ""},  # local only with nothing to answer would silently break chat
        ):
            with self.subTest(settings=bad), self.assertRaises(ValueError):
                local_models.validate(bad)

    def test_a_new_install_allows_outside_providers_and_searches_with_bge_m3(self):
        chosen = local_models.settings(self.paths)
        self.assertEqual((chosen["privacy"], chosen["chat_model"], chosen["embedding_model"]), tuple(OUTSIDE.values()))
        self.assertFalse(local_models.local_only(self.paths))

    def test_an_older_install_keeps_the_search_model_its_index_was_built_with(self):
        proxy = self.paths.projects / "litellm"
        proxy.mkdir(parents=True)
        (proxy / "config.yaml").write_text("model: ollama/nomic-embed-text:latest\n")
        with patch("ctl.platform_apps.by_capability", return_value=SimpleNamespace(id="litellm")):
            self.assertEqual(local_models.embedding_model(self.paths).id, "nomic-embed-text")

    def test_a_damaged_record_falls_back_to_the_safe_defaults(self):
        records.put(
            *local_models.SETTINGS, {"privacy": "local_only", "chat_model": "", "embedding_model": "x"}, self.paths
        )
        self.assertEqual(local_models.settings(self.paths)["privacy"], "outside_allowed")


class RoutingTests(_Runtime):
    def routes(self) -> dict[str, dict]:
        models = local_models.litellm_models("GATEWAY", "GATEWAY_KEY", "RUNNER", "ollama", self.paths)
        return {item["model_name"]: item["litellm_params"] for item in models}

    def test_outside_allowed_routes_chat_to_the_provider_gateway(self):
        routes = self.routes()
        self.assertEqual(routes["mu3lab-chat"]["api_base"], "os.environ/GATEWAY")
        self.assertNotIn(local_models.LOCAL_ALIAS, routes)
        self.assertEqual(routes["mu3lab-embed"]["model"], "ollama/bge-m3:567m")
        self.assertEqual(local_models.chat_list(self.paths), "-all,+mu3lab-chat=Mu3Lab chat (outside providers)")

    def test_a_chosen_local_model_is_a_private_choice_beside_outside_chat(self):
        self.store({**LOCAL, "privacy": "outside_allowed"})
        routes = self.routes()
        self.assertEqual(routes["mu3lab-chat"]["api_base"], "os.environ/GATEWAY")
        self.assertEqual(routes[local_models.LOCAL_ALIAS]["model"], "ollama_chat/qwen3.5:4b-q4_K_M")
        self.assertEqual(
            local_models.chat_list(self.paths),
            "-all,+mu3lab-chat=Mu3Lab chat (outside providers),+mu3lab-local=Qwen 3.5 4B (private, on this server)",
        )

    def test_local_only_leaves_no_route_to_any_outside_provider(self):
        self.store(LOCAL)
        routes = self.routes()
        for name in ("mu3lab-chat", "mu3lab-fast", local_models.LOCAL_ALIAS):
            self.assertEqual(routes[name]["api_base"], "os.environ/RUNNER", name)
        self.assertFalse(any("GATEWAY" in str(params) for params in routes.values()))
        self.assertEqual(
            local_models.chat_list(self.paths), "-all,+mu3lab-chat=Qwen 3.5 4B (local only, on this server)"
        )

    def test_local_only_switches_off_every_provider_key_in_the_gateway(self):
        saved = [{"id": "groq", "api_key": "k", "label": "Groq"}]
        with (
            patch("ctl.core_wiring.records", return_value=saved),
            patch("ctl.core_wiring.ControlState.runtime", return_value=None),
            patch.dict(core_wiring.BY_ID, {"groq": object()}),
        ):
            self.assertTrue(core_wiring._provider_keys(self.paths)[0]["enabled"])
            self.store(LOCAL)
            self.assertFalse(core_wiring._provider_keys(self.paths)[0]["enabled"])


class JobTests(_Runtime):
    """The change downloads and checks first, then switches, and puts things back if routing fails."""

    def setUp(self):
        super().setUp()
        self.db = JobStore(self.paths.state / "mu3lab.db")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.deleted: list[str] = []
        self.applied: list[dict] = []
        self.present = {"bge-m3:567m", "nomic-embed-text:latest", "qwen3:8b-q4_K_M"}
        patches = {
            "RuntimePaths": lambda: self.paths,
            "downloaded": lambda: set(self.present),
            "pull": lambda tag, _log: self.present.add(tag),
            "check_chat": lambda _model: None,
            "check_embedding": lambda _model: None,
            "delete": self.deleted.append,
            "_apply": lambda paths, _root, _log: self.applied.append(local_models.settings(paths)),
            "_probe_litellm": lambda _log: None,
            "_followers": lambda _catalog, _paths: [],
            "index_tied": lambda _catalog, _paths: [],
        }
        for name, value in patches.items():
            self.stack.enter_context(patch(f"ctl.local_models.{name}", side_effect=value))
        self.stack.enter_context(patch("ctl.registry.load", return_value=SimpleNamespace(catalog=None)))
        self.stack.enter_context(patch("ctl.service_state.tailnet_dns_name", return_value="host.ts.net"))

    def run_job(self, chosen: dict[str, str]) -> dict:
        job = self.db.create(
            kind="wiring", service_id=local_models.SERVICE_ID, action="apply", actor="owner", params=chosen
        )
        claimed = self.db.claim("worker")
        assert claimed and claimed["id"] == job["id"]
        local_models.execute_claimed(self.db, claimed, "worker", Path("/repo"))
        return self.db.get(job["id"])

    def test_a_working_model_is_switched_to_and_unused_models_are_deleted(self):
        job = self.run_job(LOCAL)
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertIn("only on this server", job["detail"])
        self.assertIn("qwen3.5:4b-q4_K_M", self.present)
        self.assertEqual(local_models.settings(self.paths)["privacy"], "local_only")
        self.assertEqual(sorted(self.deleted), ["nomic-embed-text:latest", "qwen3:8b-q4_K_M"])

    def test_a_model_that_fails_its_check_changes_nothing(self):
        self.stack.enter_context(patch("ctl.local_models.check_chat", side_effect=RuntimeError("out of memory")))
        job = self.run_job(LOCAL)
        self.assertEqual((job["state"], job["error_code"]), ("failed", "model_failed"))
        self.assertIn("Nothing was changed", job["detail"])
        self.assertEqual(local_models.settings(self.paths)["privacy"], "outside_allowed")
        self.assertEqual((self.applied, self.deleted), ([], []))

    def test_failed_routing_puts_the_earlier_settings_back(self):
        self.stack.enter_context(
            patch("ctl.local_models._probe_litellm", side_effect=RuntimeError("LiteLLM returned HTTP 500"))
        )
        job = self.run_job(LOCAL)
        self.assertEqual((job["state"], job["error_code"]), ("failed", "routing_failed"))
        self.assertEqual(local_models.settings(self.paths)["privacy"], "outside_allowed")
        # Routed once with the new settings, then again with the earlier ones.
        self.assertEqual([item["privacy"] for item in self.applied], ["local_only", "outside_allowed"])
        self.assertEqual(self.deleted, [])

    def test_the_search_model_cannot_change_under_an_index_tied_app(self):
        self.stack.enter_context(patch("ctl.local_models.index_tied", return_value=["SurfSense"]))
        job = self.run_job({**OUTSIDE, "embedding_model": "nomic-embed-text"})
        self.assertEqual((job["state"], job["error_code"]), ("failed", "index_tied"))
        self.assertEqual(local_models.settings(self.paths)["embedding_model"], "bge-m3")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("ctl.api.security.ingress_token", return_value="token"))
        self.stack.enter_context(patch("ctl.api.security.csrf_token", return_value="bound"))
        self.stack.enter_context(patch("ctl.local_models.downloaded", side_effect=OSError("no Ollama")))
        self.stack.enter_context(patch("ctl.local_models.index_tied", return_value=[]))
        self.client = TestClient(create_app())
        self.headers = test_maintenance.MaintenanceApiTests.headers

    def test_the_settings_and_reviewed_models_are_shown(self):
        response = self.client.get("/api/v1/ai/models", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["settings"]["privacy"], "outside_allowed")
        self.assertEqual([model["id"] for model in body["chat_models"]], [model.id for model in local_models.CHAT])
        self.assertFalse(body["runner_reachable"])

    def test_a_change_is_queued_as_a_job(self):
        response = self.client.put("/api/v1/ai/models", headers=self.headers, json=LOCAL)
        self.assertEqual(response.status_code, 200, response.text)
        store = JobStore.runtime()
        assert store is not None
        job = store.get(response.json()["job"]["id"])
        assert job is not None
        self.assertEqual((job["service_id"], job["state"]), (local_models.SERVICE_ID, "queued"))
        store.transition(job["id"], "cancelled", actor="test")

    def test_unusable_or_unchanged_settings_are_refused(self):
        for body in ({**LOCAL, "chat_model": ""}, {**OUTSIDE, "chat_model": "llama3:70b"}, OUTSIDE):
            with self.subTest(body=body):
                response = self.client.put("/api/v1/ai/models", headers=self.headers, json=body)
                self.assertEqual(response.status_code, 422, response.text)

    def test_the_search_model_is_locked_while_an_index_tied_app_is_installed(self):
        with patch("ctl.local_models.index_tied", return_value=["SurfSense"]):
            body = {**OUTSIDE, "embedding_model": "nomic-embed-text"}
            response = self.client.put("/api/v1/ai/models", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("SurfSense", response.text)


if __name__ == "__main__":
    unittest.main()
