"""Curated provider and SurfSense MCP MVP contracts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.control_state import ControlState
from ctl.core_wiring import configure
from ctl.provider_catalog import catalog, get, prefix_warning
from ctl.provider_secrets import save
from ctl.jobs import JobStore
from ctl.runtime import RuntimePaths
from ctl.secrets import ensure_core_envs


class ProviderCatalogTests(unittest.TestCase):
    def test_exact_curated_provider_ids(self):
        self.assertEqual([item["id"] for item in catalog()], [
            "cerebras", "google", "groq", "huggingface", "nvidia",
            "openrouter", "mistral", "zhipu",
        ])

    def test_prefix_is_advisory_and_arbitrary_provider_is_rejected(self):
        self.assertIn("usual gsk_", prefix_warning("groq", "unusual-key"))
        self.assertEqual(prefix_warning("groq", "gsk_valid-shape"), "")
        with self.assertRaisesRegex(ValueError, "curated catalog"):
            get("arbitrary")

    def test_only_verified_enabled_connection_reaches_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            ensure_core_envs(paths.root, token_factory=lambda: "stable-secret")
            save("groq", "Groq", "gsk_private", paths)
            state = ControlState(paths.runtime / "control-plane.sqlite3")
            state.set_provider("groq", "Groq", state="degraded")
            self.assertEqual(configure(paths)["provider_count"], 0)
            state.set_provider("groq", "Groq", state="verified", verified=True)
            self.assertEqual(configure(paths)["provider_count"], 1)
            state.set_provider("groq", "Groq", enabled=False, state="disabled")
            self.assertEqual(configure(paths)["provider_count"], 0)

    def test_successful_provider_verification_queues_verification_only_job(self):
        from ctl.provider_ops import execute_claimed
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "control.sqlite3"
            state = ControlState(database)
            state.set_provider("groq", "Groq", state="verifying")
            store = JobStore(database)
            core = store.create(kind="lifecycle", service_id="core-suite", action="install", actor="owner")
            store.transition(core["id"], "waiting_for_confirmation", actor="owner", detail="provider required")
            provider = store.create(kind="wiring", service_id="provider:groq", action="verify", actor="owner")
            claimed = store.claim("worker")
            self.assertEqual(claimed["id"], provider["id"])
            from ctl.provider_ops import StreamProbe
            with patch("ctl.provider_ops.ControlState.runtime", return_value=state), \
                 patch("ctl.provider_ops._verify", return_value=StreamProbe(
                     True, 200, "groq/model-a", "model-a", "", "passed")), \
                 patch("ctl.provider_ops._reconcile", return_value=(True, "ready", [])):
                execute_claimed(store, claimed, "worker", Path(tmp))
            jobs = store.jobs(limit=10)
            self.assertTrue(any(item["service_id"] == "core-suite" and item["state"] == "queued"
                                and item["action"] == "verify" for item in jobs))
            self.assertTrue(any(item["id"] == core["id"] and item["state"] == "cancelled"
                                for item in jobs))

    def test_model_catalog_does_not_infer_provider_from_owned_by(self):
        from ctl.provider_ops import _available_models
        payload = {"data": [
            {"id": "model-a", "owned_by": "freellmapi", "available": True},
            {"id": "model-b", "owned_by": "freellmapi", "available": True},
            {"id": "model-c", "owned_by": "groq", "available": False},
        ]}
        with patch("ctl.provider_ops._request_json", return_value=(200, payload)):
            self.assertEqual(_available_models("internal-key"), ["model-a", "model-b"])

    def test_groq_verification_uses_routed_via_not_owned_by(self):
        from ctl.provider_ops import StreamProbe, _verify
        with patch("ctl.provider_ops._reconcile", return_value=(True, "ready", [])), \
             patch("ctl.provider_ops.read_runtime_env", return_value={"FREELLMAPI_SERVICE_KEY": "internal"}), \
             patch("ctl.provider_ops._available_models", return_value=["llama-3.3-70b-versatile"]), \
             patch("ctl.provider_ops._probe_stream", return_value=StreamProbe(
                 True, 200, "groq/llama-3.3-70b-versatile", "llama-3.3-70b-versatile", "", "done")):
            result = _verify("groq", Path("."), lambda _line: None)
        self.assertTrue(result.success)
        self.assertEqual(result.routed_via.split("/", 1)[0], "groq")

    def test_verification_classifies_catalog_and_route_failures(self):
        from ctl.provider_ops import ModelCatalogUnavailable, StreamProbe, _verify
        common = [patch("ctl.provider_ops._reconcile", return_value=(True, "ready", [])),
                  patch("ctl.provider_ops.read_runtime_env", return_value={"FREELLMAPI_SERVICE_KEY": "internal"})]
        with common[0], common[1], patch("ctl.provider_ops._available_models", return_value=[]):
            self.assertEqual(_verify("groq", Path("."), lambda _line: None).error_code,
                             "catalog_mismatch")
        with patch("ctl.provider_ops._reconcile", return_value=(True, "ready", [])), \
             patch("ctl.provider_ops.read_runtime_env", return_value={"FREELLMAPI_SERVICE_KEY": "internal"}), \
             patch("ctl.provider_ops._available_models", side_effect=ModelCatalogUnavailable("offline")):
            self.assertEqual(_verify("groq", Path("."), lambda _line: None).error_code,
                             "gateway_unavailable")
        with patch("ctl.provider_ops._reconcile", return_value=(True, "ready", [])), \
             patch("ctl.provider_ops.read_runtime_env", return_value={"FREELLMAPI_SERVICE_KEY": "internal"}), \
             patch("ctl.provider_ops._available_models", return_value=["llama-3.3-70b-versatile"]), \
             patch("ctl.provider_ops._probe_stream", return_value=StreamProbe(
                 True, 200, "openrouter/llama", "llama-3.3-70b-versatile", "", "done")):
            self.assertEqual(_verify("groq", Path("."), lambda _line: None).error_code,
                             "provider_route_mismatch")


class SurfSenseContractTests(unittest.TestCase):
    def test_mcp_token_prefix_is_enforced_without_storing_password(self):
        from ctl import mcp_config
        from ctl.mcp_catalog import load as load_catalog
        from ctl.registry import load

        server = next(item for item in load_catalog(load()) if item.id == "surfsense-official")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            with patch("ctl.mcp_registry.RuntimePaths", return_value=paths):
                with self.assertRaisesRegex(ValueError, "ss_pat_"):
                    mcp_config.write(server, {"api_token": "not-a-personal-token"})
                token = "ss_" + "pat_test-value"
                mcp_config.write(server, {"api_token": token})
                path = paths.projects / "mcp-surfsense-official" / ".env"
                from ctl.secrets import read_runtime_env
                self.assertEqual(read_runtime_env(path)["MCP_AUTH_TOKEN"], token)
                content = path.read_text()
                self.assertNotIn("PASSWORD", content)


if __name__ == "__main__":
    unittest.main()
