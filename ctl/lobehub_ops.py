"""Mu3Lab-owned LobeHub account defaults and provider policy for v2.2.18."""

from __future__ import annotations

import json
import base64
import os
from pathlib import Path

from ctl import actions
from ctl.jobs import redact
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env


AGENTS = (
    ("actual-budget", "Actual Budget", "Plan budgets and inspect transactions.",
     "Help with Actual Budget. Explain calculations and ask before changing transactions, categories, or budgets. Use only Actual Budget tools assigned to this agent."),
    ("mealie", "Mealie", "Plan meals, recipes, and shopping lists.",
     "Help with Mealie recipes, meal plans, and shopping lists. Ask before changing recipes or lists. Use only Mealie tools assigned to this agent."),
    ("immich", "Immich", "Find and organize photos and videos.",
     "Help find and organize Immich photos and albums. Ask before uploads, edits, or deletions. Use only Immich tools assigned to this agent."),
    ("paperless-ngx", "Paperless-ngx", "Find and organize documents.",
     "Help find and organize Paperless documents. Ask before changing metadata, uploading, or deleting documents. Use only Paperless tools assigned to this agent."),
    ("surfsense", "SurfSense", "Search and organize research sources.",
     "Help search and organize SurfSense research. Use only SurfSense tools assigned to this agent. Ask before changing sources or workspaces."),
    ("firecrawl", "Firecrawl", "Collect and analyze web content.",
     "Help collect web content with Firecrawl tools assigned to this agent. Explain when a request will fetch external pages or start a crawl. Ask before starting large crawls."),
    ("nextcloud", "Nextcloud", "Work with files and calendars.",
     "Help with Nextcloud files and calendars. Do not claim to have accessed live data unless a reviewed Nextcloud tool is assigned. Ask before changing data."),
    ("adventurelog", "AdventureLog", "Plan and review travel records.",
     "Help plan and review AdventureLog trips. Do not claim to have accessed live data unless a reviewed AdventureLog tool is assigned. Ask before changing data."),
)


def apply_model_policy(values: dict[str, str], root: Path) -> None:
    """Hide every pinned builtin model except the one LiteLLM chat route."""
    path = root / "apps" / "lobehub" / "blocked-model-providers.txt"
    providers = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.startswith("#")]
    for provider in providers:
        values[f"{provider.upper()}_MODEL_LIST"] = "-all"
        values[f"ENABLED_{provider.upper()}"] = "0"
    for name in ("AWS_BEDROCK_MODEL_LIST", "GITEE_AI_MODEL_LIST", "TENCENT_CLOUD_MODEL_LIST"):
        values[name] = "-all"
    values["ENABLED_OPENAI"] = "1"
    values["OPENAI_MODEL_LIST"] = "-all,+mu3lab-chat"


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql() -> str:
    rows = [{"slug": f"mu3lab-{slug}", "title": title, "description": description,
             "system_role": prompt} for slug, title, description, prompt in AGENTS]
    agent_json = _quoted(json.dumps(rows, ensure_ascii=False))
    return f"""
BEGIN;
UPDATE ai_providers SET enabled = false WHERE id <> 'openai' AND enabled IS DISTINCT FROM false;
UPDATE ai_providers SET enabled = true WHERE id = 'openai' AND enabled IS DISTINCT FROM true;
UPDATE ai_models SET enabled = false
 WHERE enabled IS TRUE AND (provider_id <> 'openai' OR id <> 'mu3lab-chat');
INSERT INTO agents (id, slug, title, name, description, user_id, model, provider,
                    system_role, plugins, virtual, pinned, created_at, updated_at)
SELECT 'agt_mu3lab_' || replace(data.slug, '-', '_') || '_' || substr(md5(users.id), 1, 8),
       data.slug, data.title, data.title, data.description, users.id,
       'mu3lab-chat', 'openai', data.system_role, '[]'::jsonb, false, true, now(), now()
FROM users CROSS JOIN jsonb_to_recordset({agent_json}::jsonb)
  AS data(slug text, title text, description text, system_role text)
ON CONFLICT (slug, user_id) WHERE workspace_id IS NULL DO NOTHING;
CREATE OR REPLACE FUNCTION mu3lab_provider_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.id <> 'openai' AND NEW.enabled IS DISTINCT FROM false THEN
    RAISE EXCEPTION 'Mu3Lab allows only the LiteLLM OpenAI-compatible provider';
  END IF;
  IF TG_OP = 'INSERT' AND (NEW.key_vaults IS NOT NULL OR NEW.config IS NOT NULL) THEN
    RAISE EXCEPTION 'Mu3Lab manages provider credentials and routes on the server';
  ELSIF TG_OP = 'UPDATE' AND
        (NEW.key_vaults IS DISTINCT FROM OLD.key_vaults OR NEW.config IS DISTINCT FROM OLD.config) THEN
    RAISE EXCEPTION 'Mu3Lab manages provider credentials and routes on the server';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS mu3lab_provider_guard_trigger ON ai_providers;
CREATE TRIGGER mu3lab_provider_guard_trigger BEFORE INSERT OR UPDATE ON ai_providers
FOR EACH ROW EXECUTE FUNCTION mu3lab_provider_guard();
CREATE OR REPLACE FUNCTION mu3lab_model_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.enabled IS TRUE AND (NEW.provider_id <> 'openai' OR NEW.id <> 'mu3lab-chat') THEN
    RAISE EXCEPTION 'Mu3Lab exposes only mu3lab-chat';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS mu3lab_model_guard_trigger ON ai_models;
CREATE TRIGGER mu3lab_model_guard_trigger BEFORE INSERT OR UPDATE ON ai_models
FOR EACH ROW EXECUTE FUNCTION mu3lab_model_guard();
CREATE OR REPLACE FUNCTION mu3lab_user_key_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' AND NEW.key_vaults IS NOT NULL THEN
    RAISE EXCEPTION 'Mu3Lab manages provider credentials on the server';
  ELSIF TG_OP = 'UPDATE' AND NEW.key_vaults IS DISTINCT FROM OLD.key_vaults THEN
    RAISE EXCEPTION 'Mu3Lab manages provider credentials on the server';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS mu3lab_user_key_guard_trigger ON user_settings;
CREATE TRIGGER mu3lab_user_key_guard_trigger BEFORE INSERT OR UPDATE ON user_settings
FOR EACH ROW EXECUTE FUNCTION mu3lab_user_key_guard();
COMMIT;
"""


