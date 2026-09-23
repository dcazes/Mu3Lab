"""Read LobeChat's tool metadata without reading message or tool content."""

from __future__ import annotations

import json

from ctl import actions
from ctl.mcp_activity import McpActivity


SQL = """
SELECT json_build_object(
  'ref', p.id, 'server', substring(p.identifier from 8),
  'tool', COALESCE(p.api_name, ''), 'actor', p.user_id,
  'outcome', CASE WHEN p.error IS NOT NULL OR m.error IS NOT NULL
                  THEN 'failed' ELSE 'succeeded' END,
  'created_at', m.created_at
)::text
FROM message_plugins p JOIN messages m ON m.id = p.id
WHERE p.identifier LIKE 'mu3lab-%'
  AND m.created_at < now() - interval '30 seconds'
ORDER BY m.created_at DESC LIMIT 500;
"""


def ingest(log) -> None:
    rc, output = actions.docker_cmd_stdin(
        ["docker", "exec", "-i", "mu3lab-lobehub-postgres-1", "psql", "-At", "-v", "ON_ERROR_STOP=1",
         "-U", "postgres", "-d", "lobehub"], SQL, lambda _line: None)
    if rc:
        return
    store = McpActivity()
    for line in output.splitlines():
        try:
            row = json.loads(line)
            if not all(row.get(key) for key in ("ref", "server", "tool", "actor", "created_at")):
                continue
            store.ingest_chat_call(str(row["ref"]), str(row["server"]), str(row["tool"]),
                                   str(row["actor"]), str(row["outcome"]),
                                   str(row["created_at"]))
        except (ValueError, TypeError):
            log("Skipped malformed LobeChat MCP activity metadata.")
