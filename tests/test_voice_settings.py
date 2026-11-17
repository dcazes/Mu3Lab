"""The household's voice assistant settings and the file the agent reads (ctl/voice_settings.py)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import voice_settings
from ctl.runtime import RuntimePaths
from ctl.store import records


class VoiceSettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = RuntimePaths(Path(tmp.name))
        self.paths.state.mkdir(parents=True)
        (self.paths.projects / "livekit").mkdir(parents=True)
        self.folder = self.paths.projects / "livekit" / "voice-tools"
        for patcher in (
            patch("ctl.voice_tools.app", return_value=SimpleNamespace(id="livekit")),
            patch("ctl.voice_settings.search_installed", return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def agent_file(self) -> dict:
        return json.loads((self.folder / voice_settings.FILE).read_text())

    def test_defaults_until_an_administrator_chooses(self):
        chosen = voice_settings.get(self.paths)
        self.assertEqual(chosen["responsiveness"], "balanced")
        self.assertTrue(chosen["interruptions"])
        self.assertTrue(chosen["search"])
        self.assertEqual(chosen["voice"], "")

    def test_choices_are_checked_before_anything_is_saved(self):
        with self.assertRaisesRegex(ValueError, "how quickly"):
            voice_settings.save({"responsiveness": "instant"}, "owner", lambda _line: None, self.paths)
        with self.assertRaisesRegex(ValueError, "voices listed"):
            voice_settings.save({"voice": "../../etc"}, "owner", lambda _line: None, self.paths)
        self.assertEqual(records.get(*voice_settings.SETTINGS, self.paths), {})

    def test_a_saved_choice_reaches_the_agent_without_a_restart(self):
        (self.folder).mkdir()
        (self.folder / voice_settings.OLD_FILE).write_text("{}")
        voice_settings.save({"responsiveness": "fast", "voice": "bm_george"}, "owner", lambda _line: None, self.paths)
        written = self.agent_file()
        self.assertEqual(written["responsiveness"], "fast")
        self.assertEqual(written["voice"], "bm_george")
        self.assertNotIn("engine", written)
        # The agent runs as its own user.
        self.assertEqual((self.folder / voice_settings.FILE).stat().st_mode & 0o777, 0o644)
        self.assertFalse((self.folder / voice_settings.OLD_FILE).exists())

    def test_web_search_needs_the_searxng_app_and_the_switch(self):
        voice_settings.write(lambda _line: None, self.paths)
        self.assertEqual(self.agent_file()["search_url"], "")
        with patch("ctl.voice_settings.search_installed", return_value=True):
            voice_settings.write(lambda _line: None, self.paths)
            self.assertEqual(self.agent_file()["search_url"], voice_settings.SEARCH_URL)
            voice_settings.save({"search": False}, "owner", lambda _line: None, self.paths)
            self.assertEqual(self.agent_file()["search_url"], "")

    def test_nothing_is_written_before_the_voice_app_is_installed(self):
        (self.paths.projects / "livekit").rmdir()
        self.assertFalse(voice_settings.write(lambda _line: None, self.paths))


if __name__ == "__main__":
    unittest.main()


class SpeechModelTests(unittest.TestCase):
    """Speaches' models: which are downloaded, and removing one it does not use."""

    STT = ("deepdml/faster-whisper-large-v3-turbo-ct2", "Systran/faster-whisper-small")
    TTS = ("speaches-ai/Kokoro-82M-v1.0-ONNX-int8", "speaches-ai/Kokoro-82M-v1.0-ONNX")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = RuntimePaths(Path(tmp.name))
        fields = [
            {"key": "stt_model", "value": self.STT[0], "options": list(self.STT)},
            {"key": "tts_model", "value": self.TTS[0], "options": list(self.TTS)},
        ]
        for patcher in (
            patch("ctl.voice_settings._speech_app", return_value=SimpleNamespace(id="speaches")),
            patch("ctl.registry.load"),
            patch("ctl.service_config.read", return_value=fields),
            patch("ctl.chat_ops.installed", return_value=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def download(self, model_id: str) -> Path:
        folder = voice_settings._speech_folder(model_id, self.paths)
        (folder / "snapshots" / "abc123").mkdir(parents=True)
        return folder

    def test_every_offered_speech_model_has_its_size_and_memory(self):
        import yaml

        manifest = yaml.safe_load((Path(__file__).resolve().parents[1] / "apps/speaches/app.yaml").read_text())
        fields = [field for field in manifest["configuration"] if field["key"] in {"stt_model", "tts_model"}]
        offered = {option for field in fields for option in field["options"]}
        self.assertTrue(offered)
        self.assertEqual(offered - set(voice_settings.SPEECH), set())

    def test_lists_which_are_downloaded_and_in_use(self):
        self.download(self.STT[0])
        self.download(self.TTS[1])
        # A download Speaches never finished has no snapshot yet.
        voice_settings._speech_folder(self.STT[1], self.paths).mkdir(parents=True)
        options = voice_settings.speech_models(self.paths)["stt"]["options"]
        self.assertEqual([(item["downloaded"], item["in_use"]) for item in options], [(True, True), (False, False)])
        tts = voice_settings.speech_models(self.paths)["tts"]["options"][1]
        self.assertEqual(
            (tts["name"], tts["precision"], tts["downloaded"], tts["in_use"]), ("Kokoro full", "32-bit", True, False)
        )

    def test_removes_only_a_downloaded_model_speaches_does_not_use(self):
        unused = self.download(self.TTS[1])
        self.download(self.TTS[0])
        with patch("ctl.lifecycle.uninstall.delete_data", return_value=(True, "")) as delete:
            with self.assertRaisesRegex(ValueError, "in use"):
                voice_settings.remove_speech_model(self.TTS[0], Path("."), print, self.paths)
            with self.assertRaisesRegex(ValueError, "listed"):
                voice_settings.remove_speech_model("someone/else", Path("."), print, self.paths)
            self.assertIn(
                "not downloaded", voice_settings.remove_speech_model(self.STT[1], Path("."), print, self.paths)
            )
            delete.assert_not_called()
            self.assertIn(
                "Removed Kokoro full", voice_settings.remove_speech_model(self.TTS[1], Path("."), print, self.paths)
            )
        self.assertEqual(delete.call_args.args[0], [unused, unused.parent / ".locks" / unused.name])
