"""Voice call reports (ctl/voice_reports.py): listed newest first, read as a transcript, deleted
after the household's limit or on request, and nothing outside the reports folder is ever touched."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import voice_reports, voice_settings
from ctl.runtime import RuntimePaths


class VoiceReportsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = RuntimePaths(Path(tmp.name))
        self.paths.state.mkdir(parents=True)
        self.base = self.paths.projects / "livekit" / voice_reports.FOLDER
        self.base.mkdir(parents=True)
        patcher = patch("ctl.voice_tools.app", return_value=SimpleNamespace(id="livekit"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def report(self, name: str, *, audio: bool = True, age_days: float = 0) -> Path:
        folder = self.base / name
        folder.mkdir()
        now = time.time() - age_days * 86400
        (folder / "call.json").write_text(
            json.dumps({"who": "sam@example.com", "name": "Sam", "started_at": now - 90, "ended_at": now})
        )
        items = [
            {"type": "message", "role": "user", "content": ["Add dinner at seven"], "created_at": now - 80},
            {"type": "function_call", "name": "calendar-create_event", "arguments": '{"title": "Dinner"}'},
            {"type": "function_call_output", "output": '{"status": "needs_confirmation"}', "is_error": False},
            {
                "type": "message",
                "role": "assistant",
                "content": ["Dinner at seven tonight. Shall I add it?"],
                "interrupted": True,
                "metrics": {"e2e_latency": 1.25},
            },
            {"type": "message", "role": "system", "content": ["instructions"]},
        ]
        (folder / "report.json").write_text(json.dumps({"chat_history": {"items": items}}))
        if audio:
            (folder / "audio.ogg").write_bytes(b"OggS-fake")
        os.utime(folder, (now, now))
        return folder

    def test_listed_newest_first_and_old_ones_deleted(self):
        self.report("20261120-100000Z_room-a", age_days=4)
        self.report("20261124-100000Z_room-b", audio=False)
        self.report("20261101-100000Z_room-c", age_days=30)
        (self.base / "README.md").write_text("note")
        found = voice_reports.listing(self.paths)
        self.assertEqual([item["id"] for item in found], ["20261124-100000Z_room-b", "20261120-100000Z_room-a"])
        self.assertEqual((found[0]["who"], found[0]["audio"], round(found[0]["seconds"])), ("Sam", False, 90))
        self.assertFalse((self.base / "20261101-100000Z_room-c").exists())
        self.assertTrue((self.base / "README.md").exists())
        voice_settings.save({"reports_days": 3}, "owner", lambda _line: None, self.paths)
        self.assertEqual(len(voice_reports.listing(self.paths)), 1)

    def test_a_report_reads_as_a_transcript_with_tool_calls(self):
        self.report("20261124-100000Z_room-b")
        report = voice_reports.read("20261124-100000Z_room-b", self.paths)
        self.assertEqual([line["kind"] for line in report["lines"]], ["said", "tool", "tool_result", "said"])
        self.assertEqual(report["lines"][0]["text"], "Add dinner at seven")
        self.assertTrue(report["lines"][3]["interrupted"])
        self.assertEqual(report["lines"][3]["reply_after"], 1.25)
        self.assertTrue(voice_reports.audio("20261124-100000Z_room-b", self.paths).startswith("data:audio/ogg;base64,"))

    def test_only_report_folders_can_be_read_or_deleted(self):
        outside = self.paths.projects / "livekit" / "voice-tools"
        outside.mkdir()
        for name in ("../voice-tools", "voice-tools", "20261124-100000Z_../../x", ""):
            with self.subTest(name=name), self.assertRaises(voice_reports.ReportError):
                voice_reports.delete(name, self.paths)
        self.assertTrue(outside.exists())
        self.report("20261124-100000Z_room-b", audio=False)
        with self.assertRaisesRegex(voice_reports.ReportError, "no audio"):
            voice_reports.audio("20261124-100000Z_room-b", self.paths)
        voice_reports.delete("20261124-100000Z_room-b", self.paths)
        self.assertEqual(voice_reports.listing(self.paths), [])
        with self.assertRaisesRegex(voice_reports.ReportError, "gone"):
            voice_reports.read("20261124-100000Z_room-b", self.paths)

    def test_the_agent_is_told_what_to_keep(self):
        self.paths.projects.joinpath("livekit", "voice-tools").mkdir()
        voice_settings.save({"reports_audio": False, "reports_days": 7}, "owner", lambda _line: None, self.paths)
        written = json.loads((self.paths.projects / "livekit" / "voice-tools" / voice_settings.FILE).read_text())
        self.assertEqual((written["reports"], written["reports_audio"], written["reports_days"]), (True, False, 7))
        voice_settings.save({"reports": False, "reports_audio": True}, "owner", lambda _line: None, self.paths)
        written = json.loads((self.paths.projects / "livekit" / "voice-tools" / voice_settings.FILE).read_text())
        self.assertEqual((written["reports"], written["reports_audio"]), (False, False))


if __name__ == "__main__":
    unittest.main()
