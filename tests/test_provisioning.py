"""First-run state and generated AI wiring are durable and private."""

from __future__ import annotations

import json
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl import mcp_ops
from ctl.control_state import ControlState
from ctl.core_setup import core_services, execute_claimed, plan
from ctl.core_wiring import configure
from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext, StepFailed, load_app_hooks
from ctl.engine.project import Facts
from ctl.jobs import JobStore
from ctl.provider_secrets import records, save
from ctl.provisioning import ProvisioningStore
from ctl.registry import load
from ctl.rules import rules_for
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from tests.support import render_core_projects


class ProvisioningStoreTests(unittest.TestCase):
    def test_survives_reopen_and_never_stores_simple_secret_assignments(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "runtime" / "control-plane.sqlite3"
            first = ProvisioningStore(database)
            first.update("core", "running", detail="api_key=must-not-persist")
            second = ProvisioningStore(database)
            summary = second.summary()
            core = next(item for item in summary["phases"] if item["phase_id"] == "core")
            self.assertEqual(core["actual_state"], "running")
            self.assertNotIn("must-not-persist", core["detail"])
            self.assertEqual(core["attempts"], 1)

    def test_waiting_phase_is_visible_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "runtime" / "control-plane.sqlite3"
            ProvisioningStore(database).update("configuration", "waiting_for_user", detail="Add a provider.")
            summary = ProvisioningStore(database).summary()
        self.assertEqual(summary["waiting"]["phase_id"], "configuration")
        self.assertFalse(summary["complete"])

    def test_structured_inputs_are_redacted_before_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "runtime" / "control-plane.sqlite3"
            ProvisioningStore(database).update(
                "configuration", "running", inputs={"provider": {"api_key": "must-not-persist", "label": "safe"}}
            )
            with sqlite3.connect(database) as conn:
                stored = conn.execute(
                    "SELECT inputs_json FROM provisioning_steps WHERE phase_id = 'configuration'"
                ).fetchone()[0]
        self.assertNotIn("must-not-persist", stored)
        self.assertIn("safe", stored)

    def test_illegal_state_regression_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProvisioningStore(Path(tmp) / "runtime" / "control-plane.sqlite3")
            store.update("core", "verified")
            with self.assertRaises(ValueError):
                store.update("core", "waiting_for_user")

    def test_progress_and_next_action_cover_all_eight_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProvisioningStore(Path(tmp) / "runtime" / "control-plane.sqlite3")
            for phase in ("foundation", "vaultwarden", "tailscale", "identity", "dashboard_protection", "core"):
                store.update(phase, "verified")
            summary = store.summary()
        self.assertEqual(summary["progress"], {"completed": 6, "total": 8})
        self.assertEqual(summary["next_action"]["href"], "/settings/ai")


@unittest.skipUnless(
    __import__("importlib.util").util.find_spec("cryptography"),
    "cryptography is installed by control-plane requirements",
)
class CoreWiringTests(unittest.TestCase):
    def test_core_projects_generate_stable_private_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            render_core_projects(paths)
            encryption = read_runtime_env(paths.projects / "freellmapi" / ".env")["ENCRYPTION_KEY"]
            render_core_projects(paths)
            self.assertEqual(read_runtime_env(paths.projects / "freellmapi" / ".env")["ENCRYPTION_KEY"], encryption)
            self.assertEqual(len(bytes.fromhex(encryption)), 32)
            self.assertTrue((paths.projects / "ollama" / "docker-compose.yml").is_file())

    def test_provider_key_reaches_only_private_generated_adapter_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            render_core_projects(paths)
            save("groq", "Groq provider", "user-provider-secret", paths)
            ControlState(paths.runtime / "control-plane.sqlite3").set_provider(
                "groq", "Groq provider", state="verified", verified=True
            )
            result = configure(paths)
            self.assertTrue(result["chat_configured"])
            self.assertEqual(result["provider_count"], 1)
            self.assertEqual(records(paths)[0]["api_key"], "user-provider-secret")
            adapter = paths.projects / "freellmapi" / "freellmapi.config.json"
            gateway = paths.projects / "litellm" / "config.yaml"
            self.assertIn("user-provider-secret", adapter.read_text(encoding="utf-8"))
            self.assertNotIn("user-provider-secret", gateway.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(adapter.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(gateway.stat().st_mode), 0o600)
            self.assertIn("mu3lab-chat", gateway.read_text(encoding="utf-8"))
            self.assertIn("mu3lab-embed", gateway.read_text(encoding="utf-8"))

    def gateway_context(self, paths):
        registry = load()
        app = registry.catalog.get("freellmapi")
        return HookContext(
            app,
            paths.projects / app.id,
            Compose(paths.projects / app.id),
            lambda _: None,
            lambda *_: None,
            facts=Facts("test.host", paths, registry.catalog),
        )

    def test_freellmapi_bootstrap_mints_one_scoped_gateway_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            render_core_projects(paths)
            ctx = self.gateway_context(paths)
            ctx.set_env({"FREELLMAPI_SERVICE_KEY": ""})
            hooks = load_app_hooks(ctx.app)
            with patch.dict(
                hooks.bootstrap_account.__globals__,
                request=Mock(side_effect=[{"token": "dashboard-session"}, [], {"key": "sk-cp-private-gateway-key"}]),
            ):
                hooks.bootstrap_account(ctx)
            self.assertEqual(ctx.env()["FREELLMAPI_SERVICE_KEY"], "sk-cp-private-gateway-key")
            self.assertEqual(ctx.env()["MU3LAB_ADMIN_BOOTSTRAPPED"], "true")

    def test_freellmapi_never_mints_a_second_key_after_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            render_core_projects(paths)
            ctx = self.gateway_context(paths)
            hooks = load_app_hooks(ctx.app)
            api = Mock()
            with patch.dict(hooks.bootstrap_account.__globals__, request=api):
                detail = hooks.bootstrap_account(ctx)
            self.assertIn("already saved", detail)
            api.assert_not_called()

    def test_freellmapi_missing_profile_key_fails_without_minting_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            render_core_projects(paths)
            ctx = self.gateway_context(paths)
            ctx.set_env({"FREELLMAPI_SERVICE_KEY": ""})
            hooks = load_app_hooks(ctx.app)
            api = Mock(side_effect=[{"token": "session"}, [{"name": "Mu3Lab LiteLLM"}]])
            with patch.dict(hooks.bootstrap_account.__globals__, request=api), self.assertRaises(StepFailed) as failed:
                hooks.bootstrap_account(ctx)
            self.assertEqual(failed.exception.code, "client_credential_incomplete")
            self.assertEqual(api.call_count, 2)

    def test_first_admin_config_is_removed_after_http_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            render_core_projects(paths)
            ctx = self.gateway_context(paths)
            ctx.set_env({"MU3LAB_ADMIN_BOOTSTRAPPED": "false", "FREELLMAPI_SERVICE_KEY": ""})
            rule = rules_for(ctx.app.manifest)[0]
            rule.before_start(ctx)
            config = ctx.project / "freellmapi.config.json"
            self.assertEqual(
                json.loads(config.read_text())["admin"]["password"], ctx.env()["FREELLMAPI_ADMIN_PASSWORD"]
            )
            hooks = load_app_hooks(ctx.app)
            api = Mock(side_effect=[{"token": "session"}, [], {"key": "sk-cp-created"}])
            with patch.dict(hooks.bootstrap_account.__globals__, request=api):
                hooks.bootstrap_account(ctx)
            self.assertEqual(api.call_args_list[0].args[1], "/api/auth/login")
            rule.after_healthy(ctx)
            self.assertNotIn("admin", json.loads(config.read_text()))
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_blocked_core_suite_says_why(self):
        refused = {"ok": False, "reasons": ["Docker is not ready"]}
        with patch("ctl.core_setup.capacity", return_value=refused):
            checked = plan(Path(__file__).resolve().parents[1])
        self.assertFalse(checked["ready"])
        self.assertEqual(checked["error"], "Docker is not ready")

    def test_core_order_includes_firecrawl_and_respects_manifest_dependencies(self):
        ordered = [service.id for service in core_services()]
        self.assertEqual(set(ordered), {"ollama", "freellmapi", "litellm", "lobehub", "firecrawl"})
        self.assertLess(ordered.index("ollama"), ordered.index("litellm"))
        self.assertLess(ordered.index("freellmapi"), ordered.index("litellm"))
        self.assertLess(ordered.index("litellm"), ordered.index("lobehub"))

    def test_core_parent_waits_for_all_apps_and_stops_at_an_app_failure(self):
        ordered = [service.id for service in core_services()]
        for failed_app in (None, ordered[1]):
            with self.subTest(failed_app=failed_app), tempfile.TemporaryDirectory() as tmp:
                store = JobStore(Path(tmp) / "jobs.sqlite3")
                parent = store.create(kind="lifecycle", service_id="core-suite", action="install", actor="owner")
                claimed = store.claim("worker")
                calls = []

                def install_app(
                    job_store,
                    _control,
                    job,
                    service,
                    _registry,
                    _actor,
                    _root,
                    *,
                    complete_job,
                    parent=parent,
                    calls=calls,
                    failed_app=failed_app,
                ):
                    self.assertFalse(complete_job)
                    self.assertEqual(job["id"], parent["id"])
                    self.assertEqual(job_store.get(parent["id"])["state"], "running")
                    calls.append(service.id)
                    if service.id == failed_app:
                        job_store.transition(parent["id"], "failed", actor="owner", detail="app failed")
                        return False
                    return True

                with (
                    patch("ctl.core_setup.ProvisioningStore.runtime", return_value=None),
                    patch("ctl.core_setup.ControlState.runtime", return_value=None),
                    patch("ctl.core_setup.plan", return_value={"ready": True}),
                    patch("ctl.core_setup.run_install", side_effect=install_app),
                    patch("ctl.core_setup.configure_wiring", return_value={"chat_configured": False}),
                    patch("ctl.core_setup._verify_platform", return_value=(False, "Add a provider")) as verify,
                ):
                    execute_claimed(store, claimed, "worker", Path(tmp))
                if failed_app:
                    self.assertEqual(calls, ordered[:2])
                    self.assertEqual(store.get(parent["id"])["state"], "failed")
                    verify.assert_not_called()
                else:
                    self.assertEqual(calls, ordered)
                    self.assertEqual(store.get(parent["id"])["state"], "waiting_for_confirmation")
                    verify.assert_called_once()
                self.assertEqual(len(store.jobs()), 1)

    def test_preenable_switches_on_the_preferred_mcp_but_respects_later_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            with (
                patch("ctl.mcp_ops.ControlState.runtime", return_value=state),
                patch("ctl.mcp_ops._materialize") as materialize,
            ):
                mcp_ops.preenable("firecrawl", Path(tmp))
                runtime = state.mcp_server("firecrawl-official")
                self.assertTrue(runtime["enabled"])
                self.assertEqual(runtime["state"], "prepared")
                state.set_mcp_server("firecrawl-official", "firecrawl", enabled=False, state="disabled")
                mcp_ops.preenable("firecrawl", Path(tmp))
            materialize.assert_called_once()
            self.assertFalse(state.mcp_server("firecrawl-official")["enabled"])

    def test_verification_only_job_never_pulls_or_recreates_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "control.sqlite3")
            queued = store.create(kind="verification", service_id="core-suite", action="verify", actor="owner")
            claimed = store.claim("worker")
            self.assertEqual(claimed["id"], queued["id"])
            with (
                patch("ctl.core_setup.ProvisioningStore.runtime", return_value=None),
                patch("ctl.core_setup.configure_wiring", return_value={}),
                patch("ctl.core_setup._verify_platform", return_value=(True, "verified")),
                patch("ctl.engine.install.download_images") as pull,
                patch("ctl.engine.install.run_install") as up,
            ):
                execute_claimed(store, claimed, "worker", Path(tmp))
            self.assertEqual(store.get(queued["id"])["state"], "succeeded")
            pull.assert_not_called()
            up.assert_not_called()
