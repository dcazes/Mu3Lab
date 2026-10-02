"""Mu3Lab-owned LobeHub account defaults and provider policy for v2.2.18."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from ctl import actions, job_guard
from ctl.jobs import redact
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

AGENTS = (
    (
        "actual-budget",
        "Actual Budget",
        "Plan budgets and inspect transactions.",
        "Help with Actual Budget. Explain calculations and ask before changing transactions, categories, or budgets. Use only Actual Budget tools assigned to this agent.",
    ),
    (
        "mealie",
        "Mealie",
        "Plan meals, recipes, and shopping lists.",
        "Help with Mealie recipes, meal plans, and shopping lists. Ask before changing recipes or lists. Use only Mealie tools assigned to this agent.",
    ),
    (
        "immich",
        "Immich",
        "Find and organize photos and videos.",
        "Help find and organize Immich photos and albums. Ask before uploads, edits, or deletions. Use only Immich tools assigned to this agent.",
    ),
    (
        "paperless-ngx",
        "Paperless-ngx",
        "Find and organize documents.",
        "Help find and organize Paperless documents. Ask before changing metadata, uploading, or deleting documents. Use only Paperless tools assigned to this agent.",
    ),
    (
        "surfsense",
        "SurfSense",
        "Search and organize research sources.",
        "Help search and organize SurfSense research. Use only SurfSense tools assigned to this agent. Ask before changing sources or workspaces.",
    ),
    (
        "firecrawl",
        "Firecrawl",
        "Collect and analyze web content.",
        "Help collect web content with Firecrawl tools assigned to this agent. Explain when a request will fetch external pages or start a crawl. Ask before starting large crawls.",
    ),
    (
        "nextcloud",
        "Nextcloud",
        "Work with files and calendars.",
        "Help with Nextcloud files and calendars. Do not claim to have accessed live data unless a reviewed Nextcloud tool is assigned. Ask before changing data.",
    ),
    (
        "adventurelog",
        "AdventureLog",
        "Plan and review travel records.",
        "Help plan and review AdventureLog trips. Do not claim to have accessed live data unless a reviewed AdventureLog tool is assigned. Ask before changing data.",
    ),
)


def apply_model_policy(values: dict[str, str], root: Path) -> None:
    """Hide every pinned builtin model except the one LiteLLM chat route."""
    path = root / "apps" / "lobehub" / "blocked-model-providers.txt"
    providers = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    for provider in providers:
        values[f"{provider.upper()}_MODEL_LIST"] = "-all"
        values[f"ENABLED_{provider.upper()}"] = "0"
    for name in ("AWS_BEDROCK_MODEL_LIST", "GITEE_AI_MODEL_LIST", "TENCENT_CLOUD_MODEL_LIST"):
        values[name] = "-all"
    values["ENABLED_OPENAI"] = "1"
    values["OPENAI_MODEL_LIST"] = "-all,+mu3lab-chat"


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def installed_apps() -> set[str]:
    """Apps with an assistant that finished installing at least once.

    A stopped app keeps its assistant (and its chats); only an app that was
    never installed, or was removed, has none.
    """
    from ctl.control_state import ControlState

    state = ControlState.runtime()
    if state is None:
        return set()
    return {slug for slug, *_ in AGENTS if (state.installation(slug) or {}).get("installed_at")}


def _agent_sql(installed: set[str]) -> str:
    """Give each installed app its assistant; drop unused ones for other apps."""
    rows = [
        {"slug": f"mu3lab-{slug}", "title": title, "description": description, "system_role": prompt}
        for slug, title, description, prompt in AGENTS
        if slug in installed
    ]
    agent_json = _quoted(json.dumps(rows, ensure_ascii=False))
    absent = ", ".join(_quoted(f"mu3lab-{slug}") for slug, *_ in AGENTS if slug not in installed) or "NULL"
    return f"""
CREATE OR REPLACE FUNCTION mu3lab_seed_default_agents() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO agents (id, slug, title, name, description, user_id, model, provider,
                      system_role, plugins, virtual, pinned, created_at, updated_at)
  SELECT 'agt_mu3lab_' || replace(data.slug, '-', '_') || '_' || substr(md5(NEW.id), 1, 8),
         data.slug, data.title, data.title, data.description, NEW.id,
         'mu3lab-chat', 'openai', data.system_role, '[]'::jsonb, false, true, now(), now()
  FROM jsonb_to_recordset({agent_json}::jsonb)
    AS data(slug text, title text, description text, system_role text)
  ON CONFLICT (slug, user_id) WHERE workspace_id IS NULL DO NOTHING;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS mu3lab_seed_default_agents_trigger ON users;
