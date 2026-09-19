"""First-run state and generated AI wiring are durable and private."""

from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl.core_setup import _provision_freellmapi
from ctl.core_wiring import configure
from ctl.provider_secrets import records, save
from ctl.provisioning import ProvisioningStore
from ctl.secrets import ensure_core_envs
from ctl.runtime import RuntimePaths
from ctl.secrets import ensure_core_envs


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
            ProvisioningStore(database).update(
                "configuration", "waiting_for_user", detail="Add a provider.")
            summary = ProvisioningStore(database).summary()
        self.assertEqual(summary["waiting"]["phase_id"], "configuration")
        self.assertFalse(summary["complete"])

    def test_structured_inputs_are_redacted_before_sqlite(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "runtime" / "control-plane.sqlite3"
            ProvisioningStore(database).update(
                "configuration", "running",
                inputs={"provider": {"api_key": "must-not-persist", "label": "safe"}})
            with sqlite3.connect(database) as conn:
                stored = conn.execute("SELECT inputs_json FROM provisioning_steps WHERE phase_id = 'configuration'").fetchone()[0]
        self.assertNotIn("must-not-persist", stored)
        self.assertIn("safe", stored)

    def test_illegal_state_regression_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProvisioningStore(Path(tmp) / "runtime" / "control-plane.sqlite3")
            store.update("core", "verified")
            with self.assertRaises(ValueError):
                store.update("core", "waiting_for_user")


@unittest.skipUnless(__import__("importlib.util").util.find_spec("cryptography"),
                     "cryptography is installed by control-plane requirements")
class CoreWiringTests(unittest.TestCase):
    def test_core_environment_contract_includes_ollama(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = ensure_core_envs(Path(tmp), token_factory=lambda: "generated")
        self.assertEqual(set(paths), {"ollama", "freellmapi", "litellm", "open-webui"})

    def test_provider_key_reaches_only_private_generated_adapter_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            ensure_core_envs(paths.root, token_factory=lambda: "stable-secret")
            save("groq", "Groq provider", "user-provider-secret", paths)
            from ctl.control_state import ControlState
            ControlState(paths.runtime / "control-plane.sqlite3").set_provider(
                "groq", "Groq provider", state="verified", verified=True)
            result = configure(paths)
            self.assertTrue(result["chat_configured"])
            self.assertEqual(result["provider_count"], 1)
            self.assertEqual(records(paths)[0]["api_key"], "user-provider-secret")
            adapter = Path(result["freellmapi_config"])
            gateway = Path(result["litellm_config"])
            self.assertIn("user-provider-secret", adapter.read_text(encoding="utf-8"))
            self.assertNotIn("user-provider-secret", gateway.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(adapter.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(gateway.stat().st_mode), 0o600)
            self.assertIn("mu3lab-chat", gateway.read_text(encoding="utf-8"))
            self.assertIn("mu3lab-embed", gateway.read_text(encoding="utf-8"))

    def test_freellmapi_bootstrap_mints_one_scoped_gateway_key(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            ensure_core_envs(paths.root, token_factory=lambda: "stable-secret")
            responses = iter([
                (201, {"token": "dashboard-session"}),
                (200, []),
                (201, {"key": "sk-cp-private-gateway-key"}),
            ])
            with patch("ctl.core_setup._http_json", side_effect=lambda *args, **kwargs: next(responses)):
                ok, _detail = _provision_freellmapi(paths)
            self.assertTrue(ok)
            from ctl.secrets import read_runtime_env
            env = read_runtime_env(paths.projects / "freellmapi" / ".env")
            self.assertEqual(env["FREELLMAPI_SERVICE_KEY"], "sk-cp-private-gateway-key")

    def test_freellmapi_never_mints_a_second_key_after_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            ensure_core_envs(paths.root, token_factory=lambda: "stable-secret")
            env_path = paths.projects / "freellmapi" / ".env"
            env_path.write_text(env_path.read_text(encoding="utf-8") + "FREELLMAPI_SERVICE_KEY=sk-cp-existing\n", encoding="utf-8")
            ok, detail = _provision_freellmapi(paths)
            self.assertTrue(ok)
            self.assertIn("already exists", detail)

    def test_freellmapi_uses_container_loopback_for_first_setup(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            ensure_core_envs(paths.root, token_factory=lambda: "stable-secret")
            responses = iter([
                (403, {"error": {"type": "setup_code_required"}}),
                (200, []),
                (201, {"key": "sk-cp-private-gateway-key"}),
            ])
            local = (0, {"status": 201, "body": {"token": "dashboard-session"}})
            with patch("ctl.core_setup._http_json", side_effect=lambda *args, **kwargs: next(responses)), \
                 patch("ctl.core_setup.actions.freellmapi_local_setup", return_value=local) as setup:
                ok, _detail = _provision_freellmapi(paths)
            self.assertTrue(ok)
            setup.assert_called_once()
