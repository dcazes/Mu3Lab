"""LobeChat shows an app's assistant only once that app is installed."""

from __future__ import annotations

import json
import re
import unittest
from unittest.mock import MagicMock, patch

from ctl import lobehub_ops


def _seeded(sql: str) -> set[str]:
    """Slugs the seeding statements would insert."""
    rows = re.search(r"jsonb_to_recordset\('(.*?)'::jsonb\)", sql, re.S)
    assert rows
    return {row["slug"] for row in json.loads(rows.group(1).replace("''", "'"))}


def _removable(sql: str) -> set[str]:
    listed = re.search(r"a\.slug IN \((.*?)\)", sql, re.S)
    assert listed
    return set(re.findall(r"'([^']+)'", listed.group(1)))


class AgentSyncTests(unittest.TestCase):
    def test_only_installed_apps_get_an_assistant(self):
        sql = lobehub_ops._sql({"firecrawl"})
        self.assertEqual(_seeded(sql), {"mu3lab-firecrawl"})
        self.assertIn("mu3lab-mealie", _removable(sql))
        self.assertNotIn("mu3lab-firecrawl", _removable(sql))

    def test_assistants_with_conversations_are_never_removed(self):
        sql = lobehub_ops._sql(set())
        for guard in ("FROM topics", "FROM messages", "agents_to_sessions", "coordinator_agent_id"):
            self.assertIn(guard, sql.split("DELETE FROM agents", 1)[1])

    def test_with_every_app_installed_nothing_is_removed(self):
        every = {slug for slug, *_ in lobehub_ops.AGENTS}
        self.assertEqual(_removable(lobehub_ops._sql(every)), set())

    def test_a_stopped_app_keeps_its_assistant(self):
        state = MagicMock()
        state.installation.side_effect = lambda slug: {
            "mealie": {"state": "stopped", "installed_at": "2026-09-29T00:00:00+00:00"},
            "immich": {"state": "failed", "installed_at": ""},
        }.get(slug)
        with patch("ctl.control_state.ControlState.runtime", return_value=state):
            self.assertEqual(lobehub_ops.installed_apps(), {"mealie"})

    def test_sync_waits_for_lobechat_to_run(self):
        stopped = (0, "mu3lab-lobehub-postgres-1\tExited (0) 2 minutes ago\n")
        with (
            patch("ctl.lobehub_ops.actions.docker_container_statuses", return_value=stopped),
            patch("ctl.lobehub_ops.actions.docker_cmd_stdin") as run,
        ):
            ok, _detail = lobehub_ops.sync_agents(lambda _line: None)
        self.assertTrue(ok)
        run.assert_not_called()

    def test_sync_runs_without_a_lobechat_installation_record(self):
        # The core installer starts LobeChat without recording its state.
        running = (0, "mu3lab-lobehub-app-1\tUp 5 minutes\nmu3lab-lobehub-postgres-1\tUp 5 minutes (healthy)\n")
        state = MagicMock()
        state.installation.side_effect = lambda slug: (
            {"state": "running", "installed_at": "x"} if slug == "mealie" else None
        )
        with (
            patch("ctl.control_state.ControlState.runtime", return_value=state),
            patch("ctl.lobehub_ops.actions.docker_container_statuses", return_value=running),
            patch("ctl.lobehub_ops.actions.docker_cmd_stdin", return_value=(0, "")) as run,
        ):
            ok, _detail = lobehub_ops.sync_agents(lambda _line: None)
        self.assertTrue(ok)
        self.assertIn("mu3lab-mealie", run.call_args.args[1])

    def test_sync_attaches_a_connector_that_went_live_before_its_assistant(self):
        running = (0, "mu3lab-lobehub-postgres-1\tUp 5 minutes (healthy)\n")
        state = MagicMock()
        state.installation.return_value = {"state": "running", "installed_at": "x"}
        state.mcp_server.side_effect = lambda server_id: (
            {"enabled": True, "state": "live", "tool_snapshot": [{"id": "scrape"}]}
            if server_id == "firecrawl-official"
            else None
        )
        with (
            patch("ctl.control_state.ControlState.runtime", return_value=state),
            patch("ctl.lobehub_ops.actions.docker_container_statuses", return_value=running),
            patch("ctl.lobehub_ops.actions.docker_cmd_stdin", return_value=(0, "")),
            patch("ctl.lobehub_ops.sync_mcp", return_value=(True, "")) as bind,
        ):
            ok, _detail = lobehub_ops.sync_agents(lambda _line: None)
        self.assertTrue(ok)
        self.assertEqual([call.args[0].id for call in bind.call_args_list], ["firecrawl-official"])


if __name__ == "__main__":
    unittest.main()
