"""Navidrome's setup through its own API, and the rules model runners and per-person logins share."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import vault_sync
from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext, load_app_hooks
from ctl.engine.project import Facts
from ctl.manifest.catalog import load
from ctl.rules.container_script import ContainerScript, ContainerScriptParams
from ctl.rules.needs_embedding_model import EmbeddingParams, NeedsEmbeddingModel
from ctl.rules.per_person_logins import everyone
from ctl.runtime import RuntimePaths
from ctl.store import person_logins

PEOPLE = [
    {"username": "dak", "name": "Dak", "email": "dak@example.com", "role": "admin", "active": True},
    {"username": "sam", "name": "Sam", "email": "sam@example.com", "role": "member", "active": True},
    {"username": "old", "name": "Old", "email": "old@example.com", "role": "admin", "active": False},
]


class _AppTest(unittest.TestCase):
    app_id = ""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = RuntimePaths(Path(tmp.name))
        self.catalog = load()
        self.app = self.catalog.get(self.app_id)
        self.project = self.paths.projects / self.app.id
        self.project.mkdir(parents=True)
        self.hooks = load_app_hooks(self.app)
        self.log: list[str] = []

    def ctx(self) -> HookContext:
        return HookContext(
            self.app,
            self.project,
            Compose(self.project),
            self.log.append,
            lambda *_: None,
            None,
            Facts("box.tail1234.ts.net", self.paths, self.catalog),
        )


# --- Navidrome ----------------------------------------------------------------------


# --- Shared rules -------------------------------------------------------------------


class PersonLoginTests(unittest.TestCase):
    def test_membership_follows_the_audience_and_active_state(self):
        members = {person["username"]: person["member"] for person in everyone("operators", PEOPLE)}
        self.assertEqual(members, {"dak": True, "sam": False, "old": False})
        members = {person["username"]: person["member"] for person in everyone("household", PEOPLE)}
        self.assertEqual(members, {"dak": True, "sam": True, "old": False})

    def test_a_personal_login_reaches_that_persons_vault_with_the_apps_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            person_logins.put("navidrome", "sam", "sam", "phone-secret", paths)
            with patch("ctl.vault_sync.onboarding_state.pending_logins", return_value=[]):
                items = vault_sync.items_for({"uid": "u-sam", "username": "sam", "role": "member"}, "box.ts.net", paths)
        item = next(item for item in items if item.mu3lab_id == "login:navidrome")
        self.assertEqual((item.username, item.password), ("sam", "phone-secret"))
        self.assertEqual(item.url, "https://box.ts.net:8474")


class RerunWhenTests(unittest.TestCase):
    def test_a_script_runs_again_once_the_settings_it_depends_on_change(self):
        rule = ContainerScript(
            ContainerScriptParams(
                at="after_healthy",
                service="app",
                script="scripts/music.sh",
                interpreter=("sh", "-s"),
                purpose="Share the music",
                rerun_when=("MU3LAB_MUSIC_SHARED",),
            )
        )
        env = {"MU3LAB_MUSIC_SHARED": "1"}
        ran: list[str] = []
        ctx = SimpleNamespace(
            env=lambda: dict(env),
            set_env=env.update,
            facts=None,
            app=SimpleNamespace(id="nextcloud", manifest=SimpleNamespace(name="Nextcloud")),
        )
        running = SimpleNamespace(installation=lambda _id: {"state": "running"})
        with (
            patch.object(
                ContainerScript,
                "_run",
                lambda self, _ctx: ran.append("ran") or env.update({self._ran_key: self._inputs(_ctx)}),
            ),
            patch("ctl.rules.container_script.ControlState.runtime", return_value=running),
        ):
            self.assertIn("again", rule.periodic(ctx))  # never ran with these settings
            self.assertEqual(rule.periodic(ctx), "")
            env["MU3LAB_MUSIC_SHARED"] = "0"
            self.assertIn("again", rule.periodic(ctx))
        self.assertEqual(ran, ["ran", "ran"])


class EmbeddingRuleTests(_AppTest):
    app_id = "ollama"

    def test_a_runner_that_is_not_chosen_downloads_nothing(self):
        rule = NeedsEmbeddingModel(EmbeddingParams(at="after_healthy"))
        other = SimpleNamespace(id="other", manifest=SimpleNamespace(name="Other runner"))
        with (
            patch("ctl.local_models.runner", return_value=other),
            patch("ctl.model_runners.engine", side_effect=AssertionError("must not be asked")),
        ):
            rule.after_healthy(self.ctx())

    def test_the_chosen_runner_downloads_the_search_model_when_missing(self):
        rule = NeedsEmbeddingModel(EmbeddingParams(at="after_healthy"))
        engine = SimpleNamespace(
            downloaded=lambda: set(), has=lambda _model, present: False, pulled=[], check_embedding=lambda _model: None
        )
        engine.pull = lambda model, _log: engine.pulled.append(model.id)
        with (
            patch("ctl.local_models.runner", return_value=self.app),
            patch("ctl.model_runners.engine", return_value=engine),
        ):
            rule.after_healthy(self.ctx())
        self.assertEqual(engine.pulled, ["bge-m3"])


if __name__ == "__main__":
    unittest.main()
