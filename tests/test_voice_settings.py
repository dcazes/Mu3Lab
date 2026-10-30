"""The household's voice assistant settings and the file the agent reads (ctl/voice_settings.py)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import duplex_voice, voice_settings
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
        self.assertEqual(written["engine"], "pipeline")
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

    def test_calls_switch_to_duplex_only_once_the_model_is_loaded(self):
        duplex_voice._save(duplex_voice.DUPLEX, "owner", [], self.paths, loaded=False)
        voice_settings.write(lambda _line: None, self.paths)
        self.assertTrue(duplex_voice.duplex_on(self.paths))
        self.assertEqual(self.agent_file()["engine"], "pipeline")
        duplex_voice._save(duplex_voice.DUPLEX, "owner", [], self.paths)
        voice_settings.write(lambda _line: None, self.paths)
        self.assertEqual(self.agent_file()["engine"], "duplex")

    def test_nothing_is_written_before_the_voice_app_is_installed(self):
        (self.paths.projects / "livekit").rmdir()
        self.assertFalse(voice_settings.write(lambda _line: None, self.paths))


if __name__ == "__main__":
    unittest.main()