CREATE TRIGGER mu3lab_seed_default_agents_trigger AFTER INSERT ON users
FOR EACH ROW EXECUTE FUNCTION mu3lab_seed_default_agents();
INSERT INTO agents (id, slug, title, name, description, user_id, model, provider,
                    system_role, plugins, virtual, pinned, created_at, updated_at)
SELECT 'agt_mu3lab_' || replace(data.slug, '-', '_') || '_' || substr(md5(users.id), 1, 8),
       data.slug, data.title, data.title, data.description, users.id,
       'mu3lab-chat', 'openai', data.system_role, '[]'::jsonb, false, true, now(), now()
FROM users CROSS JOIN jsonb_to_recordset({agent_json}::jsonb)
  AS data(slug text, title text, description text, system_role text)
ON CONFLICT (slug, user_id) WHERE workspace_id IS NULL DO NOTHING;
-- An assistant someone already chatted with stays, so no conversation is lost.
DELETE FROM agents a
 WHERE a.workspace_id IS NULL
   AND a.slug IN ({absent})
   AND NOT EXISTS (SELECT 1 FROM topics t WHERE t.agent_id = a.id)
   AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.agent_id = a.id)
   AND NOT EXISTS (SELECT 1 FROM agents_to_sessions s JOIN messages m ON m.session_id = s.session_id
                    WHERE s.agent_id = a.id)
   AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.coordinator_agent_id = a.id);
"""


def _sql(installed: set[str]) -> str:
    # Pinned upstream completion contract:
    # https://github.com/lobehub/lobehub/blob/v2.2.18/src/store/user/slices/onboarding/selectors.ts
    # https://github.com/lobehub/lobehub/blob/v2.2.18/packages/const/src/user.ts
    return f"""
