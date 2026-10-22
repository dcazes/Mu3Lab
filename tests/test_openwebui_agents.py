"""Open WebUI gets everyone's app assistants, through Mu3Lab's administrator key, reaching ContextForge."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


from ctl import chat_ops, openwebui_ops
from ctl.control_state import ControlState


class ClientTests(unittest.TestCase):


    def test_the_main_assistant_is_every_new_chats_model(self):
        compose = (Path(__file__).resolve().parents[1] / "apps/open-webui/docker-compose.yml").read_text()
        self.assertIn("DEFAULT_MODELS: mu3lab-assistant", compose)


class ReportTests(unittest.TestCase):
    def test_the_report_shows_open_webuis_answer(self):
        def app(app_id):
            manifest = Mock(id=app_id)
            manifest.name = app_id.title()
            return Mock(id=app_id, manifest=manifest)

        apps = [app(name) for name in ("ready", "stopped", "broken", "down", "new")]
        servers = {
            "ready-mcp": {"enabled": True, "state": "live"},
            "stopped-mcp": {"enabled": True, "state": "stopped"},
            "broken-mcp": {"enabled": True, "state": "live"},
            "down-mcp": {"enabled": True, "state": "degraded"},
            "new-mcp": {"enabled": True, "state": "live"},
        }
        state = Mock()
        state.installation.return_value = {"state": "running"}
        state.app_installation.return_value = {"state": "running"}
        state.mcp_server.side_effect = servers.get
        connectors = [Mock(id=name + "-mcp", service_id=name) for name in ("ready", "stopped", "broken", "down", "new")]
        record = {
            "checks": {
                "ready": {"status": "ready", "detail": "Open WebUI reached 2 tools."},
                "stopped": {"status": "paused", "detail": "Switched off."},
                "broken": {"status": "failed", "detail": "Open WebUI could not reach this app's tools."},
                "down": {"status": "paused", "detail": "Switched off."},
            },
            "error": "",
        }
        with (
            patch.object(openwebui_ops.ControlState, "runtime", return_value=state),
            patch.object(openwebui_ops.records, "get", return_value=record),
            patch.object(openwebui_ops, "load_connectors", return_value=connectors),
            patch.object(openwebui_ops, "load_registry"),
            patch.object(openwebui_ops, "cached", return_value=Mock(apps=apps)),
        ):
            report = {item["id"]: item for item in openwebui_ops.assistant_report("me", operator=True)}
        self.assertEqual(
            {key: item["status"] for key, item in report.items()},
            {"ready": "ready", "stopped": "paused", "broken": "failed", "down": "connector_down", "new": "pending"},
        )
        self.assertIn("could not reach", report["broken"]["detail"])


class ChatAppChoiceTests(unittest.TestCase):
    def test_open_webui_is_the_default_and_lobechat_must_be_installed_to_choose(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "state.db")
            with patch("ctl.chat_ops.ControlState.runtime", return_value=state):
                self.assertEqual(chat_ops.selected().id, "open-webui")
                with self.assertRaises(chat_ops.ChatError):
                    chat_ops.choose("lobehub", "owner")
                state.set_installation("lobehub", "running")
                self.assertEqual(chat_ops.choose("lobehub", "owner").id, "lobehub")
                self.assertEqual(chat_ops.selected().id, "lobehub")
                self.assertTrue(chat_ops.needs_connect(chat_ops.selected()))


if __name__ == "__main__":
    unittest.main()
