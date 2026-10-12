"""Unsloth Studio (the default model runner) and Navidrome: setup through their own APIs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx

from ctl import model_runners, vault_sync
from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext, load_app_hooks
from ctl.engine.project import Facts
from ctl.integrations.unsloth_studio import Studio, StudioError
from ctl.manifest.catalog import load
from ctl.registry import load as load_registry
from ctl.routes import _block
from ctl.rules.container_script import ContainerScript, ContainerScriptParams
from ctl.rules.needs_embedding_model import EmbeddingParams, NeedsEmbeddingModel
from ctl.rules.per_person_logins import everyone
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
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


# --- Unsloth Studio -----------------------------------------------------------------


class StudioClientTests(unittest.TestCase):
    def studio(self, handler) -> Studio:
        return Studio(8095, transport=httpx.MockTransport(handler))

    def test_a_download_is_followed_until_it_completes(self):
        states = iter(["running", "complete"])
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(f"{request.method} {request.url.path}")
            if request.url.path == "/api/hub/download":
                self.assertEqual(json.loads(request.content), {"repo_id": "r/x-GGUF", "gguf_variant": "Q4_K_M"})
                return httpx.Response(202, json={"state": "running"})
            return httpx.Response(200, json={"state": next(states)})

        with patch("ctl.integrations.unsloth_studio.time.sleep"), self.studio(handler) as studio:
            studio.download("r/x-GGUF", "Q4_K_M", lambda _line: None)
        self.assertEqual(seen.count("GET /api/hub/download-status"), 2)

    def test_a_failed_download_says_so_without_secrets(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/hub/download":
                return httpx.Response(202, json={})
            return httpx.Response(200, json={"state": "error", "error": "disk full"})

        with self.studio(handler) as studio, self.assertRaisesRegex(StudioError, "disk full"):
            studio.download("r/x-GGUF", "Q4_K_M", lambda _line: None)

    def test_only_finished_downloads_count(self):
        cached = [{"repo_id": "a/done-GGUF", "partial": False}, {"repo_id": "a/half-GGUF", "partial": True}]
        with self.studio(lambda _request: httpx.Response(200, json={"cached": cached})) as studio:
            self.assertEqual(studio.downloaded(), {"a/done-GGUF"})

    def test_a_new_account_gets_its_password_through_its_setup_code(self):
        calls: list[tuple[str, dict, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else {}
            calls.append((request.url.path, body, request.headers.get("authorization", "")))
            if request.url.path == "/api/auth/login":
                return httpx.Response(200, json={"access_token": "person-token"})
            return httpx.Response(200, json={"access_token": "new"})

        with self.studio(handler) as studio:
            studio.set_password("dak", "setup-code", "chosen-password")
        self.assertEqual(calls[0][:2], ("/api/auth/login", {"username": "dak", "password": "setup-code"}))
        self.assertEqual(calls[1][0], "/api/auth/change-password")
        self.assertEqual(calls[1][1], {"current_password": "setup-code", "new_password": "chosen-password"})
        self.assertEqual(calls[1][2], "Bearer person-token")

    def test_refusals_carry_studios_own_sentence_only_when_it_is_short(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"detail": "Username is unavailable"})

        with self.studio(handler) as studio, self.assertRaisesRegex(StudioError, "Username is unavailable"):
            studio.create_account("dak")


class StudioEngineTests(_AppTest):
    app_id = "unsloth"

    def test_litellm_reaches_studio_on_the_private_network_with_mu3labs_key(self):
        (self.project / ".env").write_text("UNSLOTH_API_KEY=sk-unsloth-abc\n")
        engine = model_runners.engine(self.app, self.paths)
        self.assertEqual(engine.api_base(), "http://unsloth:8000/v1")
        self.assertEqual(engine.api_key(), "sk-unsloth-abc")
        self.assertTrue(engine.embedding_follows_restart)

    def test_a_search_model_with_the_wrong_vector_size_is_refused(self):
        (self.project / ".env").write_text("UNSLOTH_API_KEY=sk-unsloth-abc\n")
        engine = model_runners.engine(self.app, self.paths)
        model = SimpleNamespace(name="BGE-M3", gguf="gpustack/bge-m3-GGUF", dimensions=1024)
        with patch.object(Studio, "embed", return_value=[0.1] * 384), self.assertRaisesRegex(RuntimeError, "1024"):
            engine.check_embedding(model)  # type: ignore[arg-type]

    def test_an_unfinished_setup_is_reported_rather_than_sent_without_a_key(self):
        with self.assertRaisesRegex(model_runners.RunnerError, "has not finished setting up"):
            model_runners.engine(self.app, self.paths).downloaded()


class FakeStudio:
    """Studio's accounts, as the hooks see them through ``Studio``."""

    def __init__(self) -> None:
        self.accounts_list: list[dict[str, Any]] = []
        self.passwords: dict[str, str] = {}
        self.events: list[str] = []
        self.key_valid = False

    def __call__(self, _port: int, *, token: str = "", **_kwargs: Any) -> FakeStudio:
        self.token = token
        return self

    def __enter__(self) -> FakeStudio:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def login(self, username: str, password: str) -> dict:
        self.events.append(f"login {username}")
        return {}

    def key_works(self) -> bool:
        return self.key_valid

    def create_key(self, name: str) -> str:
        self.events.append(f"key {name}")
        return "sk-unsloth-new"

    def prepare_routing(self) -> None:
        self.events.append("routing")

    def accounts(self) -> list[dict[str, Any]]:
        return self.accounts_list

    def create_account(self, username: str) -> dict:
        account = {"account_id": f"id-{username}", "username": username, "is_active": True}
        self.accounts_list.append(account)
        return {"account": account, "setup_code": f"code-{username}"}

    def new_setup_code(self, account_id: str) -> dict:
        account = next(item for item in self.accounts_list if item["account_id"] == account_id)
        return {"account": account, "setup_code": "fresh-code"}

    def set_active(self, account_id: str, active: bool) -> None:
        self.events.append(f"{'open' if active else 'close'} {account_id}")
        next(item for item in self.accounts_list if item["account_id"] == account_id)["is_active"] = active

    def set_password(self, username: str, _code: str, password: str) -> None:
        self.passwords[username] = password