BEGIN;
CREATE OR REPLACE FUNCTION mu3lab_skip_initial_onboarding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.is_onboarded = true;
  NEW.onboarding = COALESCE(NEW.onboarding, '{{}}'::jsonb) || jsonb_build_object(
    'finishedAt', COALESCE(NEW.onboarding ->> 'finishedAt', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')),
    'version', COALESCE(NEW.onboarding -> 'version', '2'::jsonb));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS mu3lab_skip_initial_onboarding_trigger ON users;
CREATE TRIGGER mu3lab_skip_initial_onboarding_trigger BEFORE INSERT ON users
FOR EACH ROW EXECUTE FUNCTION mu3lab_skip_initial_onboarding();
-- v2.2.18 checks onboarding.finishedAt; is_onboarded is deprecated.
UPDATE users SET is_onboarded = true,
  onboarding = COALESCE(onboarding, '{{}}'::jsonb) || jsonb_build_object(
    'finishedAt', COALESCE(onboarding ->> 'finishedAt', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')),
    'version', COALESCE(onboarding -> 'version', '2'::jsonb))
WHERE is_onboarded IS DISTINCT FROM true OR onboarding ->> 'finishedAt' IS NULL;
UPDATE ai_providers SET enabled = false WHERE id <> 'openai' AND enabled IS DISTINCT FROM false;
UPDATE ai_providers SET enabled = true WHERE id = 'openai' AND enabled IS DISTINCT FROM true;
UPDATE ai_models SET enabled = false
 WHERE enabled IS TRUE AND (provider_id <> 'openai' OR id <> 'mu3lab-chat');
{_agent_sql(installed)}CREATE OR REPLACE FUNCTION mu3lab_provider_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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


DATABASE_CONTAINER = "mu3lab-lobehub-postgres-1"
PSQL = [
    "docker",
    "exec",
    "-i",
    DATABASE_CONTAINER,
    "psql",
    "-v",
    "ON_ERROR_STOP=1",
    "-U",
    "postgres",
    "-d",
    "lobehub",
]


def sync_agents(log) -> tuple[bool, str]:
    """Follow an app install or removal: its assistant appears or goes away.

    LobeChat's own start runs the same statements through `reconcile`, so an
    app changed while LobeChat is stopped is picked up when it starts.

    LobeChat is core, and the core installer records no installation state
    for it, so Docker decides whether its database can take the change.
    """
    job_guard.checkpoint()
    rc, output = actions.docker_container_statuses("mu3lab-lobehub")
    database_up = f"{DATABASE_CONTAINER}\tUp" in output
    if rc or not database_up:
        return True, "LobeChat is not running; its assistants sync when it starts."
    rc, output = actions.docker_cmd_stdin(PSQL, f"BEGIN;\n{_agent_sql(installed_apps())}COMMIT;\n", log)
    if rc:
        return False, redact(output)
    # A connector that went live before its assistant existed had nothing to
    # attach to; attach it now that the assistant does.
    ok, detail = _bind_live_connectors(log)
    if not ok:
        return False, detail
    return True, "LobeChat assistants match the installed apps."


def reconcile(log) -> tuple[bool, str]:
    """Keep existing conversations while adding app agents and enforcing model rows."""
    job_guard.checkpoint()
    rc, output = actions.docker_cmd_stdin(PSQL, _sql(installed_apps()), log)
    if rc:
        return False, redact(output)
    ok, detail = _bind_live_connectors(log)
    if not ok:
        return False, detail
    return True, "LobeChat agents and single-provider policy reconciled."


def _bind_live_connectors(log) -> tuple[bool, str]:
    """Attach every enabled, live connector to its app's assistant."""
    from ctl.control_state import ControlState
    from ctl.mcp_catalog import load as load_mcp_catalog
    from ctl.mcp_ops import bind
    from ctl.registry import load as load_registry

    state = ControlState.runtime()
    errors = []
    if state:
        for server in load_mcp_catalog(load_registry()):
            runtime = state.mcp_server(server.id)
            if (
                server.transport != "streamable-http"
                or not runtime
                or not runtime["enabled"]
                or runtime["state"] != "live"
            ):
                continue
            ok, detail = bind(server, runtime.get("tool_snapshot") or [], log)
            if not ok:
                errors.append(detail)
    if errors:
        return False, "; ".join(errors)
    return True, "Live connectors are attached to their assistants."


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
    from ctl.mcp_activity import McpActivity

    activity = McpActivity()
    rows = [
        _tool_row(tool, activity.permission(server.id, str(tool.get("id", "")), str(tool.get("risk", "write"))))
        for tool in tools
        if tool.get("id")
    ]
    ok = _sync_connector(
        f"mu3lab-{server.id}", server.service_id, server.name, server.endpoint, rows, token, enabled=enabled, log=log
    )
    return ok, (
        f"{server.name} synced to its LobeChat agent."
        if ok
        else f"LobeChat rejected the {server.name} connector sync; check its database logs."
    )


def sync_gateway(service_id: str, app_name: str, tools: list[dict], *, enabled: bool, log) -> tuple[bool, str]:
    """Connect an app's assistant to its tool gateway path, and only that.

    The assistant's instructions carry the gateway's category map while it is
    connected and go back to the default when it is not, unless the owner has
    edited them in LobeChat.
    """
    from ctl import mcp_gateway

    rows = [_tool_row(tool, "auto" if tool.get("risk") == "read" else "needs_approval") for tool in tools]
    instructions = ""
    if enabled:
        policy = json.loads((mcp_gateway.project() / "policy" / "policy.json").read_text(encoding="utf-8"))
        instructions = str(policy["apps"][service_id]["instructions"])
    else:
        instructions = next((prompt for slug, _t, _d, prompt in AGENTS if slug == service_id), "")
    ok = _sync_connector(
        f"mu3lab-{service_id}",
        service_id,
        app_name,
        mcp_gateway.endpoint(service_id),
        rows,
        mcp_gateway.app_token(service_id) if enabled else "",
        enabled=enabled,
        log=log,
        exclusive=True,
        instructions=instructions,
    )
    return ok, (
        f"{app_name}'s assistant is connected through the tool gateway."
        if ok
        else f"LobeChat rejected the {app_name} connector sync; check its database logs."
    )


def _tool_row(tool: dict, permission: str) -> dict:
    return {
        "name": str(tool.get("id", "")),
        "title": str(tool.get("title", ""))[:255],
        "risk": "write" if tool.get("risk") == "write" else "read",
        "permission": permission,
        "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
    }


def _instructions_sql(slug: str, service_id: str, instructions: str) -> str:
    """Replace a Mu3Lab-written system role; never one the owner has edited."""
    seeded = next((prompt for key, _t, _d, prompt in AGENTS if key == service_id), "")
    digest = hashlib.sha256(instructions.encode()).hexdigest()
    return f"""
UPDATE agents a SET system_role = {_quoted(instructions)},
  metadata = COALESCE(a.metadata, '{{}}'::jsonb) || jsonb_build_object('mu3lab_system_role_sha', {_quoted(digest)}),
  updated_at = now()
WHERE a.slug = {slug} AND a.workspace_id IS NULL
  AND a.system_role IS DISTINCT FROM {_quoted(instructions)}
  AND (a.system_role = {_quoted(seeded)}
       OR a.metadata ->> 'mu3lab_system_role_sha'
          = encode(sha256(convert_to(COALESCE(a.system_role, ''), 'UTF8')), 'hex'));
"""


def _sync_connector(
    raw_identifier: str,
    service_id: str,
    raw_name: str,
    raw_endpoint: str,
    tool_rows: list[dict],
    token: str,
    *,
    enabled: bool,
    log,
    exclusive: bool = False,
    instructions: str = "",
) -> bool:
    job_guard.checkpoint()
    if not (RuntimePaths().projects / "lobehub" / ".env").is_file():
        return True
    try:
        credential = _quoted(_encrypt_credential(token)) if token else "NULL"
    except (ValueError, OSError) as exc:
        log(str(exc))
        return False
    identifier = _quoted(raw_identifier)
    slug = _quoted(f"mu3lab-{service_id}")
    name = _quoted(raw_name)
    endpoint = _quoted(raw_endpoint)
    tool_json = _quoted(json.dumps(tool_rows, ensure_ascii=False))
    status = "connected" if enabled else "disconnected"
    insert_sql = (
        f"""
INSERT INTO user_connectors (user_id, agent_id, identifier, name, source_type,
  mcp_server_url, mcp_connection_type, status, is_enabled, credentials, created_at, updated_at)
SELECT a.user_id, a.id, {identifier}, {name}, 'custom', {endpoint}, 'http',
       'connected', true, {credential}, now(), now()
FROM agents a WHERE a.slug = {slug} AND a.workspace_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM user_connectors c WHERE c.agent_id = a.id AND c.identifier = {identifier});
"""
        if enabled
        else ""
    )
    # An assistant connected through the gateway keeps no other Mu3Lab connector.
    exclusive_sql = (
        f"""
UPDATE agents a SET plugins = COALESCE((
    SELECT jsonb_agg(item.value)
    FROM jsonb_array_elements(COALESCE(a.plugins, '[]'::jsonb)) AS item(value)
    WHERE CASE WHEN jsonb_typeof(item.value) = 'string' THEN item.value #>> '{{}}'
               ELSE item.value ->> 'identifier' END NOT LIKE 'mu3lab-%'
       OR CASE WHEN jsonb_typeof(item.value) = 'string' THEN item.value #>> '{{}}'
               ELSE item.value ->> 'identifier' END = {identifier}
  ), '[]'::jsonb), updated_at = now()
WHERE a.slug = {slug} AND a.workspace_id IS NULL;
DELETE FROM user_connectors c USING agents a
 WHERE c.agent_id = a.id AND a.slug = {slug} AND a.workspace_id IS NULL
   AND c.identifier LIKE 'mu3lab-%' AND c.identifier <> {identifier};
"""
        if exclusive
        else ""
    )
    stale_sql = (
        f"""
DELETE FROM user_connector_tools t USING user_connectors c, agents a
 WHERE t.user_connector_id = c.id AND c.agent_id = a.id AND a.slug = {slug} AND c.identifier = {identifier}
   AND t.tool_name NOT IN (SELECT r.name FROM jsonb_to_recordset({tool_json}::jsonb) AS r(name text));
"""
        if enabled
        else ""
    )
    sql = f"""
BEGIN;
{exclusive_sql}{insert_sql}
UPDATE user_connectors c SET status = '{status}', is_enabled = {"true" if enabled else "false"},
  mcp_server_url = {endpoint}, mcp_connection_type = 'http',
  credentials = COALESCE({credential}, c.credentials), updated_at = now()
FROM agents a WHERE c.agent_id = a.id AND a.slug = {slug} AND c.identifier = {identifier};
UPDATE agents a SET plugins = CASE WHEN {"true" if enabled else "false"} THEN
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
{stale_sql}
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
{_instructions_sql(slug, service_id, instructions) if instructions else ""}COMMIT;
"""
    rc, _output = actions.docker_cmd_stdin(PSQL, sql, log)
    return rc == 0


def set_tool_permission(server_id: str, tool_name: str, permission: str) -> tuple[bool, str]:
    """Apply a Mu3Lab tool decision to matching agent-scoped LobeChat rows."""
    if permission not in {"auto", "needs_approval", "disabled"}:
        return False, "invalid tool permission"
    sql = f"""
UPDATE user_connector_tools t SET permission = {_quoted(permission)}, updated_at = now()
FROM user_connectors c
WHERE t.user_connector_id = c.id AND c.identifier = {_quoted("mu3lab-" + server_id)}
  AND t.tool_name = {_quoted(tool_name)} AND c.agent_id IS NOT NULL;
"""
    rc, _ = actions.docker_cmd_stdin(
        [
            "docker",
            "exec",
            "-i",
            "mu3lab-lobehub-postgres-1",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "lobehub",
        ],
        sql,
        lambda line: None,
    )
    return rc == 0, ("Permission updated in LobeChat." if rc == 0 else "LobeChat permission sync failed.")
