"""Navidrome's setup through its own API, and the rules model runners and per-person logins share."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from ctl import vault_sync
from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext, load_app_hooks
from ctl.engine.project import Facts
from ctl.manifest.catalog import load
from ctl.registry import load as load_registry
from ctl.routes import _block
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


class FakeNavidrome:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple[str, dict]] = []

    def api(self, _port: int, _token: str, path: str, *, method: str = "GET", data: Any = None) -> Any:
        if method == "GET":
            return list(self.users.values())
        self.writes.append((method, data))
        if method == "POST":
            self.users[data["userName"]] = {**data, "id": f"id-{data['userName']}"}
        else:
            self.users[data["userName"]].update(data)
        return {}


class NavidromeHookTests(_AppTest):
    app_id = "navidrome"

    def setUp(self) -> None:
        super().setUp()
        (self.project / ".env").write_text("NAVIDROME_ADMIN_PASSWORD=service-secret\n")
        self.fake = FakeNavidrome()
        self.hooks.sync_people.__globals__["_api"] = self.fake.api
        self.hooks.sync_people.__globals__["_token"] = lambda _port, _password: "token"

    def test_every_household_member_gets_an_account_and_administrators_stay_administrators(self):
        self.hooks.sync_people(self.ctx(), everyone("household", PEOPLE))
        self.assertEqual(set(self.fake.users), {"dak", "sam"})
        self.assertTrue(self.fake.users["dak"]["isAdmin"])
        self.assertFalse(self.fake.users["sam"]["isAdmin"])
        login = person_logins.get("navidrome", "sam", self.paths)
        assert login is not None
        self.assertEqual(login["password"], self.fake.users["sam"]["password"])

    def test_an_account_made_by_a_first_browser_visit_gets_a_phone_password(self):
        self.fake.users["sam"] = {"id": "id-sam", "userName": "sam", "isAdmin": False}
        self.hooks.sync_people(self.ctx(), everyone("household", PEOPLE[1:2]))
        self.assertEqual(self.fake.writes[0][0], "PUT")
        self.assertIsNotNone(person_logins.get("navidrome", "sam", self.paths))

    def test_a_deactivated_person_loses_phone_access_but_keeps_their_library(self):
        self.hooks.sync_people(self.ctx(), everyone("household", PEOPLE[1:2]))
        before = self.fake.users["sam"]["password"]
        self.hooks.sync_people(self.ctx(), everyone("household", [{**PEOPLE[1], "active": False}]))
        self.assertIn("sam", self.fake.users)
        self.assertNotEqual(self.fake.users["sam"]["password"], before)
        self.assertIsNone(person_logins.get("navidrome", "sam", self.paths))

    def test_phone_apps_skip_authentik_and_the_browser_gets_the_verified_name(self):
        block = _block(load_registry().get("navidrome"))
        bypass, browser = block.split("\t\thandle {", 1)
        self.assertIn("path /rest/*", bypass)
        self.assertNotIn("header Authorization", bypass)
        self.assertIn("header_up -X-Mu3lab-User", bypass)
        self.assertIn("forward_auth", browser)

    def test_nextcloud_shows_the_music_only_while_navidrome_is_installed(self):
        nextcloud = load().get("nextcloud").manifest
        music = next(item for item in nextcloud.integrates_with if item.capability == "music_library")
        self.assertEqual(music.env["MU3LAB_MUSIC_SHARED"], "1")
        self.assertEqual(music.absent_env["MU3LAB_MUSIC_SHARED"], "0")
        self.assertIn("music_library", self.app.manifest.capabilities)
        self.assertEqual(self.app.manifest.env["MU3LAB_MUSIC_FOLDER"], "{{data_root}}/navidrome/music")


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
