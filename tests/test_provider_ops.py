"""Provider verification asks FreeLLMAPI, which keeps the free-model knowledge."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.freellmapi_admin import GatewayAdminError
from ctl.provider_ops import StreamProbe, _forget_removed_keys, _verify

CHAT_OK = StreamProbe(True, 200, "", "mu3lab-chat", "", "Stream completed.")


class FakeGateway:
    """FreeLLMAPI's admin API as `_verify` sees it."""

    def __init__(self, verdict="healthy", reason="", models=(), results=None, keys=None):
        self.verdict, self.reason = verdict, reason
        self.models = [{"id": index, "modelId": name} for index, name in enumerate(models)]
        self.results = results or {}
        self.keys = keys if keys is not None else [{"id": 7, "platform": "groq"}]
        self.tested: list[str] = []
        self.deleted: list[int] = []

    def api_keys(self):
        return self.keys

    def check_key(self, key_id):
        return self.verdict, self.reason

    def ranked_models(self, platform):
        return self.models

    def test_model(self, model_db_id):
        name = self.models[model_db_id]["modelId"]
        self.tested.append(name)
        return self.results.get(name, (False, "HTTP 404 model retired"))

    def delete_key(self, key_id):
        self.deleted.append(key_id)


def _run(gateway: FakeGateway, chat: StreamProbe = CHAT_OK, saved_key: str = "gsk_" + "a" * 52) -> StreamProbe:
    with (
        patch("ctl.provider_ops.records", return_value=[{"id": "groq", "api_key": saved_key}]),
        patch("ctl.provider_ops._reconcile", return_value=(True, "ok", [])),
        patch("ctl.provider_ops.GatewayAdmin.sign_in", return_value=gateway),
        patch("ctl.provider_ops._chat_route_check", return_value=chat),
    ):
        return _verify("groq", Path("."), lambda _line: None)


class VerifyTests(unittest.TestCase):
    def test_uses_freellmapis_own_top_model(self):
        gateway = FakeGateway(models=("openai/gpt-oss-120b", "llama"), results={"openai/gpt-oss-120b": (True, "")})
        result = _run(gateway)
        self.assertTrue(result.success)
        self.assertEqual(result.model, "openai/gpt-oss-120b")
        self.assertEqual(gateway.tested, ["openai/gpt-oss-120b"])

    def test_a_retired_model_moves_on_down_freellmapis_ranking(self):
        gateway = FakeGateway(models=("gemini-2.5-flash", "gemini-3.8-flash"), results={"gemini-3.8-flash": (True, "")})
        result = _run(gateway)
        self.assertTrue(result.success)
        self.assertEqual(gateway.tested, ["gemini-2.5-flash", "gemini-3.8-flash"])

    def test_an_invalid_key_reports_the_providers_reason(self):
        gateway = FakeGateway(verdict="invalid", reason="HTTP 401: token expired or incorrect")
        result = _run(gateway)
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "credential_rejected")
        self.assertIn("token expired or incorrect", result.detail)
        self.assertIn("console.groq.com/keys", result.detail)
        self.assertEqual(gateway.tested, [])

    def test_a_healthy_key_is_connected_even_when_every_model_is_busy(self):
        result = _run(FakeGateway(models=("a", "b")))
        self.assertTrue(result.success)
        self.assertIn("accepted the key", result.detail)

    def test_an_unchecked_key_with_no_answering_model_fails(self):
        result = _run(FakeGateway(verdict="unknown", models=("a",)))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "stream_failed")

    def test_chat_must_still_stream_through_litellm(self):
        broken = StreamProbe(False, 502, "", "mu3lab-chat", "stream_failed", "LiteLLM returned HTTP 502.")
        result = _run(FakeGateway(models=("a",), results={"a": (True, "")}), chat=broken)
        self.assertEqual(result.error_code, "litellm_route_failed")

    def test_a_key_freellmapi_did_not_load_is_reported(self):
        self.assertEqual(_run(FakeGateway(keys=[])).error_code, "gateway_unavailable")

    def test_an_unreachable_gateway_is_reported(self):
        with (
            patch("ctl.provider_ops.records", return_value=[{"id": "groq", "api_key": "gsk_abc"}]),
            patch("ctl.provider_ops._reconcile", return_value=(True, "ok", [])),
            patch("ctl.provider_ops.GatewayAdmin.sign_in", side_effect=GatewayAdminError("down")),
        ):
            self.assertEqual(_verify("groq", Path("."), lambda _line: None).error_code, "gateway_unavailable")

    def test_a_saved_sentence_is_rejected_plainly(self):
        result = _run(FakeGateway(), saved_key="compare these\ntwo products")
        self.assertEqual(result.error_code, "credential_rejected")
        self.assertIn("doesn't look like an API key", result.detail)


class GatewayKeyCleanupTests(unittest.TestCase):
    def test_keys_of_removed_providers_leave_freellmapi(self):
        gateway = FakeGateway(
            keys=[{"id": 1, "platform": "groq"}, {"id": 2, "platform": "zhipu"}, {"id": 3, "platform": "custom"}]
        )
        with (
            patch("ctl.provider_ops.records", return_value=[{"id": "groq", "api_key": "gsk_abc"}]),
            patch("ctl.provider_ops.GatewayAdmin.sign_in", return_value=gateway),
        ):
            _forget_removed_keys(lambda _line: None)
        # Keys Mu3Lab does not manage (e.g. added in FreeLLMAPI's own dashboard) stay.
        self.assertEqual(gateway.deleted, [2])


if __name__ == "__main__":
    unittest.main()
