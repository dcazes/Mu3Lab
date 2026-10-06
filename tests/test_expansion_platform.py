"""Platform features added for the 2026 app expansion.

Media libraries, apps that work with each other (integrates_with and
rewiring), per-person accounts, the owner guard's script check and finish
step, email identity headers, per-app role claims, form-based sign-in
launch, speech routing in LiteLLM, and personal voice keys.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import httpx
import yaml

from ctl import hostinfo, voice_keys
from ctl.authentik_blueprints import OidcApp, render_oidc_blueprint
from ctl.core_wiring import RoutingParams, speech_models
from ctl.engine import rewire
from ctl.engine.compose import Compose, ScriptResult
from ctl.engine.hooks import HookContext
from ctl.engine.launch import caddy_handler
from ctl.engine.project import Facts, build_env, render
from ctl.integrations.litellm import LiteLLM
from ctl.lifecycle.signin_check import _form_launch
from ctl.manifest.catalog import load
from ctl.manifest.models import Oidc
from ctl.registry import load as load_registry
from ctl.routes import _block
from ctl.rules import rules_for
from ctl.rules.initial_owner_guard import InitialOwnerGuard
from ctl.rules.per_person_accounts import DIGEST_ENV, PerPersonAccounts, digest, household
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

OWNER = {"owner_uid": "u-owner", "email": "owner@example.com", "username": "owner", "display_name": "Owner"}


class ScriptedContext(HookContext):
    """A HookContext whose container scripts answer from a queue instead of Docker."""

    answers: list[ScriptResult]
    calls: list[tuple[str, str, list[str]]]
    reregistered: int

    def run_script(
        self,
        service: str,
        script: str,
        interpreter: Sequence[str],
        *,
        args: Sequence[str] = (),
        timeout: int = 300,
        folder: Path | None = None,
    ) -> ScriptResult:
        self.calls.append((service, script, list(args)))
        return self.answers.pop(0)


def scripted(app_id: str, paths: RuntimePaths, answers: list[ScriptResult], env: str = "") -> ScriptedContext:
    catalog = load()
    app = catalog.get(app_id)
    project = paths.projects / app_id
    project.mkdir(parents=True, exist_ok=True)
    (project / ".env").write_text(env)
    ctx = ScriptedContext(
        app, project, Compose(project), lambda _line: None, lambda *_: None, OWNER, Facts("box.test", paths, catalog)
    )
    ctx.answers, ctx.calls, ctx.reregistered = answers, [], 0

    def reregister() -> None:
        ctx.reregistered += 1

    ctx.reregister_sign_in = reregister
    return ctx


def ok(marker: str) -> ScriptResult:
    return ScriptResult(ok=True, raw=marker + "\n")


class MediaFolderTests(unittest.TestCase):
    def test_media_env_points_under_the_media_root_and_folders_are_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load()
            project = render(catalog.get("audiobookshelf"), Facts("box.test", paths, catalog))
            env = read_runtime_env(project / ".env")
            self.assertEqual(env["MU3LAB_MEDIA_AUDIOBOOKS"], str(paths.media / "audiobooks"))
            self.assertTrue((paths.media / "audiobooks").is_dir())
            self.assertTrue((paths.media / "podcasts").is_dir())

    def test_media_is_never_an_app_data_folder(self):
        # Uninstall's delete-data and backups use mounts under the data root only.
        compose = (load().get("audiobookshelf").folder / "docker-compose.yml").read_text()
        self.assertIn("${MU3LAB_MEDIA_AUDIOBOOKS", compose)
        self.assertNotIn("MU3LAB_DATA_ROOT:-/srv/mu3lab/data}/audiobookshelf/audiobooks", compose)


class IntegrationTests(unittest.TestCase):
    def _env(self, paths: RuntimePaths, app_id: str) -> dict[str, str]:
        catalog = load()
        return build_env(catalog.get(app_id), Facts("box.test", paths, catalog), {}, [])

    def _install(self, paths: RuntimePaths, app_id: str, env: str = "") -> None:
        project = paths.projects / app_id
        project.mkdir(parents=True, exist_ok=True)
        (project / "docker-compose.yml").write_text("services: {}\n")
        (project / ".env").write_text(env)

    def test_absent_provider_uses_the_fallback_and_drops_provider_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            env = self._env(paths, "open-webui")
            self.assertEqual(env["AUDIO_STT_ENGINE"], "web")
            self.assertNotIn("AUDIO_STT_MODEL", env)

    def test_installed_provider_supplies_its_published_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            self._install(paths, "speaches", "MU3LAB_TTS_VOICE=af_heart\nMU3LAB_SPEECH_URL=http://speaches:8000/v1\n")
            env = self._env(paths, "open-webui")
            self.assertEqual(env["AUDIO_STT_ENGINE"], "openai")
            self.assertEqual(env["AUDIO_TTS_VOICE"], "af_heart")
            litellm = self._env(paths, "litellm")
            self.assertEqual(litellm["MU3LAB_SPEECH_URL"], "http://speaches:8000/v1")

    def test_removing_the_provider_removes_its_settings_on_the_next_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load()
            facts = Facts("box.test", paths, catalog)
            before = {"MU3LAB_SPEECH_URL": "http://speaches:8000/v1", "MU3LAB_STT_MODEL": "x"}
            env = build_env(catalog.get("litellm"), facts, before, [])
            self.assertNotIn("MU3LAB_SPEECH_URL", env)
            self.assertNotIn("MU3LAB_STT_MODEL", env)

    def test_consumers_are_installed_apps_using_one_of_the_providers_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load()
            self._install(paths, "open-webui")
            names = {app.id for app in rewire.consumers(catalog.get("speaches"), catalog, paths)}
            # LiteLLM is core (always present); Open WebUI is installed here; Dawarich is not.
            self.assertEqual(names, {"litellm", "open-webui"})
            self.assertEqual(rewire.consumers(catalog.get("photon"), catalog, paths), [])

    def test_rewire_restarts_only_a_running_consumer_whose_files_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load()
            facts = Facts("box.test", paths, catalog)
            render(catalog.get("open-webui"), facts)
            with (
                patch.object(rewire, "_running", return_value=True),
                patch.object(Compose, "up", return_value=(0, "")) as up,
            ):
                self.assertFalse(rewire.rewire_one(catalog.get("open-webui"), facts, lambda _line: None))
                self._install(paths, "speaches", "MU3LAB_TTS_VOICE=af_heart\n")
                self.assertTrue(rewire.rewire_one(catalog.get("open-webui"), facts, lambda _line: None))
            self.assertEqual(up.call_count, 1)


class ConfigDefaultTests(unittest.TestCase):
    def test_country_comes_from_the_timezone_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = Path(tmp) / "zone.tab"
            table.write_text("# comment\nCA\t+4339-07923\tAmerica/Toronto\tEastern\nDE\t+5230+01322\tEurope/Berlin\n")
            self.assertEqual(hostinfo.country_code("America/Toronto", table), "ca")
            self.assertEqual(hostinfo.country_code("Mars/Base", table), "")

    def test_region_default_follows_the_host_only_when_a_ready_index_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load()
            facts = Facts("box.test", paths, catalog)
            with patch.object(hostinfo, "country_code", return_value="ca"):
                self.assertEqual(build_env(catalog.get("photon"), facts, {}, [])["PHOTON_REGION"], "ca")
            with patch.object(hostinfo, "country_code", return_value="zz"):
                self.assertNotIn("PHOTON_REGION", build_env(catalog.get("photon"), facts, {}, []))


class PerPersonAccountsTests(unittest.TestCase):
    PEOPLE: ClassVar[list[dict[str, Any]]] = [
        {"username": "sam", "name": "Sam", "email": "sam@example.com", "role": "member", "active": True},
        {"username": "owner", "name": "Owner", "email": "owner@example.com", "role": "admin", "active": True},
    ]

    def test_household_is_stable_and_audience_filters_operators(self):
        self.assertEqual([p["username"] for p in household("household", self.PEOPLE)], ["owner", "sam"])
        self.assertEqual([p["username"] for p in household("operators", self.PEOPLE)], ["owner"])

    def test_periodic_runs_the_script_only_when_the_household_changed(self):
        rule = rules_for(load().get("beaver-habits").manifest)[0]
        self.assertIsInstance(rule, PerPersonAccounts)
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.rules.per_person_accounts.list_people") as people:
            paths = RuntimePaths(Path(tmp))
            people.return_value = self.PEOPLE
            ctx = scripted("beaver-habits", paths, [ok("MU3LAB_BEAVER_PEOPLE_OK created=2")])
            self.assertIn("match the household", rule.periodic(ctx))
            sent = json.loads(ctx.calls[0][2][0])
            self.assertEqual({person["email"] for person in sent}, {"sam@example.com", "owner@example.com"})
            self.assertEqual(ctx.env()[DIGEST_ENV], digest(household("household", self.PEOPLE)))
            self.assertEqual(rule.periodic(ctx), "")  # unchanged: no script run
            self.assertEqual(len(ctx.calls), 1)

    def test_a_failed_sync_is_retried_later_and_fails_an_install(self):
        rule = rules_for(load().get("beaver-habits").manifest)[0]
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.rules.per_person_accounts.list_people") as people:
            paths = RuntimePaths(Path(tmp))
            people.return_value = self.PEOPLE
            ctx = scripted("beaver-habits", paths, [ScriptResult(ok=False, error="boom"), ScriptResult(ok=False)])
            self.assertEqual(rule.periodic(ctx), "")
            self.assertNotIn(DIGEST_ENV, ctx.env())
            with self.assertRaises(Exception) as raised:
                rule.after_healthy(ctx)
            self.assertIn("could not create household accounts", str(raised.exception))


class OwnerGuardScriptTests(unittest.TestCase):
    def _rule(self, app_id: str) -> InitialOwnerGuard:
        return next(rule for rule in rules_for(load().get(app_id).manifest) if isinstance(rule, InitialOwnerGuard))

    def test_script_check_lifts_the_guard_once_the_owner_signed_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = scripted(
                "outline",
                RuntimePaths(Path(tmp)),
                [ok("MU3LAB_OUTLINE_OWNER_OK")],
                "MU3LAB_INITIAL_OWNER_USERNAME=owner\n",
            )
            self.assertIn("whole household", self._rule("outline").periodic(ctx))
            self.assertEqual(ctx.calls[0][2], ["owner", "owner@example.com"])
            self.assertEqual(ctx.env()["MU3LAB_INITIAL_OWNER_USERNAME"], "")
            self.assertEqual(ctx.reregistered, 1)

    def test_guard_stays_until_the_finish_step_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = [ok("MU3LAB_DAWARICH_OWNER_OK"), ScriptResult(ok=False, raw="MU3LAB_ERROR not yet")]
            ctx = scripted("dawarich", RuntimePaths(Path(tmp)), answers, "MU3LAB_INITIAL_OWNER_USERNAME=owner\n")
            rule = self._rule("dawarich")
            self.assertEqual(rule.periodic(ctx), "")
            self.assertEqual(ctx.env()["MU3LAB_INITIAL_OWNER_USERNAME"], "owner")
            ctx.answers = [ok("MU3LAB_DAWARICH_OWNER_OK"), ok("MU3LAB_DAWARICH_OWNER_OK")]
            self.assertIn("whole household", rule.periodic(ctx))
            self.assertEqual(json.loads(ctx.calls[-1][2][0])["email"], "owner@example.com")

    def test_not_signed_in_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = scripted(
                "open-webui", RuntimePaths(Path(tmp)), [ScriptResult(ok=True)], "MU3LAB_INITIAL_OWNER_USERNAME=owner\n"
            )
            self.assertEqual(self._rule("open-webui").periodic(ctx), "")
            self.assertEqual(ctx.env()["MU3LAB_INITIAL_OWNER_USERNAME"], "owner")


class RouteAndSignInTests(unittest.TestCase):
    def test_email_identity_replaces_any_client_header(self):
        block = _block(load_registry().get("beaver-habits"))
        self.assertIn("header_up X-Mu3lab-Email {http.request.header.X-Authentik-Email}", block)
        self.assertIn("copy_headers X-Authentik-Email", block)
        self.assertNotIn("X-Authentik-Username}", block)

    def test_speech_keys_skip_the_gate_only_with_a_bearer_key(self):
        block = _block(load_registry().get("litellm"))
        bypass, browser = block.split("\t\thandle {", 1)
        self.assertIn('header Authorization "Bearer *"', block)
        self.assertNotIn("forward_auth", bypass)
        self.assertIn("forward_auth", browser)

    def test_role_claim_is_its_own_scope_named_after_the_claim(self):
        text = render_oidc_blueprint(
            "box.tail1234.ts.net",
            OidcApp(
                "audiobookshelf",
                "Audiobookshelf",
                8462,
                "id",
                "secret",
                ("/cb",),
                "",
                ("abs_roles", "admin", "user", True),
            ),
        )

        class Loader(yaml.SafeLoader):
            pass

        Loader.add_multi_constructor("!", lambda loader, suffix, node: str(node.value))
        entries = yaml.load(text, Loader=Loader)["entries"]
        scope = next(e for e in entries if e.get("attrs", {}).get("scope_name") == "abs_roles")
        self.assertIn("'admin' if admin else 'user'", scope["attrs"]["expression"])
        provider = next(e for e in entries if "property_mappings" in e.get("attrs", {}))
        self.assertIn("mu3lab-audiobookshelf-roles", provider["attrs"]["property_mappings"])

    def test_form_launch_needs_its_details_and_serves_a_hashed_launch_page(self):
        with self.assertRaises(ValueError):
            Oidc.model_validate(
                {
                    "client_id": "x",
                    "redirect_paths": ["/cb"],
                    "launch_path": "/l",
                    "launch": "form_post",
                    "env": {"client_id": "A", "client_secret": "B"},
                }
            )
        handler = caddy_handler(load().get("dawarich").manifest)
        self.assertIn("handle /__mu3lab/login", handler)
        self.assertIn("form-action 'self' https://{http.request.host}", handler)
        self.assertIn('"action": "/users/auth/openid_connect"', handler)

    def test_form_launch_check_submits_the_hidden_fields(self):
        page = (
            '<form class="button_to" method="post" action="/users/auth/openid_connect"><button>Go</button>'
            '<input type="hidden" name="authenticity_token" value="tok123" /></form>'
        )
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=page)
            seen["body"] = request.content.decode()
            return httpx.Response(302, headers={"location": "https://box.test/application/o/authorize/"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            reply = _form_launch(client, "https://box.test:8465", "/users/sign_in", "/users/auth/openid_connect", "D")
        self.assertEqual(reply.status_code, 302)
        self.assertEqual(seen["body"], "authenticity_token=tok123")


class SpeechRoutingTests(unittest.TestCase):
    PARAMS = RoutingParams.model_validate(
        {
            "mode": "models",
            "config_file": "config.yaml",
            "gateway": "g",
            "runner": "r",
            "service_key_env": "S",
            "master_key_env": "M",
            "api_base_env": "A",
            "runner_api_base_env": "R",
            "embedding_model": "e",
            "embedding_provider": "ollama",
            "speech_url_env": "MU3LAB_SPEECH_URL",
        }
    )

    def test_stable_names_route_to_the_configured_models(self):
        env = {"MU3LAB_SPEECH_URL": "http://speaches:8000/v1", "MU3LAB_STT_MODEL": "w", "MU3LAB_TTS_MODEL": "k"}
        models = {item["model_name"]: item for item in speech_models(env, self.PARAMS)}
        self.assertEqual(models["mu3lab-stt"]["litellm_params"]["model"], "openai/w")
        self.assertEqual(models["mu3lab-tts"]["model_info"]["mode"], "audio_speech")

    def test_no_speech_app_means_no_speech_routes(self):
        self.assertEqual(speech_models({}, self.PARAMS), [])


class VoiceKeyTests(unittest.TestCase):
    def test_create_replaces_the_previous_key_and_limits_it_to_speech(self):
        calls: list[tuple[str, str, Any]] = []
        existing = [{"key_alias": voice_keys.alias("u1"), "created_at": "2026-10-01"}]

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else None
            calls.append((request.method, request.url.path, body))
            self.assertEqual(request.headers["authorization"], "Bearer master")
            if request.url.path == "/key/list":
                return httpx.Response(200, json={"keys": existing})
            if request.url.path == "/key/generate":
                return httpx.Response(200, json={"key": "sk-new"})
            return httpx.Response(200, json={"deleted_keys": body["key_aliases"]})

        client = LiteLLM("http://litellm.test", "master", transport=httpx.MockTransport(handler))
        with patch.object(voice_keys, "_client", return_value=client):
            self.assertEqual(voice_keys.create("u1", load(), RuntimePaths()), "sk-new")
        self.assertEqual([path for _method, path, _body in calls], ["/key/list", "/key/delete", "/key/generate"])
        generated = calls[-1][2]
        self.assertEqual(generated["models"], ["mu3lab-stt", "mu3lab-tts"])
        self.assertEqual(generated["user_id"], "u1")
