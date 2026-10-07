"""Manifest-driven installs preserve setup order, ownership and retry boundaries."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from ctl.control_state import ControlState
from ctl.engine import install
from ctl.engine.compose import ScriptResult
from ctl.integrations.authentik import AuthentikError
from ctl.jobs import JobStore
from ctl.manifest.catalog import Catalog
from ctl.registry import Registry, load, service_from
from ctl.rules import Params, Rule, rules_for
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.store import onboarding as onboarding_state

OWNER = {"owner_uid": "owner", "username": "owner@test", "email": "owner@test", "display_name": "Owner"}


class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = RuntimePaths(self.root / "runtime")
        self.calls = []
        self.starts = []
        self.start_results = []
        self.script_results = []
        self.exec_result = (0, "{}")
        self.images = {"app": "example/app@sha256:" + "a" * 64}
        test = self

        class FakeCompose:
            def __init__(self, directory, gpu_mode="cpu"):
                self.directory = directory
                self.gpu_mode = gpu_mode

            def validate(self, log):
                test.calls.append("validate")
                return 0, ""

            def up(self, log, **kwargs):
                test.calls.append("start")
                test.starts.append(kwargs)
                return test.start_results.pop(0) if test.start_results else (0, "")

            def exec(self, service, argv, log, **kwargs):
                return test.exec_result

            def run_script(self, service, interpreter, script, log, **kwargs):
                test.calls.append("script")
                return test.script_results.pop(0) if test.script_results else ScriptResult(True)

        self.fake_compose = FakeCompose
        patches = {
            "RuntimePaths": self.paths,
            "tailnet_dns_name": "host.test",
            "resolved_mode": "cpu",
            "missing_required": [],
            "workflow_secrets.job_identity": OWNER,
            "download_images": (0, ""),
            "app_releases.pin": self.images,
            "wait_healthy": (True, "healthy"),
            "apply_route": (True, "route ready"),
            "verify_sign_in": "sign-in verified",
            "sync_agents": (True, "ready"),
            "preenable": None,
            "sync_application": True,
            "Authentik.runtime": Mock(),
            "sync_sign_in": [],
            "wait_for_provider": {},
            "_append_runtime_diagnostics": None,
        }
        self.mocks = {}
        for key, value in patches.items():
            patcher = patch("ctl.engine.install." + key, return_value=value)
            self.mocks[key] = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("ctl.engine.install.Compose", FakeCompose)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("ctl.engine.project.hostinfo.timezone", return_value="Etc/UTC")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.paths.runtime.mkdir(parents=True)
        self.store = JobStore(self.paths.runtime / "control.sqlite")
        self.state = ControlState(self.paths.runtime / "control.sqlite")
        self.configure()

    def configure(self, *, rules=(), account="none", tier="optional", env=None, sign_in=None, secrets=()):
        folder = self.root / "apps" / "example-app"
        folder.mkdir(parents=True, exist_ok=True)
        data = dict(
            id="example-app",
            name="Example app",
            tier=tier,
            group="apps",
            category="test",
            tagline="Test",
            summary="Test app",
            version="1.0",
            upstream="example/app",
            service={"local_port": 19981, "start_timeout_seconds": 345},
            account={"mode": account},
            sign_in=sign_in or {"method": "none"},
            rules=list(rules),
            env=env or {},
            secrets=list(secrets),
        )
        (folder / "app.yaml").write_text(yaml.safe_dump(data))
        (folder / "docker-compose.yml").write_text(
            yaml.safe_dump({"name": "mu3lab-example-app", "services": {"app": {"image": self.images["app"]}}})
        )
        (folder / "docker-compose.bootstrap.yml").write_text("services: {}\n")
        (folder / "scripts").mkdir(exist_ok=True)
        (folder / "scripts/setup.sh").write_text("# reviewed setup script\n")
        self.registry = load(self.root / "apps")
        self.app = self.registry.catalog.get("example-app")
        self.service = self.registry.get(self.app.id)

    def run_install(self):
        job = self.store.create(kind="lifecycle", service_id=self.app.id, action="install", actor="owner")
        claimed = self.store.claim("worker")
        install.run_install(self.store, self.state, claimed, self.service, self.registry, "owner", self.root)
        return self.store.get(job["id"])

    def test_stage_order_and_success_projection(self):
        trace = []

        class TraceRule(Rule):
            def prepare_env(self, app, env, owner):
                trace.append("prepare")

            def before_start(self, ctx):
                trace.append("before")

            def plan_start(self, ctx, plan):
                trace.append("plan")

            def after_start(self, ctx):
                trace.append("after_start")

            def after_healthy(self, ctx):
                trace.append("after_healthy")

        with patch("ctl.engine.install.rules_for", return_value=[TraceRule(Params())]):
            job = self.run_install()
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(trace, ["prepare", "before", "plan", "after_start", "after_healthy"])
        stages = [e["detail"].split(":", 1)[0] for e in self.store.events(job["id"]) if e["event"] == "stage"]
        self.assertEqual(
            stages,
            [
                "validate_service",
                "materialize_runtime",
                "validate_configuration",
                "pull_images",
                "resolve_digests",
                "start_service",
                "verify_application",
                "configure_route",
                "verify_sign_in",
                "verify_sign_in",
                "finalize",
            ],
        )
        self.assertEqual(self.state.installation(self.app.id)["state"], "running")
        self.assertTrue(onboarding_state.read(self.app.id, self.paths)["config_version"])

    def first_admin_rules(self):
        return [
            {
                "rule": "staged_first_start",
                "with": {
                    "first": ["db", "app"],
                    "then": {
                        "service": "app",
                        "script": "scripts/setup.sh",
                        "interpreter": ["sh"],
                        "purpose": "Initialize database",
                    },
                },
            },
            {
                "rule": "first_admin_from_env",
                "with": {
                    "override": "docker-compose.bootstrap.yml",
                    "fresh_when_empty": "db",
                    "verify": {"script": {"service": "app", "script": "scripts/setup.sh", "interpreter": ["sh"]}},
                },
            },
        ]

    def test_adventurelog_keeps_naming_its_owner_so_no_default_admin_appears(self):
        # AdventureLog creates admin/admin whenever its admin settings are missing after first start.
        app = load().catalog.get("adventurelog")
        rule = next(rule for rule in rules_for(app.manifest) if rule.name == "first_admin_from_env")
        owner = {"owner_uid": "u1", "email": "owner@example.test", "username": "owner", "display_name": "Owner"}
        env: dict[str, str] = {}
        rule.prepare_env(app, env, owner)
        self.assertEqual(env, {"MU3LAB_OWNER_USERNAME": "owner", "MU3LAB_OWNER_EMAIL": "owner@example.test"})
        rule.prepare_env(app, env, {**owner, "username": "someone-else"})
        self.assertEqual(env["MU3LAB_OWNER_USERNAME"], "owner")
        compose = yaml.safe_load((app.folder / "docker-compose.yml").read_text())
        settings = compose["services"]["app"]["environment"]
        self.assertIn("MU3LAB_OWNER_USERNAME", settings["DJANGO_ADMIN_USERNAME"])
        self.assertIn("MU3LAB_OWNER_FALLBACK_PASSWORD", settings["DJANGO_ADMIN_PASSWORD"])

    def test_staged_bootstrap_final_start_drops_secrets_overrides_and_service_filter(self):
        self.configure(rules=self.first_admin_rules(), account="environment_bootstrap")
        job = self.run_install()
        self.assertEqual(job["state"], "succeeded")
        first, final = self.starts
        self.assertIsNone(first["wait_seconds"])
        self.assertEqual(first["services"], ["db", "app"])
        self.assertTrue(first["env"]["MU3LAB_BOOTSTRAP_PASSWORD"])
        self.assertEqual(final, {"wait_seconds": 345, "recreate": True})
        self.assertNotIn("MU3LAB_BOOTSTRAP_PASSWORD", read_runtime_env(self.paths.projects / self.app.id / ".env"))
        self.assertNotIn(first["env"]["MU3LAB_BOOTSTRAP_PASSWORD"], str(self.store.events(job["id"])))

    def test_bootstrap_failure_still_removes_first_start_settings(self):
        self.configure(rules=self.first_admin_rules(), account="environment_bootstrap")
        self.script_results = [ScriptResult(True), ScriptResult(False, error="account missing")]
        job = self.run_install()
        self.assertEqual(job["error_code"], "account_verification_failed")
        self.assertEqual(self.starts[-1], {"wait_seconds": 345, "recreate": True})
        self.mocks["apply_route"].assert_not_called()

    def test_existing_database_does_not_get_another_first_administrator(self):
        self.configure(rules=self.first_admin_rules(), account="environment_bootstrap")
        folder = self.paths.data / self.app.id / "db"
        folder.mkdir(parents=True)
        (folder / "existing").write_text("existing database")
        self.assertEqual(self.run_install()["state"], "succeeded")
        self.assertFalse(self.starts[0]["env"])
        self.assertEqual(self.state.initialization(self.app.id)["state"], "existing_account")

    def test_app_status_allows_retry_with_unfinished_database_but_keeps_installed_owner(self):
        rules = self.first_admin_rules()
        rules[1]["with"]["existing_account_check"] = {
            "service": "app",
            "command": ["app", "status"],
            "field": "installed",
        }
        self.configure(rules=rules, account="environment_bootstrap")
        folder = self.paths.data / self.app.id / "db"
        folder.mkdir(parents=True)
        (folder / "allocated-before-migration").write_text("unfinished")
        self.exec_result = (0, '{"installed": false}')
        self.assertEqual(self.run_install()["state"], "succeeded")
        self.assertTrue(self.starts[0]["env"])
        self.exec_result = (0, '{"installed": true}')
        self.assertEqual(self.run_install()["state"], "succeeded")
        self.assertFalse(self.starts[2]["env"])

    def test_unhealthy_dependency_retries_without_recreating_and_does_not_loop(self):
        self.state.set_installation(self.app.id, "failed")
        self.start_results = [(1, "dependency unhealthy"), (0, "")]
        self.assertEqual(self.run_install()["state"], "succeeded")
        self.assertTrue(self.starts[0]["recreate"])
        self.assertFalse(self.starts[1]["recreate"])
        self.start_results = [(1, "dependency unhealthy"), (1, "dependency unhealthy")]
        job = self.run_install()
        self.assertEqual(job["error_code"], "dependency_unhealthy")
        self.assertEqual(len(self.starts), 4)

    def test_other_start_failures_are_not_retried(self):
        self.start_results = [(1, "database authentication failed for user")]
        self.assertEqual(self.run_install()["error_code"], "database_auth_failed")
        self.assertEqual(len(self.starts), 1)

    def test_owner_required_by_account_or_rule_before_materializing(self):
        self.mocks["workflow_secrets.job_identity"].return_value = None
        for account, rules in [("api_bootstrap", []), ("none", self.first_admin_rules())]:
            self.configure(account=account, rules=rules)
            self.assertEqual(self.run_install()["error_code"], "identity_email_missing")
        self.assertFalse((self.paths.projects / self.app.id).exists())
        self.assertEqual(self.calls, [])

    def test_existing_owner_is_preserved_when_another_operator_retries(self):
        onboarding_state.remember_owner(self.app.id, OWNER, self.paths)
        self.mocks["workflow_secrets.job_identity"].return_value = dict(OWNER, owner_uid="other", email="other@test")
        self.assertEqual(self.run_install()["state"], "succeeded")
        self.assertEqual(onboarding_state.read(self.app.id, self.paths)["owner"], OWNER)

    def test_configuration_and_core_preflight_rejections_do_not_start(self):
        self.mocks["missing_required"].return_value = ["setting"]
        self.assertEqual(self.run_install()["error_code"], "configuration_required")
        self.configure(tier="foundation")
        self.assertEqual(self.run_install()["error_code"], "install_not_supported")
        self.assertEqual(self.calls, [])

    def test_core_uses_shared_steps_and_leaves_parent_job_running(self):
        self.configure(tier="core")
        job = self.store.create(kind="lifecycle", service_id="core-suite", action="install", actor="owner")
        claimed = self.store.claim("worker")
        self.store.transition(job["id"], "running", actor="owner", detail="Core setup")
        result = install.run_install(
            self.store, self.state, claimed, self.service, self.registry, "owner", self.root, complete_job=False
        )
        self.assertTrue(result)
        self.assertEqual(self.store.get(job["id"])["state"], "running")
        self.assertEqual(self.state.installation(self.app.id)["state"], "running")
        self.assertEqual(self.calls, ["validate", "start"])

    def test_script_outputs_are_saved_privately_and_never_logged(self):
        self.configure(
            rules=[
                {
                    "rule": "container_script",
                    "with": {
                        "at": "after_start",
                        "service": "app",
                        "script": "scripts/setup.sh",
                        "interpreter": ["sh"],
                        "purpose": "Store app token",
                    },
                }
            ]
        )
        self.script_results = [ScriptResult(True, {"APP_TOKEN": "private-token", "not_settings": "ignore"})]
        job = self.run_install()
        self.assertEqual(job["state"], "succeeded")
        path = self.paths.projects / self.app.id / ".env"
        self.assertEqual(read_runtime_env(path)["APP_TOKEN"], "private-token")
        self.assertNotIn("not_settings", read_runtime_env(path))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private-token", str(self.store.events(job["id"])))

    def test_password_login_is_disabled_after_account_bootstrap_and_templates_rerendered(self):
        self.configure(
            account="api_bootstrap",
            secrets=[{"env": "PASSWORD_LOGIN", "kind": "fixed", "value": "true"}],
            rules=[{"rule": "password_login_off_after_setup", "with": {"env": "PASSWORD_LOGIN"}}],
        )
        (self.app.folder / "settings.json.tmpl").write_text('{"enabled": {{PASSWORD_LOGIN}}}')
        hook = Mock()
        hook.bootstrap_account.side_effect = lambda ctx: self.calls.append("bootstrap") or "Owner verified"
        with patch("ctl.engine.install.load_app_hooks", return_value=hook):
            self.assertEqual(self.run_install()["state"], "succeeded")
        self.assertLess(self.calls.index("bootstrap"), len(self.calls) - 1)
        self.assertEqual(self.starts[-1], {"wait_seconds": 345, "recreate": True})
        self.assertEqual((self.paths.projects / self.app.id / "settings.json").read_text(), '{"enabled": false}')

    def test_oidc_is_registered_before_start_and_ready_identity_discards_password(self):
        sign_in = {
            "method": "oidc",
            "oidc": {
                "client_id": "example",
                "redirect_paths": ["/callback"],
                "launch_path": "/login",
                "env": {"client_id": "OIDC_ID", "client_secret": "OIDC_SECRET"},
            },
        }
        self.configure(sign_in=sign_in)
        onboarding_state.remember_owner(self.app.id, OWNER, self.paths)
        onboarding_state.prepare_login(self.app.id, self.paths)
        self.mocks["wait_for_provider"].side_effect = lambda *_: self.calls.append("provider")
        self.assertEqual(self.run_install()["state"], "succeeded")
        self.assertLess(self.calls.index("provider"), self.calls.index("start"))
        self.assertEqual(self.state.service_identity(self.app.id)["state"], "ready")
        self.assertFalse(onboarding_state.read(self.app.id, self.paths).get("password"))

    def test_provider_failure_does_not_start_and_signin_failure_never_marks_ready(self):
        self.configure(
            sign_in={
                "method": "oidc",
                "oidc": {
                    "client_id": "example",
                    "redirect_paths": ["/callback"],
                    "launch_path": "/login",
                    "env": {"client_id": "OIDC_ID", "client_secret": "OIDC_SECRET"},
                },
            }
        )
        self.mocks["sync_sign_in"].side_effect = AuthentikError("unavailable")
        self.assertEqual(self.run_install()["error_code"], "sign_in_unavailable")
        self.assertEqual(self.starts, [])
        self.mocks["sync_sign_in"].side_effect = None
        self.mocks["verify_sign_in"].side_effect = install.SignInError("no redirect")
        self.assertEqual(self.run_install()["error_code"], "sign_in_failed")
        self.assertEqual(self.state.installation(self.app.id)["state"], "failed")

    def guard_context(self):
        app = load().catalog.get("actual-budget")
        rules = rules_for(app.manifest)
        ctx = install.context(
            app, install.Facts("host.test", self.paths, Catalog((app,))), rules, OWNER, lambda _: None, lambda *_: None
        )
        ctx.project.mkdir(parents=True, exist_ok=True)
        ctx.set_env({"MU3LAB_INITIAL_OWNER_USERNAME": "owner"})
        database = self.paths.data / app.id / "server-files/account.sqlite"
        database.parent.mkdir(parents=True)
        with sqlite3.connect(database) as conn:
            conn.execute("CREATE TABLE users (id TEXT,user_name TEXT,role TEXT,enabled INTEGER,owner INTEGER)")
            conn.execute("CREATE TABLE sessions (user_id TEXT,auth_method TEXT,expires_at INTEGER)")
            conn.execute("INSERT INTO users VALUES ('1','owner','ADMIN',1,1)")
            conn.execute("INSERT INTO sessions VALUES ('1','password',-1)")
        return ctx, rules[0], database

    def test_owner_guard_requires_admin_owner_and_live_oidc_session_then_lifts_once(self):
        ctx, rule, database = self.guard_context()
        with patch("ctl.rules.initial_owner_guard.time.time", return_value=1000):
            self.assertFalse(rule.periodic(ctx))
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE sessions SET auth_method='openid',expires_at=999")
            self.assertFalse(rule.periodic(ctx))
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE sessions SET expires_at=-1")
                conn.execute("UPDATE users SET owner=0")
            self.assertFalse(rule.periodic(ctx))
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE users SET owner=1")
            self.assertIn("whole household", rule.periodic(ctx))
            self.assertFalse(rule.periodic(ctx))
        self.mocks["sync_sign_in"].assert_called_once()
        self.assertEqual(self.mocks["sync_sign_in"].call_args.kwargs["app_id"], "actual-budget")

    def test_owner_guard_restores_restriction_if_authentik_update_fails(self):
        ctx, rule, database = self.guard_context()
        with sqlite3.connect(database) as conn:
            conn.execute("UPDATE sessions SET auth_method='openid'")
        self.mocks["sync_sign_in"].side_effect = AuthentikError("retry later")
        with self.assertRaises(AuthentikError):
            rule.periodic(ctx)
        self.assertEqual(ctx.env()["MU3LAB_INITIAL_OWNER_USERNAME"], "owner")
        self.mocks["sync_sign_in"].side_effect = None
        self.assertIn("whole household", rule.periodic(ctx))

    def test_periodic_runs_only_installed_apps_and_continues_after_one_failure(self):
        app2 = self.app.__class__(self.app.manifest.model_copy(update={"id": "second-app"}), self.app.folder)
        catalog = Catalog((self.app, app2))
        registry = Registry(tuple(service_from(a.manifest) for a in catalog.apps), catalog)
        rule = Mock()
        rule.periodic.side_effect = [AuthentikError("deferred"), "maintenance complete"]
        with (
            patch("ctl.engine.install.load", return_value=registry),
            patch("ctl.engine.install.rules_for", return_value=[rule]),
            patch("ctl.engine.install.installed", side_effect=[True, False]),
        ):
            install.run_periodic(self.store, self.root, lambda _: None)
        self.assertEqual(rule.periodic.call_count, 1)
        rule.periodic.side_effect = [AuthentikError("deferred"), "maintenance complete"]
        with (
            patch("ctl.engine.install.load", return_value=registry),
            patch("ctl.engine.install.rules_for", return_value=[rule]),
            patch("ctl.engine.install.installed", return_value=True),
        ):
            install.run_periodic(self.store, self.root, lambda _: None)
        self.assertEqual(rule.periodic.call_count, 3)
