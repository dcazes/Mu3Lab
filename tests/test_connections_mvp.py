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

    def test_successful_provider_verification_resumes_waiting_core_job(self):
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
            with patch("ctl.provider_ops.ControlState.runtime", return_value=state), \
                 patch("ctl.provider_ops._verify", return_value=(True, "passed", ["model-a"])):
                execute_claimed(store, claimed, "worker", Path(tmp))
            jobs = store.jobs(limit=10)
            self.assertTrue(any(item["service_id"] == "core-suite" and item["state"] == "queued"
                                for item in jobs))

    def test_model_samples_use_freellmapi_openai_owned_by_shape(self):
        from ctl.provider_ops import _samples
        payload = {"data": [
            {"id": "model-a", "owned_by": "groq", "available": True},
            {"id": "model-b", "owned_by": "nvidia", "available": True},
            {"id": "model-c", "owned_by": "groq", "available": False},
        ]}
        with patch("ctl.provider_ops._request_json", return_value=(200, payload)):
            self.assertEqual(_samples("groq", "internal-key"), ["model-a"])


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
