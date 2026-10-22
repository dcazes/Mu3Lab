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

import yaml

from ctl import hostinfo
from ctl.authentik_blueprints import OidcApp, render_oidc_blueprint
from ctl.control_state import ControlState
from ctl.core_wiring import RoutingParams, speech_models
from ctl.engine import rewire
from ctl.engine.compose import Compose, ScriptResult
from ctl.engine.hooks import HookContext
from ctl.engine.launch import caddy_handler
from ctl.engine.project import Facts, build_env, render
from ctl.manifest.catalog import load
from ctl.manifest.models import Oidc
from ctl.registry import load as load_registry
from ctl.routes import _block
from ctl.rules import rules_for
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
            env = self._env(paths, "nextcloud")
            self.assertEqual(env["MU3LAB_MUSIC_SHARED"], "0")
            self.assertTrue(env["MU3LAB_MUSIC_FOLDER"].endswith("/nextcloud/no-music"))

    def test_installed_provider_supplies_its_published_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            self._install(
                paths,
                "speech",
                "MU3LAB_TTS_VOICE=af_heart\nMU3LAB_STT_URL=http://speech-stt:8000/v1\nWLK_API_TOKEN=t0ken\n",
            )
            litellm = self._env(paths, "litellm")
            self.assertEqual(litellm["MU3LAB_STT_URL"], "http://speech-stt:8000/v1")
            self.assertEqual(litellm["MU3LAB_STT_KEY"], "t0ken")

    def test_removing_the_provider_removes_its_settings_on_the_next_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load()
            facts = Facts("box.test", paths, catalog)
            before = {"MU3LAB_STT_URL": "http://speech-stt:8000/v1", "MU3LAB_STT_KEY": "t", "MU3LAB_STT_MODEL": "x"}
            env = build_env(catalog.get("litellm"), facts, before, [])
            self.assertNotIn("MU3LAB_STT_URL", env)
            self.assertNotIn("MU3LAB_STT_KEY", env)
            self.assertNotIn("MU3LAB_STT_MODEL", env)

    def test_consumers_are_installed_apps_using_one_of_the_providers_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load()
            self._install(paths, "open-webui")
            # LiteLLM is core (always present); Open WebUI's chat voice has its own Whisper.
            names = {app.id for app in rewire.consumers(catalog.get("speech"), catalog, paths)}
            self.assertEqual(names, {"litellm"})
            self.assertEqual({app.id for app in rewire.consumers(catalog.get("navidrome"), catalog, paths)}, set())
            self._install(paths, "nextcloud")
            names = {app.id for app in rewire.consumers(catalog.get("navidrome"), catalog, paths)}
            self.assertEqual(names, {"nextcloud"})

    def test_rewire_restarts_only_a_running_consumer_whose_files_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load()
            facts = Facts("box.test", paths, catalog)
            render(catalog.get("nextcloud"), facts)
            with (
                patch.object(rewire, "_running", return_value=True),
                patch.object(Compose, "up", return_value=(0, "")) as up,
            ):
                self.assertFalse(rewire.rewire_one(catalog.get("nextcloud"), facts, lambda _line: None))
                self._install(paths, "navidrome")
                self.assertTrue(rewire.rewire_one(catalog.get("nextcloud"), facts, lambda _line: None))
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
                self.assertEqual(build_env(catalog.get("dawarich"), facts, {}, [])["PHOTON_REGION"], "ca")
            with patch.object(hostinfo, "country_code", return_value="zz"):
                self.assertNotIn("PHOTON_REGION", build_env(catalog.get("dawarich"), facts, {}, []))


