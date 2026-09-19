"""Durable runtime operations for reviewed application-data MCP servers."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ctl import actions
from ctl.control_state import ControlState
from ctl.jobs import JobStore, redact
from ctl.mcp_catalog import load as load_catalog
from ctl.mcp_registry import credential_path, missing_credentials
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text

SUPPORTED_ACTIONS = frozenset({"enable", "install", "restart", "disable", "verify"})


def _materialize(server, root: Path) -> Path:
    source = server.compose_path(root)
    if source is None or not (source / "docker-compose.yml").is_file():
        raise ValueError("reviewed MCP runtime is not available")
    target = credential_path(server.id).parent
    target.mkdir(mode=0o750, parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name.startswith(".env") or item.is_symlink():
            continue
        destination = target / item.name
        if item.is_file():
            shutil.copy2(item, destination)
        elif item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
    env_path = credential_path(server.id)
    values = read_runtime_env(env_path)
    values.setdefault("MU3LAB_DATA_ROOT", str(RuntimePaths().data))
    if server.id == "actual-budget-community":
        values.setdefault("MCP_AUTH_TOKEN", secrets.token_urlsafe(40))
    env_path.write_text(runtime_env_text(values), encoding="utf-8")
    os.chmod(env_path, 0o600)
    return target


def _probe(url: str) -> tuple[bool, str]:
    if not url:
        return False, "MCP health contract is missing"
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status < 500, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        # MCP POST endpoints commonly return 404/405 to a GET while the
        # process is healthy. Authentication and tool discovery are separate.
        return exc.code in {400, 401, 403, 404, 405}, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return False, str(exc)


def _local_endpoint(server) -> str:
    """Map the catalog's container endpoint onto its loopback test port."""
    health = urllib.parse.urlsplit(server.local_health)
    endpoint = urllib.parse.urlsplit(server.endpoint)
    if not health.scheme or not health.netloc or not endpoint.path:
        return ""
    return urllib.parse.urlunsplit((health.scheme, health.netloc, endpoint.path, "", ""))


def _decode_rpc_response(payload: bytes, content_type: str) -> dict:
    """Decode either JSON or the SSE envelope used by Streamable HTTP MCP."""
    text = payload.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type or text.lstrip().startswith(("event:", "data:")):
        candidates = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not candidates:
            raise ValueError("MCP returned an empty event stream")
        text = candidates[-1]
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise ValueError("MCP returned a non-object response")
    if decoded.get("error"):
        raise ValueError(f"MCP error: {decoded['error']}")
    return decoded


def _rpc_request(url: str, method: str, request_id: int, *, token: str = "",
                 session_id: str = "", params: dict | None = None) -> tuple[dict, str]:
    if params is None:
        params = ({"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "mu3lab-verifier", "version": "1"}}
                  if method == "initialize" else {})
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-03-26",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    payload = json.dumps({"jsonrpc": "2.0", "id": request_id,
                          "method": method, "params": params}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        decoded = _decode_rpc_response(response.read(), response.headers.get("Content-Type", ""))
        return decoded, response.headers.get("Mcp-Session-Id", session_id)


def _rpc_notification(url: str, method: str, *, token: str = "", session_id: str = "") -> None:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-03-26",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    payload = json.dumps({"jsonrpc": "2.0", "method": method}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=15):
        return


def _tool_risk(name: str) -> str:
    write_words = ("create", "add", "update", "delete", "remove", "set", "upload", "move", "write")
    words = set(re.split(r"[^a-z0-9]+", name.lower()))
    return "write" if words.intersection(write_words) else "read"


