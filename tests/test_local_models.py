"""Local AI models and the household's outside-provider switch (R31)."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import core_wiring, local_models, model_runners
from ctl.api import create_app
from ctl.jobs import JobStore
from ctl.runtime import RuntimePaths
from ctl.store import records
from tests import test_maintenance

OUTSIDE = {"privacy": "outside_allowed", "chat_model": "", "embedding_model": "bge-m3", "runner": ""}
LOCAL = {"privacy": "local_only", "chat_model": "qwen3.5-4b", "embedding_model": "bge-m3", "runner": ""}


class FakeEngine:
    """A runner that keeps its downloads in a set, by model id."""

    def __init__(self, name: str = "Unsloth Studio", *, follows_restart: bool = False) -> None:
        self.app = SimpleNamespace(id=name.split()[0].lower(), manifest=SimpleNamespace(name=name))
        self.embedding_follows_restart = follows_restart
        self.present = {"bge-m3", "nomic-embed-text", "qwen3-8b"}
        self.deleted: list[str] = []
        self.checked: list[str] = []

    def downloaded(self) -> set[str]:
        return set(self.present)

    def has(self, model, present) -> bool:
        return model.id in present

    def pull(self, model, _log) -> None:
        self.present.add(model.id)

    def delete(self, model) -> None:
        self.deleted.append(model.id)

    def check_chat(self, model) -> None:
        self.checked.append(model.id)

    def check_embedding(self, model) -> None:
        self.checked.append(model.id)


class _Runtime(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = RuntimePaths(Path(tmp.name))
        self.paths.state.mkdir(parents=True)

    def store(self, chosen: dict[str, str]) -> None:
        local_models.save(chosen, "owner", self.paths)


class SettingsTests(_Runtime):
    def test_invalid_saved_local_settings_never_enable_external_routes(self):
        records.put(*local_models.SETTINGS, {**LOCAL, "chat_model": "removed-model"}, self.paths)
        self.assertTrue(local_models.local_only(self.paths))
        routes = local_models.litellm_models("EXTERNAL", "KEY", "LOCAL", "ollama", self.paths)
        self.assertFalse(any(item["litellm_params"]["api_base"] == "os.environ/EXTERNAL" for item in routes))
        self.assertEqual(local_models.chat_list(self.paths), "-all")

    def test_choices_come_only_from_the_reviewed_list(self):
        self.assertEqual(local_models.validate(LOCAL), LOCAL)
        self.assertEqual(local_models.validate({**LOCAL, "runner": "ollama"})["runner"], "ollama")
        for bad in (
            {**OUTSIDE, "privacy": "anything"},
            {**OUTSIDE, "chat_model": "llama3:70b"},
            {**OUTSIDE, "embedding_model": "mxbai-embed-large"},
            {**OUTSIDE, "runner": "litellm"},  # not a model runner
            {**LOCAL, "chat_model": ""},  # local only with nothing to answer would silently break chat
        ):
            with self.subTest(settings=bad), self.assertRaises(ValueError):
                local_models.validate(bad)

    def test_a_new_install_allows_outside_providers_and_searches_with_bge_m3(self):
        chosen = local_models.settings(self.paths)
        self.assertEqual(
            (chosen["privacy"], chosen["chat_model"], chosen["embedding_model"], chosen["runner"]),
            tuple(OUTSIDE.values()),
        )
        self.assertFalse(local_models.local_only(self.paths))

    def test_an_older_install_keeps_the_search_model_its_index_was_built_with(self):
        proxy = self.paths.projects / "litellm"
        proxy.mkdir(parents=True)
        (proxy / "config.yaml").write_text("model: ollama/nomic-embed-text:latest\n")
        with patch("ctl.platform_apps.by_capability", return_value=SimpleNamespace(id="litellm")):
            self.assertEqual(local_models.embedding_model(self.paths).id, "nomic-embed-text")

    def test_every_reviewed_model_can_run_in_unsloth_studio(self):
        for model in (*local_models.CHAT, *local_models.EMBEDDING):
            with self.subTest(model=model.id):
                self.assertRegex(model.gguf, r"^[\w.-]+/[\w.-]+-GGUF$")
                self.assertTrue(model.quant)

    def test_a_damaged_record_denies_outside_inference(self):
        records.put(
            *local_models.SETTINGS, {"privacy": "local_only", "chat_model": "", "embedding_model": "x"}, self.paths
        )
        self.assertEqual(local_models.settings(self.paths)["privacy"], "local_only")


class RoutingTests(_Runtime):
    def routes(self, runner: str = "unsloth") -> dict[str, dict]:
        app = model_runners.find(runner)
        assert app is not None
        with patch("ctl.local_models.runner", return_value=app):
            models = local_models.litellm_models("GATEWAY", "GATEWAY_KEY", "RUNNER", "RUNNER_KEY", self.paths)
        return {item["model_name"]: item["litellm_params"] for item in models}

    def test_outside_allowed_routes_chat_to_the_provider_gateway(self):
        routes = self.routes()
        self.assertEqual(routes["mu3lab-chat"]["api_base"], "os.environ/GATEWAY")
        self.assertNotIn(local_models.LOCAL_ALIAS, routes)
        # Unsloth Studio serves its search model under the GGUF repository's name.
        self.assertEqual(routes["mu3lab-embed"]["model"], "openai/gpustack/bge-m3-GGUF")
        self.assertEqual(self.routes("ollama")["mu3lab-embed"]["model"], "ollama/bge-m3:567m")
        self.assertEqual(local_models.chat_list(self.paths), "-all,+mu3lab-chat=Mu3Lab chat (outside providers)")

    def test_a_runner_with_a_key_gets_it_and_one_without_does_not(self):
        project = self.paths.projects / "unsloth"
        project.mkdir(parents=True)
        (project / ".env").write_text("UNSLOTH_API_KEY=sk-unsloth-test\n")
        self.assertEqual(self.routes()["mu3lab-embed"]["api_key"], "os.environ/RUNNER_KEY")
        self.assertNotIn("api_key", self.routes("ollama")["mu3lab-embed"])

    def test_a_chosen_local_model_is_a_private_choice_beside_outside_chat(self):
        self.store({**LOCAL, "privacy": "outside_allowed"})
        routes = self.routes()
        self.assertEqual(routes["mu3lab-chat"]["api_base"], "os.environ/GATEWAY")
        self.assertEqual(routes[local_models.LOCAL_ALIAS]["model"], "openai/unsloth/Qwen3.5-4B-GGUF:Q4_K_M")
        self.assertEqual(self.routes("ollama")[local_models.LOCAL_ALIAS]["model"], "ollama_chat/qwen3.5:4b-q4_K_M")
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


class RunnerChoiceTests(unittest.TestCase):
    def test_the_owners_choice_counts_only_while_it_is_installed(self):
        cases = (
            ({"unsloth", "ollama"}, "ollama", "ollama"),
            ({"unsloth"}, "ollama", "unsloth"),  # Ollama was removed: back to the default
            ({"ollama"}, "", "ollama"),  # an older install with only Ollama keeps working
            (set(), "", "unsloth"),
        )
        for present, chosen, expected in cases:
            with (
                self.subTest(present=present, chosen=chosen),
                patch("ctl.model_runners.installed", side_effect=lambda app_id, _state=None, p=present: app_id in p),
                patch("ctl.model_runners.ControlState.runtime", return_value=None),
            ):
                self.assertEqual(model_runners.effective(chosen).id, expected)

    def test_unsloth_studio_is_the_default_and_ollama_is_optional(self):
        self.assertEqual(model_runners.default_app().id, "unsloth")
        self.assertEqual(
            {app.id: app.manifest.tier for app in model_runners.runners()},
            {
                "ollama": "optional",
                "unsloth": "core",
            },
        )


class JobTests(_Runtime):
    """The change downloads and checks first, then switches, and puts things back if routing fails."""

    def setUp(self):
        super().setUp()
        self.db = JobStore(self.paths.state / "mu3lab.db")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.applied: list[dict] = []
        self.followed = 0
        self.engines = {"unsloth": FakeEngine(), "ollama": FakeEngine("Ollama")}
        self.engine = self.engines["unsloth"]

        def follow(_catalog, _paths):
            self.followed += 1
            return []

        patches = {
            "RuntimePaths": lambda: self.paths,
            "_apply": lambda paths, _root, _log: self.applied.append(local_models.settings(paths)),
            "_probe_litellm": lambda _log: None,
            "_followers": follow,
            "index_tied": lambda _catalog, _paths: [],
        }
        for name, value in patches.items():
            self.stack.enter_context(patch(f"ctl.local_models.{name}", side_effect=value))
        self.stack.enter_context(
            patch("ctl.model_runners.engine", side_effect=lambda app, _paths=None: self.engines[app.id])
        )
        self.stack.enter_context(patch("ctl.model_runners.installed", return_value=True))
        self.stack.enter_context(patch("ctl.model_runners.ControlState.runtime", return_value=None))
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
        self.assertIn("only on this server, in Unsloth Studio", job["detail"])
        self.assertIn("qwen3.5-4b", self.engine.present)
        self.assertEqual(local_models.settings(self.paths)["privacy"], "local_only")
        self.assertEqual(local_models.settings(self.paths)["runner"], "unsloth")
        self.assertEqual(sorted(self.engine.deleted), ["nomic-embed-text", "qwen3-8b"])

    def test_switching_runners_downloads_into_the_new_one_and_leaves_the_old_one_alone(self):
        ollama = self.engines["ollama"]
        ollama.present = set()
        job = self.run_job({**LOCAL, "runner": "ollama"})
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(ollama.present, {"qwen3.5-4b", "bge-m3"})
        self.assertEqual(ollama.checked, ["qwen3.5-4b", "bge-m3"])
        # The earlier runner keeps its models, so switching back is quick.
        self.assertEqual(self.engine.deleted, [])
        self.assertEqual(local_models.settings(self.paths)["runner"], "ollama")

    def test_a_runner_that_restarts_for_a_new_search_model_is_checked_after_it_follows(self):
        self.engine.embedding_follows_restart = True
        checked_after: list[int] = []
        self.engine.check_embedding = lambda _model: checked_after.append(self.followed)
        job = self.run_job({**OUTSIDE, "embedding_model": "nomic-embed-text"})
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual(checked_after, [1])

    def test_a_search_model_that_fails_after_the_restart_puts_everything_back(self):
        self.engine.embedding_follows_restart = True
        self.engine.check_embedding = lambda _model: (_ for _ in ()).throw(RuntimeError("no vectors"))
        job = self.run_job({**OUTSIDE, "embedding_model": "nomic-embed-text"})
        self.assertEqual((job["state"], job["error_code"]), ("failed", "model_failed"))
        self.assertEqual(local_models.settings(self.paths)["embedding_model"], "bge-m3")
        self.assertEqual([item["embedding_model"] for item in self.applied], ["nomic-embed-text", "bge-m3"])
        self.assertEqual(self.followed, 2)

    def test_a_model_that_fails_its_check_changes_nothing(self):
        self.engine.check_chat = lambda _model: (_ for _ in ()).throw(RuntimeError("out of memory"))
        job = self.run_job(LOCAL)
        self.assertEqual((job["state"], job["error_code"]), ("failed", "model_failed"))
        self.assertIn("Nothing was changed", job["detail"])
        self.assertEqual(local_models.settings(self.paths)["privacy"], "outside_allowed")
        self.assertEqual((self.applied, self.engine.deleted), ([], []))

    def test_a_runner_that_is_not_installed_is_refused(self):
        with patch("ctl.model_runners.installed", return_value=False):
            job = self.run_job({**LOCAL, "runner": "ollama"})
        self.assertEqual((job["state"], job["error_code"]), ("failed", "runner_missing"))

    def test_failed_follower_keeps_models_it_may_still_use(self):
        app = SimpleNamespace(id="demo", manifest=SimpleNamespace(name="Demo"))
        with (
            patch("ctl.local_models._followers", return_value=[app]),
            patch("ctl.engine.rewire.rewire_one", side_effect=RuntimeError("restart failed")),
        ):
            job = self.run_job(LOCAL)
        self.assertEqual((job["state"], job["error_code"]), ("failed", "followers_incomplete"))
        self.assertIn("Demo", job["detail"])
        self.assertEqual(self.engine.deleted, [])

    def test_failed_routing_puts_the_earlier_settings_back(self):
        self.stack.enter_context(
            patch("ctl.local_models._probe_litellm", side_effect=RuntimeError("LiteLLM returned HTTP 500"))
        )
        job = self.run_job(LOCAL)
        self.assertEqual((job["state"], job["error_code"]), ("failed", "routing_failed"))
        self.assertEqual(local_models.settings(self.paths)["privacy"], "outside_allowed")
        # Routed once with the new settings, then again with the earlier ones.
        self.assertEqual([item["privacy"] for item in self.applied], ["local_only", "outside_allowed"])
        self.assertEqual(self.engine.deleted, [])

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
        unreachable = FakeEngine()
        unreachable.downloaded = lambda: (_ for _ in ()).throw(OSError("no runner"))  # type: ignore[method-assign]
        self.stack.enter_context(patch("ctl.model_runners.engine", return_value=unreachable))
        self.stack.enter_context(patch("ctl.model_runners.installed", return_value=True))
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
        self.assertEqual({runner["id"] for runner in body["runners"]}, {"unsloth", "ollama"})

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
        unchanged = {**OUTSIDE, "runner": local_models.runner().id}
        for body in ({**LOCAL, "chat_model": ""}, {**OUTSIDE, "chat_model": "llama3:70b"}, OUTSIDE, unchanged):
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