def reconcile(log) -> tuple[bool, str]:
    """Keep existing conversations while adding app agents and enforcing model rows."""
    rc, output = actions.docker_cmd_stdin(
        ["docker", "exec", "-i", "mu3lab-lobehub-postgres-1", "psql", "-v", "ON_ERROR_STOP=1",
         "-U", "postgres", "-d", "lobehub"], _sql(), log)
    if rc:
        return False, redact(output)
    from ctl.control_state import ControlState
    from ctl.mcp_catalog import load as load_mcp_catalog
    from ctl.mcp_registry import credential_path
    from ctl.registry import load as load_registry
    state = ControlState.runtime()
    if state:
        for server in load_mcp_catalog(load_registry()):
            runtime = state.mcp_server(server.id)
            if server.transport != "streamable-http" or not runtime or not runtime["enabled"] or runtime["state"] != "live":
                continue
            values = read_runtime_env(credential_path(server.id))
            ok, detail = sync_mcp(server, runtime.get("tool_snapshot") or [],
                                  values.get("MCP_AUTH_TOKEN", ""), enabled=True, log=log)
            if not ok:
                return False, detail
    return True, "LobeChat agents and single-provider policy reconciled."


def _encrypt_credential(token: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    secret = read_runtime_env(RuntimePaths().projects / "lobehub" / ".env").get("KEY_VAULTS_SECRET", "")
    key = base64.b64decode(secret, validate=True)
    if len(key) not in {16, 24, 32}:
        raise ValueError("LobeChat credential encryption is unavailable")
    iv = os.urandom(12)
    cipher = AESGCM(key).encrypt(iv, json.dumps({"type": "bearer", "token": token}).encode(), None)
    return f"{iv.hex()}:{cipher[-16:].hex()}:{cipher[:-16].hex()}"


def sync_mcp(server, tools: list[dict], token: str, *, enabled: bool, log) -> tuple[bool, str]:
    """Bind a reviewed live MCP to only its corresponding saved app agent."""
    if server.transport != "streamable-http":
        return True, "LobeChat requires Streamable HTTP MCP."
    if not (RuntimePaths().projects / "lobehub" / ".env").is_file():
        return True, "LobeChat is not installed."
    try:
        credential = _quoted(_encrypt_credential(token)) if token else "NULL"
    except (ValueError, OSError) as exc:
        return False, str(exc)
    identifier = _quoted(f"mu3lab-{server.id}")
    slug = _quoted(f"mu3lab-{server.service_id}")
    name = _quoted(server.name)
    endpoint = _quoted(server.endpoint)
    from ctl.mcp_activity import McpActivity
    activity = McpActivity()
    tool_rows = [{"name": str(tool.get("id", "")), "title": str(tool.get("title", ""))[:255],
                  "risk": "write" if tool.get("risk") == "write" else "read",
                  "permission": activity.permission(server.id, str(tool.get("id", "")),
                                                     str(tool.get("risk", "write"))),
                  "parameters": tool.get("parameters") or {"type": "object", "properties": {}}}
                 for tool in tools if tool.get("id")]
    tool_json = _quoted(json.dumps(tool_rows, ensure_ascii=False))
    status = "connected" if enabled else "disconnected"
    insert_sql = f"""
INSERT INTO user_connectors (user_id, agent_id, identifier, name, source_type,
  mcp_server_url, mcp_connection_type, status, is_enabled, credentials, created_at, updated_at)
SELECT a.user_id, a.id, {identifier}, {name}, 'custom', {endpoint}, 'http',
       'connected', true, {credential}, now(), now()
FROM agents a WHERE a.slug = {slug} AND a.workspace_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM user_connectors c WHERE c.agent_id = a.id AND c.identifier = {identifier});
""" if enabled else ""
    sql = f"""
BEGIN;
{insert_sql}
UPDATE user_connectors c SET status = '{status}', is_enabled = {'true' if enabled else 'false'},
  mcp_server_url = {endpoint}, mcp_connection_type = 'http',
  credentials = COALESCE({credential}, c.credentials), updated_at = now()
FROM agents a WHERE c.agent_id = a.id AND a.slug = {slug} AND c.identifier = {identifier};
UPDATE agents a SET plugins = CASE WHEN {'true' if enabled else 'false'} THEN
    CASE WHEN EXISTS (
      SELECT 1 FROM jsonb_array_elements(COALESCE(a.plugins, '[]'::jsonb)) AS item(value)
      WHERE CASE WHEN jsonb_typeof(item.value) = 'string' THEN item.value #>> '{{}}'
                 ELSE item.value ->> 'identifier' END = {identifier}
    ) THEN COALESCE(a.plugins, '[]'::jsonb)
    ELSE COALESCE(a.plugins, '[]'::jsonb) || jsonb_build_array({identifier}) END
  ELSE COALESCE((
    SELECT jsonb_agg(item.value)
    FROM jsonb_array_elements(COALESCE(a.plugins, '[]'::jsonb)) AS item(value)
    WHERE CASE WHEN jsonb_typeof(item.value) = 'string' THEN item.value #>> '{{}}'
               ELSE item.value ->> 'identifier' END IS DISTINCT FROM {identifier}
  ), '[]'::jsonb) END, updated_at = now()
WHERE a.slug = {slug} AND a.workspace_id IS NULL;
INSERT INTO user_connector_tools (user_connector_id, user_id, tool_name, display_name,
  description, input_schema, crud_type, permission, created_at, updated_at)
SELECT c.id, c.user_id, t.name, t.title, t.title, t.parameters,
       t.risk, t.permission, now(), now()
FROM user_connectors c JOIN agents a ON c.agent_id = a.id
CROSS JOIN jsonb_to_recordset({tool_json}::jsonb)
  AS t(name text, title text, risk text, permission text, parameters jsonb)
WHERE a.slug = {slug} AND c.identifier = {identifier}
ON CONFLICT (user_connector_id, tool_name) DO UPDATE SET
  display_name = EXCLUDED.display_name, description = EXCLUDED.description,
  input_schema = EXCLUDED.input_schema, crud_type = EXCLUDED.crud_type,
  permission = EXCLUDED.permission,
  updated_at = now();
COMMIT;
"""
    rc, _output = actions.docker_cmd_stdin(
        ["docker", "exec", "-i", "mu3lab-lobehub-postgres-1", "psql", "-v", "ON_ERROR_STOP=1",
         "-U", "postgres", "-d", "lobehub"], sql, log)
    return rc == 0, (f"{server.name} synced to its LobeChat agent." if rc == 0
                     else f"LobeChat rejected the {server.name} connector sync; check its database logs.")


def set_tool_permission(server_id: str, tool_name: str, permission: str) -> tuple[bool, str]:
    """Apply a Mu3Lab tool decision to matching agent-scoped LobeChat rows."""
    if permission not in {"auto", "needs_approval", "disabled"}:
        return False, "invalid tool permission"
    sql = f"""
UPDATE user_connector_tools t SET permission = {_quoted(permission)}, updated_at = now()
FROM user_connectors c
WHERE t.user_connector_id = c.id AND c.identifier = {_quoted('mu3lab-' + server_id)}
  AND t.tool_name = {_quoted(tool_name)} AND c.agent_id IS NOT NULL;
"""
    rc, _ = actions.docker_cmd_stdin(
        ["docker", "exec", "-i", "mu3lab-lobehub-postgres-1", "psql", "-v", "ON_ERROR_STOP=1",
         "-U", "postgres", "-d", "lobehub"], sql, lambda line: None)
    return rc == 0, ("Permission updated in LobeChat." if rc == 0 else "LobeChat permission sync failed.")