def _discover_tools(server, values: dict[str, str]) -> list[dict[str, str | bool]]:
    """Verify the actual tool surface instead of treating an open port as success."""
    if server.transport == "openapi-bridge":
        request = urllib.request.Request(server.local_health, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            schema = json.load(response)
        paths = schema.get("paths", {}) if isinstance(schema, dict) else {}
        tools = []
        for path, operations in paths.items():
            if not isinstance(operations, dict):
                continue
            for verb, operation in operations.items():
                if verb.lower() not in {"get", "post", "put", "patch", "delete"} or not isinstance(operation, dict):
                    continue
                name = str(operation.get("operationId") or f"{verb}_{path}").strip()
                tools.append({"id": name, "title": str(operation.get("summary") or name),
                              "risk": "read" if verb.lower() == "get" else "write", "enabled": True})
        if not tools:
            raise ValueError("MCP OpenAPI bridge exposed no tools")
        return tools

    url = _local_endpoint(server)
    if not url:
        raise ValueError("MCP verification endpoint is missing")
    token = values.get("MCP_AUTH_TOKEN", "")
    _, session_id = _rpc_request(url, "initialize", 1, token=token)
    _rpc_notification(url, "notifications/initialized", token=token, session_id=session_id)
    response, _ = _rpc_request(url, "tools/list", 2, token=token, session_id=session_id)
    result = response.get("result", {})
    raw_tools = result.get("tools", []) if isinstance(result, dict) else []
    tools = [
        {"id": str(tool.get("name", "")),
         "title": str(tool.get("title") or tool.get("description") or tool.get("name", ""))[:240],
         "risk": _tool_risk(str(tool.get("name", ""))), "enabled": True}
        for tool in raw_tools if isinstance(tool, dict) and tool.get("name")
    ]
    if not tools:
        raise ValueError("MCP server returned no tools")
    if getattr(server, "id", "") == "surfsense-official":
        check, _ = _rpc_request(url, "tools/call", 3, token=token, session_id=session_id,
                                params={"name": "surfsense_list_workspaces",
                                        "arguments": {"response_format": "json"}})
        result = check.get("result", {})
        if not isinstance(result, dict) or result.get("isError"):
            raise ValueError("SurfSense rejected the token or workspace API check")
    return tools


def _connection(server, values: dict[str, str]) -> dict:
    info = {"id": server.id, "name": server.name, "description": "Mu3Lab curated application tools"}
    if server.transport == "openapi-bridge":
        return {"type": "openapi", "url": server.endpoint, "path": "openapi.json",
                "auth_type": "none", "headers": None, "key": None,
                "config": {"enable": True}, "info": info}
    auth_token = values.get("MCP_AUTH_TOKEN", "")
    return {"type": "mcp", "url": server.endpoint, "path": "",
            "auth_type": "bearer" if auth_token else "none", "headers": None,
            "key": auth_token or None, "config": {"enable": True}, "info": info}


def _register_open_webui(catalog, state: ControlState, root: Path, log) -> tuple[bool, str]:
    connections = []
    for candidate in catalog:
        runtime = state.mcp_server(candidate.id)
        if not runtime or not runtime["enabled"] or runtime["state"] not in {"starting", "live", "degraded"}:
            continue
        connections.append(_connection(candidate, read_runtime_env(credential_path(candidate.id))))
    env_path = RuntimePaths().projects / "open-webui" / ".env"
    values = read_runtime_env(env_path)
    if not values:
        return False, "Open WebUI runtime configuration is not initialized."
    serialized = json.dumps(connections, separators=(",", ":"))
    values = {key: value for key, value in values.items() if key != "TOOL_SERVER_CONNECTIONS"}
    values["TOOL_SERVER_CONNECTIONS"] = serialized
    temporary = env_path.with_suffix(".tmp")
    temporary.write_text(runtime_env_text(values), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(env_path)
    project = root / "core" / "open-webui"
    runtime_env = {"MU3LAB_ENV_FILE": str(env_path), "MU3LAB_DATA_ROOT": str(RuntimePaths().data)}
    rc, output = actions.compose_up(
        project, log,
        env=runtime_env,
        recreate=True, wait_timeout=120,
    )
    if rc:
        return False, output
    # OpenWebUI persists configuration in its own configuration model. The
    # environment is only a first-run default, so explicitly upsert that
    # reviewed value from inside the application runtime. The secret never
    # appears in argv, logs, or Mu3Lab's database.
    sync_script = (
        "import asyncio,json,os; "
        "from open_webui.models.config import Config; "
        "value=json.loads(os.environ.get('TOOL_SERVER_CONNECTIONS','[]')); "
        "asyncio.run(Config.upsert({'tool_server.connections':value}))"
    )
    rc, output = actions.compose_exec(project, "open-webui", ["python", "-c", sync_script], log,
                                      timeout=60, env=runtime_env)
    if rc:
        return False, output or "Open WebUI rejected the curated tool-server configuration."
    # Restart once more so the runtime MCP manager consumes the persisted
    # connection list rather than merely storing it for a future boot.
    rc, output = actions.compose_up(project, log, env=runtime_env, recreate=True, wait_timeout=120)
    return rc == 0, output


def _verify_from_open_webui(server, root: Path, log) -> tuple[bool, str]:
    """Use OpenWebUI's own MCP client to prove the registered tools load."""
    project = root / "core" / "open-webui"
    script = """
import asyncio
import sys
from open_webui.models.config import Config
from open_webui.utils.mcp.client import MCPClient

async def verify(server_id):
    rows = await Config.get('tool_server.connections', []) or []
    row = next((item for item in rows if (item.get('info') or {}).get('id') == server_id), None)
    if not row:
        return 2
    token = row.get('key') if row.get('auth_type') == 'bearer' else None
    headers = {'Authorization': f'Bearer {token}'} if token else None
    client = MCPClient()
    try:
        await client.connect(row['url'], headers=headers)
        tools = await client.list_tool_specs()
        return 0 if tools else 3
    finally:
        await client.disconnect()

raise SystemExit(asyncio.run(verify(sys.argv[1])))
""".strip()
    rc, output = actions.compose_exec(project, "open-webui",
                                      ["python", "-c", script, server.id], log, timeout=60)
    return rc == 0, output or ("Open WebUI tool discovery passed." if rc == 0 else
                              "Open WebUI could not discover the registered MCP tools.")


def execute_claimed(store: JobStore, job: dict, worker_id: str, root: Path) -> None:
    job_id, actor = str(job["id"]), str(job.get("actor") or worker_id)
    action = str(job.get("action") or "")
    server_id = str(job.get("service_id") or "").removeprefix("mcp:")
    registry = load_registry()
    catalog = load_catalog(registry)
    server = next((candidate for candidate in catalog if candidate.id == server_id), None)
    state = ControlState.runtime()
    if not server or server.status != "accepted" or action not in SUPPORTED_ACTIONS or state is None:
        store.transition(job_id, "failed", actor=worker_id,
                         detail="The worker rejected an unavailable MCP operation.",
                         error_code="unsupported_mcp_action", step_id="validate")
        return
    log = lambda line: store.append_event(job_id, "log", line)
    if action == "disable":
        project = credential_path(server.id).parent
        if (project / "docker-compose.yml").is_file():
            rc, output = actions.compose_action(project, "stop", log)
            if rc:
                store.transition(job_id, "failed", actor=actor, detail=redact(output),
                                 error_code="mcp_stop_failed", step_id="stop")
                return
        state.set_mcp_server(server.id, server.service_id, enabled=False, state="disabled")
        ok, detail = _register_open_webui(catalog, state, root, log)
        if not ok:
            store.transition(job_id, "failed", actor=actor, detail=redact(detail),
                             error_code="open_webui_registration_failed", step_id="register")
            return
        store.transition(job_id, "succeeded", actor=actor,
                         detail=f"{server.name} disabled.", step_id="complete")
        return
    missing = missing_credentials(server)
    if missing:
        state.set_mcp_server(server.id, server.service_id, enabled=False,
                             state="authentication_required",
                             error={"message": "Configure: " + ", ".join(missing)})
        store.transition(job_id, "failed", actor=actor,
                         detail="Required MCP application credentials are missing.",
                         error_code="mcp_authentication_required", step_id="validate")
        return
    try:
        project = _materialize(server, root)
    except (OSError, ValueError) as exc:
        store.transition(job_id, "failed", actor=actor, detail=redact(str(exc)),
                         error_code="mcp_materialize_failed", step_id="materialize")
        return
    state.set_mcp_server(server.id, server.service_id, enabled=True, state="starting")
    rc, output = actions.compose_up(project, log, wait_timeout=120)
    if rc:
        state.set_mcp_server(server.id, server.service_id, enabled=True, state="failed",
                             error={"message": redact(output)})
        store.transition(job_id, "failed", actor=actor, detail=redact(output),
                         error_code="mcp_start_failed", step_id="start")
        return
    healthy, detail = _probe(server.local_health)
    if not healthy:
        state.set_mcp_server(server.id, server.service_id, enabled=True, state="degraded",
                             error={"message": redact(detail)})
        store.transition(job_id, "failed", actor=actor, detail=redact(detail),
                         error_code="mcp_verify_failed", step_id="verify")
        return
    try:
        tools = _discover_tools(server, read_runtime_env(credential_path(server.id)))
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError) as exc:
        detail = f"Tool discovery failed: {exc}"
        state.set_mcp_server(server.id, server.service_id, enabled=True, state="degraded",
                             error={"message": redact(detail)})
        store.transition(job_id, "failed", actor=actor, detail=redact(detail),
                         error_code="mcp_tool_discovery_failed", step_id="verify_tools")
        return
    state.set_mcp_server(server.id, server.service_id, enabled=True, state="live",
                         tools=tools, verified=True)
    ok, detail = _register_open_webui(catalog, state, root, log)
    if not ok:
        state.set_mcp_server(server.id, server.service_id, enabled=True, state="incompatible",
                             error={"message": redact(detail)})
        store.transition(job_id, "failed", actor=actor, detail=redact(detail),
                         error_code="open_webui_registration_failed", step_id="register")
        return
    ok, detail = _verify_from_open_webui(server, root, log)
    if not ok:
        state.set_mcp_server(server.id, server.service_id, enabled=True, state="incompatible",
                             error={"message": redact(detail)})
        store.transition(job_id, "failed", actor=actor, detail=redact(detail),
                         error_code="open_webui_tool_discovery_failed", step_id="verify_open_webui")
        return
    store.transition(job_id, "succeeded", actor=actor,
                     detail=f"{server.name} is live and registered with Open WebUI.",
                     step_id="complete")
