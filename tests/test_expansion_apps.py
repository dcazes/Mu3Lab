"""App-specific setup for the 2026 expansion apps (the parts that run in Mu3Lab, not in containers)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext, StepFailed, load_app_hooks
from ctl.engine.loopback import LoopbackError
from ctl.engine.project import Facts
from ctl.manifest.catalog import load
from ctl.runtime import RuntimePaths
from ctl.store import onboarding as onboarding_state

OWNER = {"owner_uid": "u-owner", "username": "owner", "email": "owner@example.com", "display_name": "Owner"}


class FakeAudiobookshelf:
    """Answers the Audiobookshelf API calls the hook makes, and records them."""

    def __init__(self, *, initialized: bool = False, methods: list[str] | None = None, password: str = "") -> None:
        self.initialized = initialized
        self.methods = methods or ["local"]
        self.password = password
        self.libraries: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.settings: dict[str, Any] = {}

    def request(self, port: int, path: str, *, method: str = "GET", data: Any = None, **_: Any) -> Any:
        self.calls.append((method, path))
        if path == "/status":
            return {"isInit": self.initialized, "authMethods": self.methods}
        if path == "/init":
            self.initialized, self.password = True, data["newRoot"]["password"]
            return None
        if path == "/login":
            if data["password"] != self.password:
                raise LoopbackError("Setup request /login failed (HTTP 401).")
            return {"user": {"type": "root", "accessToken": "token"}}
        if path == "/api/libraries" and method == "GET":
            return {"libraries": [{"name": name} for name in self.libraries]}
        if path == "/api/libraries":
            self.libraries.append(data["name"])
            return {}
        if path == "/api/auth-settings":
            self.settings = data
            self.methods = data["authActiveAuthMethods"]
            return {"updated": True}
        raise AssertionError(path)


class AudiobookshelfSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = RuntimePaths(Path(self.tmp.name))
        self.catalog = load()
        self.app = self.catalog.get("audiobookshelf")
        project = self.paths.projects / self.app.id
        project.mkdir(parents=True)
        (project / ".env").write_text("ABS_OIDC_CLIENT_ID=mu3lab-audiobookshelf\nABS_OIDC_CLIENT_SECRET=secret\n")
        self.owner = onboarding_state.remember_owner(self.app.id, OWNER, self.paths)
        self.hooks = load_app_hooks(self.app)

    def run_hook(self, fake: FakeAudiobookshelf) -> str:
        self.hooks.bootstrap_account.__globals__["request"] = fake.request
        project = self.paths.projects / self.app.id
        ctx = HookContext(
            self.app,
            project,
            Compose(project),
            lambda _line: None,
            lambda *_: None,
            self.owner,
            Facts("box.tail1234.ts.net", self.paths, self.catalog),
        )
        return self.hooks.bootstrap_account(ctx)

    def test_new_server_gets_the_owner_libraries_and_authentik_only_sign_in(self):
        fake = FakeAudiobookshelf()
        self.assertIn("Authentik only", self.run_hook(fake))
        self.assertEqual(fake.libraries, ["Audiobooks", "Podcasts"])
        settings = fake.settings
        self.assertEqual(settings["authActiveAuthMethods"], ["openid"])
        self.assertEqual(settings["authOpenIDMatchExistingBy"], "username")
        self.assertEqual(settings["authOpenIDGroupClaim"], "abs_roles")
        self.assertEqual(settings["authOpenIDSubfolderForRedirectURLs"], "/audiobookshelf")
        self.assertEqual(settings["authOpenIDClientSecret"], "secret")
        self.assertEqual(
            settings["authOpenIDIssuerURL"], "https://box.tail1234.ts.net/application/o/mu3lab-audiobookshelf/"
        )

    def test_a_configured_server_is_left_alone(self):
        fake = FakeAudiobookshelf(initialized=True, methods=["openid"])
        self.assertIn("Existing", self.run_hook(fake))
        self.assertEqual(fake.calls, [("GET", "/status")])

    def test_an_interrupted_install_resumes_with_the_saved_password(self):
        fake = FakeAudiobookshelf()
        login = onboarding_state.prepare_login(self.app.id, self.paths)
        fake.initialized, fake.password = True, login["password"]  # /init ran, then the install stopped
        self.run_hook(fake)
        self.assertNotIn(("POST", "/init"), fake.calls)
        self.assertEqual(fake.methods, ["openid"])

    def test_someone_elses_server_is_refused(self):
        fake = FakeAudiobookshelf(initialized=True, password="not-ours")
        with self.assertRaises(StepFailed) as raised:
            self.run_hook(fake)
        self.assertIn("owner Mu3Lab did not create", raised.exception.message)


class ManifestContractTests(unittest.TestCase):
    def test_internal_services_have_no_screen_and_no_sign_in(self):
        catalog = load()
        for app_id in ("speaches", "photon"):
            with self.subTest(app=app_id):
                manifest = catalog.get(app_id).manifest
                self.assertIsNone(manifest.route)
                self.assertFalse(manifest.ui.available)
                self.assertEqual(manifest.sign_in.method, "none")

    def test_owner_accounts_are_created_at_install_not_on_first_sign_in(self):
        catalog = load()
        for app_id in ("outline", "dawarich", "open-webui"):
            with self.subTest(app=app_id):
                manifest = catalog.get(app_id).manifest
                assert manifest.sign_in.oidc is not None
                self.assertEqual(manifest.sign_in.oidc.initial_owner_env, "")
                scripts = [rule.with_ for rule in manifest.rules if rule.rule == "container_script"]
                self.assertEqual(scripts[0]["at"], "after_healthy")
                self.assertIn("{{owner_json}}", scripts[0]["args"])

    def test_app_scripts_report_with_their_markers(self):
        folder = Path(__file__).resolve().parents[1] / "apps"
        markers = {
            "beaver-habits/scripts/sync-people.py": "MU3LAB_BEAVER_PEOPLE_OK",
            "outline/scripts/adopt-owner.js": "MU3LAB_OUTLINE_OWNER_OK",
            "dawarich/scripts/adopt-owner.rb": "MU3LAB_DAWARICH_OWNER_OK",
            "open-webui/scripts/adopt-owner.py": "MU3LAB_OPEN_WEBUI_OWNER_OK",
        }
        for script, marker in markers.items():
            with self.subTest(script=script):
                self.assertIn(marker, (folder / script).read_text())
