"""Official API upserts preserve conversations and avoid duplicate assistants.

The old SQL/trigger tests were removed together with that implementation.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


from ctl import chat_connections, lobehub_ops
from ctl.runtime import RuntimePaths

# The same app while it is stopped: no key is made for it.


class AgentSyncTests(unittest.TestCase):


    def test_report_says_why_each_app_has_no_assistant(self):
        def app(app_id):
            manifest = Mock(id=app_id)
            manifest.name = app_id.title()
            return Mock(id=app_id, manifest=manifest)

        def connector(app_id):
            return Mock(id=app_id + "-mcp", service_id=app_id)

        apps = [app(name) for name in ("unconnected", "down", "ready", "waiting", "stopped", "broken")]
        servers = {
            "down-mcp": {"enabled": True, "state": "authentication_required"},
            "ready-mcp": {"enabled": True, "state": "live"},
            "waiting-mcp": {"enabled": True, "state": "live"},
            "stopped-mcp": {"enabled": True, "state": "stopped"},
            "broken-mcp": {"enabled": True, "state": "live"},
        }
        state = Mock()
        state.installation.return_value = {"state": "running"}
        state.app_installation.return_value = {"state": "running"}
        state.mcp_server.side_effect = servers.get
        # "unconnected" has no chat connector at all.
        connectors = [connector(name) for name in ("down", "ready", "waiting", "stopped", "broken")]
        managed = {
            "ready": {"agent_id": "a", "check": {"status": "ready", "detail": "LobeChat reached 3 tools."}},
            "stopped": {"agent_id": "b", "check": {"status": "paused", "detail": "Switched off."}},
            "broken": {"agent_id": "c", "check": {"status": "failed", "detail": "LobeChat could not reach it."}},
        }
        records = {"me": {"key": "sk-lh-key", "managed": managed}}
        with (
            patch.object(lobehub_ops.ControlState, "runtime", return_value=state),
            patch.object(lobehub_ops.chat_connections, "records", return_value=records),
            patch.object(lobehub_ops, "load_connectors", return_value=connectors),
            patch.object(lobehub_ops, "load_registry"),
            patch.object(lobehub_ops, "cached", return_value=Mock(apps=apps)),
        ):
            report = {item["id"]: item["status"] for item in lobehub_ops.assistant_report("me", operator=True)}
            stranger = {
                item["id"]: item["status"] for item in lobehub_ops.assistant_report("someone-else", operator=True)
            }
        self.assertEqual(
            report,
            {
                "unconnected": "needs_review",
                "down": "connector_down",
                "ready": "ready",
                "waiting": "pending",
                "stopped": "paused",
                "broken": "failed",
            },
        )
        self.assertEqual(stranger["ready"], "not_connected")


    def test_device_approval_mints_once_and_discards_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            chat_connections.save(
                "person", {"device_code": "device", "expires_at": 99999999999, "next_poll": 0, "interval": 5}, paths
            )
            client = Mock()
            client.poll_device_login.return_value = {"state": "approved", "access_token": "oidc-secret"}
            client.create_key.return_value = "sk-lh-secret"
            self.assertEqual(chat_connections.poll("person", client, paths)["state"], "connected")
            chat_connections.poll("person", client, paths)
            client.create_key.assert_called_once()
            self.assertNotIn("oidc-secret", str(chat_connections.records(paths)))
            self.assertNotIn(b"sk-lh-secret", (paths.state / "mu3lab.db").read_bytes())

    def test_another_person_cannot_poll_the_device_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            chat_connections.save("owner", {"device_code": "private"}, paths)
            with self.assertRaises(ValueError):
                chat_connections.poll("other", Mock(), paths)

    def test_repeated_connect_cannot_discard_an_approved_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            chat_connections.save("person", {"key": "sk-lh-existing"}, paths)
            with self.assertRaises(ValueError):
                chat_connections.begin("person", {"device_code": "new"}, paths)
            self.assertEqual(chat_connections.records(paths)["person"]["key"], "sk-lh-existing")

