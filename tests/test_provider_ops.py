"""Provider route-probe normalization contracts."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.provider_ops import StreamProbe, _probe_candidates, _verify


class ProviderProbeTests(unittest.TestCase):
    def test_groq_uses_normalized_gateway_models_not_owned_by_metadata(self):
        candidates = _probe_candidates("groq", [
            "gpt-oss-120b", "llama-3.3-70b-versatile", "unrelated-model",
        ])
        self.assertEqual(candidates, ["llama-3.3-70b-versatile", "gpt-oss-120b"])

    def test_stream_route_header_is_authoritative_after_normalization(self):
        successful = StreamProbe(True, 200, "groq/gpt-oss-120b", "gpt-oss-120b", "", "Stream completed.")
        with patch("ctl.provider_ops._reconcile", return_value=(True, "ok", [])), \
             patch("ctl.provider_ops.read_runtime_env", return_value={
                 "FREELLMAPI_SERVICE_KEY": "service-key", "LITELLM_MASTER_KEY": "master-key"}), \
             patch("ctl.provider_ops._available_models", return_value=["gpt-oss-120b"]), \
             patch("ctl.provider_ops._probe_stream", return_value=successful):
            result = _verify("groq", Path("."), lambda _line: None)
        self.assertTrue(result.success)
        self.assertEqual(result.routed_via, "groq/gpt-oss-120b")

    def test_provider_route_mismatch_remains_degraded_not_key_rejected(self):
        mismatch = StreamProbe(True, 200, "openrouter/gpt-oss-120b", "gpt-oss-120b", "", "Stream completed.")
        with patch("ctl.provider_ops._reconcile", return_value=(True, "ok", [])), \
             patch("ctl.provider_ops.read_runtime_env", return_value={"FREELLMAPI_SERVICE_KEY": "service-key"}), \
             patch("ctl.provider_ops._available_models", return_value=["gpt-oss-120b"]), \
             patch("ctl.provider_ops._probe_stream", return_value=mismatch):
            result = _verify("groq", Path("."), lambda _line: None)
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "provider_route_mismatch")


if __name__ == "__main__":
    unittest.main()