class PerPersonAccountsTests(unittest.TestCase):
    PEOPLE: ClassVar[list[dict[str, Any]]] = [
        {"username": "sam", "name": "Sam", "email": "sam@example.com", "role": "member", "active": True},
        {"username": "owner", "name": "Owner", "email": "owner@example.com", "role": "admin", "active": True},
    ]

    def test_household_is_stable_and_audience_filters_operators(self):
        self.assertEqual([p["username"] for p in household("household", self.PEOPLE)], ["owner", "sam"])
        self.assertEqual([p["username"] for p in household("operators", self.PEOPLE)], ["owner"])

    @staticmethod
    def _set_state(paths: RuntimePaths, state: str) -> None:
        paths.runtime.mkdir(parents=True, exist_ok=True)
        ControlState.runtime(paths).set_installation("beaver-habits", state)

    def test_periodic_runs_the_script_only_when_the_household_changed(self):
        rule = rules_for(load().get("beaver-habits").manifest)[0]
        self.assertIsInstance(rule, PerPersonAccounts)
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.rules.per_person_accounts.list_people") as people:
            paths = RuntimePaths(Path(tmp))
            self._set_state(paths, "running")
            people.return_value = self.PEOPLE
            ctx = scripted("beaver-habits", paths, [ok("MU3LAB_BEAVER_PEOPLE_OK created=2")])
            self.assertIn("match the household", rule.periodic(ctx))
            sent = json.loads(ctx.calls[0][2][0])
            self.assertEqual({person["email"] for person in sent}, {"sam@example.com", "owner@example.com"})
            self.assertEqual(ctx.env()[DIGEST_ENV], digest(household("household", self.PEOPLE)))
            self.assertEqual(rule.periodic(ctx), "")  # unchanged: no script run
            self.assertEqual(len(ctx.calls), 1)

    def test_periodic_leaves_a_stopped_app_alone_until_it_runs_again(self):
        rule = rules_for(load().get("beaver-habits").manifest)[0]
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.rules.per_person_accounts.list_people") as people:
            paths = RuntimePaths(Path(tmp))
            people.return_value = self.PEOPLE
            ctx = scripted("beaver-habits", paths, [ok("MU3LAB_BEAVER_PEOPLE_OK created=2")])
            self.assertEqual(rule.periodic(ctx), "")  # not installed yet
            self._set_state(paths, "stopped")
            self.assertEqual(rule.periodic(ctx), "")
            self.assertEqual(ctx.calls, [])
            self._set_state(paths, "running")
            self.assertIn("match the household", rule.periodic(ctx))

    def test_a_failed_sync_is_retried_later_and_fails_an_install(self):
        rule = rules_for(load().get("beaver-habits").manifest)[0]
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.rules.per_person_accounts.list_people") as people:
            paths = RuntimePaths(Path(tmp))
            self._set_state(paths, "running")
            people.return_value = self.PEOPLE
            ctx = scripted("beaver-habits", paths, [ScriptResult(ok=False, error="boom"), ScriptResult(ok=False)])
            self.assertEqual(rule.periodic(ctx), "")
            self.assertNotIn(DIGEST_ENV, ctx.env())
            with self.assertRaises(Exception) as raised:
                rule.after_healthy(ctx)
            self.assertIn("could not create household accounts", str(raised.exception))


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


class SpeechRoutingTests(unittest.TestCase):
    PARAMS = RoutingParams.model_validate(
        {
            "mode": "models",
            "config_file": "config.yaml",
            "gateway": "g",
            "service_key_env": "S",
            "master_key_env": "M",
            "api_base_env": "A",
            "runner_api_base_env": "R",
            "runner_api_key_env": "RK",
            "stt_url_env": "MU3LAB_STT_URL",
            "tts_url_env": "MU3LAB_TTS_URL",
            "stt_key_env": "MU3LAB_STT_KEY",
        }
    )
    ENV: ClassVar[dict[str, str]] = {
        "MU3LAB_STT_URL": "http://speech-stt:8000/v1",
        "MU3LAB_TTS_URL": "http://speech-tts:8880/v1",
        "MU3LAB_STT_KEY": "t0ken",
        "MU3LAB_STT_MODEL": "w",
        "MU3LAB_TTS_MODEL": "k",
    }

    def test_stable_names_route_to_the_configured_models(self):
        models = {item["model_name"]: item for item in speech_models(self.ENV, self.PARAMS)}
        self.assertEqual(models["mu3lab-stt"]["litellm_params"]["model"], "openai/w")
        self.assertEqual(models["mu3lab-stt"]["litellm_params"]["api_base"], "http://speech-stt:8000/v1")
        self.assertEqual(models["mu3lab-tts"]["litellm_params"]["api_base"], "http://speech-tts:8880/v1")
        self.assertEqual(models["mu3lab-tts"]["model_info"]["mode"], "audio_speech")

    def test_the_recognition_key_is_read_from_the_environment_never_written_into_the_config(self):
        models = {item["model_name"]: item for item in speech_models(self.ENV, self.PARAMS)}
        self.assertEqual(models["mu3lab-stt"]["litellm_params"]["api_key"], "os.environ/MU3LAB_STT_KEY")
        self.assertNotIn("t0ken", str(models))
        self.assertEqual(models["mu3lab-tts"]["litellm_params"]["api_key"], "unused")

    def test_no_speech_app_means_no_speech_routes(self):
        self.assertEqual(speech_models({}, self.PARAMS), [])