class UnslothHookTests(_AppTest):
    app_id = "unsloth"

    def setUp(self) -> None:
        super().setUp()
        (self.project / ".env").write_text("UNSLOTH_STUDIO_PASSWORD=owner-secret\n")
        self.studio = FakeStudio()
        self.hooks.sync_people.__globals__["Studio"] = self.studio

    def test_setup_mints_mu3labs_key_once_and_turns_on_model_routing(self):
        self.hooks.bootstrap_account(self.ctx())
        self.assertIn("key mu3lab-litellm", self.studio.events)
        self.assertEqual(read_runtime_env(self.project / ".env")["UNSLOTH_API_KEY"], "sk-unsloth-new")
        self.studio.events.clear()
        self.studio.key_valid = True
        self.hooks.bootstrap_account(self.ctx())
        self.assertEqual(self.studio.events, ["login unsloth", "routing"])

    def test_each_administrator_gets_a_studio_login_saved_for_their_vault(self):
        self.hooks.sync_people(self.ctx(), everyone("operators", PEOPLE))
        self.assertEqual(set(self.studio.passwords), {"dak"})  # members and deactivated people get none
        login = person_logins.get("unsloth", "dak", self.paths)
        assert login is not None
        self.assertEqual((login["login"], login["password"]), ("dak", self.studio.passwords["dak"]))

    def test_someone_who_is_no_longer_an_administrator_is_closed_and_reopens_with_the_same_login(self):
        self.hooks.sync_people(self.ctx(), everyone("operators", PEOPLE))
        demoted = [{**PEOPLE[0], "role": "member"}]
        self.hooks.sync_people(self.ctx(), everyone("operators", demoted))
        self.assertIn("close id-dak", self.studio.events)
        password = self.studio.passwords["dak"]
        self.hooks.sync_people(self.ctx(), everyone("operators", PEOPLE))
        self.assertIn("open id-dak", self.studio.events)
        self.assertEqual(self.studio.passwords["dak"], password)

    def test_a_lost_password_is_replaced_through_a_fresh_setup_code(self):
        self.studio.accounts_list.append({"account_id": "id-dak", "username": "dak", "is_active": True})
        self.hooks.sync_people(self.ctx(), everyone("operators", PEOPLE))
        self.assertIn("dak", self.studio.passwords)

    def test_a_person_named_like_studios_own_account_is_skipped(self):
        self.hooks.sync_people(self.ctx(), everyone("operators", [{**PEOPLE[0], "username": "Unsloth"}]))
        self.assertEqual(self.studio.passwords, {})
        self.assertTrue(any("shares its name" in line for line in self.log))


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
        unsloth = self.catalog.get("unsloth")
        with (
            patch("ctl.local_models.runner", return_value=unsloth),
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
