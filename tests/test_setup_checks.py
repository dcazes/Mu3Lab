"""Apps that need an AI model install, then wait stopped with a sentence until the owner makes the choice."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ctl.engine.hooks import SetupNeeded, StepFailed
from ctl.registry import load
from ctl.rules import Params
from ctl.rules.needs_chat_model import NeedsChatModel
from ctl.rules.needs_voice_model import NeedsVoiceModel, NeedsVoiceModelParams
from ctl.status.display import projection

MODEL = SimpleNamespace(name="Qwen3 4B")


def context(app_id: str):
    ctx = Mock()
    ctx.app = load().catalog.get(app_id)
    ctx.facts = SimpleNamespace(paths=Mock(), catalog=Mock())
    ctx.needs_setup.side_effect = lambda stage, code, message: (_ for _ in ()).throw(SetupNeeded(stage, code, message))
    ctx.fail.side_effect = lambda stage, code, message: (_ for _ in ()).throw(StepFailed(stage, code, message))
    return ctx


class ChatModelTests(unittest.TestCase):
    def check(self, *, local_only=False, provider=False, model=None):
        ctx = context("lobehub")
        with (
            patch("ctl.rules.needs_chat_model.local_models.local_only", return_value=local_only),
            patch("ctl.rules.needs_chat_model.local_models.chat_model", return_value=model),
            patch("ctl.rules.needs_chat_model.model_ready.provider_connected", return_value=provider),
            patch("ctl.rules.needs_chat_model.model_ready.local_model") as local,
        ):
            NeedsChatModel(Params()).setup_check(ctx)
        return local

    def test_a_connected_provider_is_enough(self):
        self.check(provider=True).assert_not_called()

    def test_no_provider_and_no_local_model_says_where_to_go(self):
        with self.assertRaises(SetupNeeded) as caught:
            self.check()
        self.assertEqual(caught.exception.code, "no_outside_provider")
        self.assertIn("LobeChat is set to use outside providers, but none is connected", caught.exception.message)
        self.assertIn("Settings, AI", caught.exception.message)

    def test_a_local_chat_model_works_without_a_provider(self):
        self.check(model=MODEL).assert_called_once()

    def test_local_only_ignores_providers_and_checks_the_local_model(self):
        self.check(local_only=True, provider=True, model=MODEL).assert_called_once()


class VoiceModelTests(unittest.TestCase):
    MODELS = ("mu3lab-voice", "mu3lab-stt", "mu3lab-tts")

    def check(self, *, privacy="outside_allowed", provider=False, routed=MODELS):
        ctx = context("livekit")
        rule = NeedsVoiceModel(NeedsVoiceModelParams(models=self.MODELS))
        with (
            patch("ctl.rules.needs_voice_model.local_models.voice_settings", return_value={"privacy": privacy}),
            patch("ctl.rules.needs_voice_model.local_models.voice_model", return_value=MODEL),
            patch("ctl.rules.needs_voice_model.model_ready.provider_connected", return_value=provider),
            patch("ctl.rules.needs_voice_model.model_ready.local_model") as local,
            patch("ctl.rules.needs_voice_model.model_ready.routed", return_value=list(routed)),
        ):
            rule.setup_check(ctx)
        return local

    def test_outside_providers_with_none_connected_needs_setup_not_a_failure(self):
        with self.assertRaises(SetupNeeded) as caught:
            self.check()
        self.assertEqual(caught.exception.code, "no_outside_provider")
        self.assertIn("choose a local model in Settings, Voice Assistant", caught.exception.message)

    def test_a_local_voice_model_needs_no_provider(self):
        self.check(privacy="local_only").assert_called_once()

    def test_missing_speech_route_needs_setup(self):
        with self.assertRaises(SetupNeeded) as caught:
            self.check(provider=True, routed=("mu3lab-voice",))
        self.assertIn("Install Speaches", caught.exception.message)

    def test_the_rule_no_longer_blocks_before_start(self):
        rule = NeedsVoiceModel(NeedsVoiceModelParams(models=self.MODELS))
        rule.before_start(context("livekit"))


class CardTests(unittest.TestCase):
    def test_an_app_waiting_on_setup_shows_needs_setup_with_the_sentence(self):
        shown = projection({"state": "setup_required", "detail": "Connect one in Settings, AI."})
        self.assertEqual(shown["display_state"], "needs_setup")
        self.assertEqual(shown["reason"], "Connect one in Settings, AI.")


if __name__ == "__main__":
    unittest.main()
