"""Reviewed application MCP servers: credentials, lifecycle, tools, and activity."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import actions, mcp_config, mcp_console
from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, Operator, OperatorMutation
from ctl.api.service_view import service_states
from ctl.control_state import ControlState
from ctl.jobs import JobStore, redact
from ctl.mcp_activity import McpActivity
from ctl.mcp_registry import snapshot as mcp_snapshot
from ctl.runtime import RuntimePaths

router = APIRouter(prefix="/api/v1/mcp/servers", tags=["mcp"])

QUEUED_ACTIONS = ("enable", "prepare", "install", "restart", "update", "disable", "verify", "switch")


def _server_view(server_id: str, identity: IdentityData) -> dict[str, Any]:
    servers = mcp_snapshot(runtime.registry(), service_states(identity))["servers"]
    server = next((item for item in servers if item["id"] == server_id), None)
    if server is None:
        raise ApiError(404, "unknown MCP server")
    return server


@router.get("")
def list_servers(operator: Operator) -> dict[str, Any]:
    return mcp_snapshot(runtime.registry(), service_states(operator))


@router.get("/{server_id}/tools")
def list_tools(server_id: str, operator: Operator) -> dict[str, Any]:
    server = _server_view(server_id, operator)
    return {"ok": True, "server_id": server_id, "state": server["state"], "tools": server["tools"]}


@router.get("/{server_id}/activity")
def activity(server_id: str, _operator: Operator, before: int = 0, limit: int = 50) -> dict[str, Any]:
    runtime.mcp_server(server_id)
    return {"ok": True, "server_id": server_id, "calls": McpActivity().history(server_id, before=before, limit=limit)}


@router.get("/{server_id}/jobs")
def server_jobs(server_id: str, _operator: Operator, limit: int = 30) -> dict[str, Any]:
    runtime.mcp_server(server_id)
    store = JobStore.runtime()
    return {
        "ok": True,
        "server_id": server_id,
        "jobs": store.jobs_for_service(f"mcp:{server_id}", limit=limit) if store else [],
    }


@router.get("/{server_id}/updates")
def server_updates(server_id: str, _operator: Operator) -> dict[str, Any]:
    server = runtime.mcp_server(server_id)
    return {
        "ok": True,
        "server_id": server_id,
        "current_version": server.revision,
        "reviewed_update": server.reviewed_update,
        "detail": "Only checked-in reviewed releases can be installed.",
    }


@router.get("/{server_id}/logs")
def server_logs(server_id: str, _operator: Operator, tail: int = 120) -> JSONResponse:
    server = runtime.mcp_server(server_id)
    project = RuntimePaths().projects / f"mcp-{server.id}"
    if not (project / "docker-compose.yml").is_file():
        raise ApiError(409, "MCP runtime is not installed")
    rc, output = actions.compose_logs(project, lambda _line: None, tail=max(20, min(tail, 500)))
    return JSONResponse(
        {"ok": rc == 0, "server_id": server.id, "lines": [redact(line) for line in output[-40000:].splitlines()]},
        status_code=200 if rc == 0 else 500,
    )


@router.put("/{server_id}/configuration")
async def put_configuration(server_id: str, request: Request, operator: OperatorMutation) -> dict[str, Any]:
    server = runtime.mcp_server(server_id)
    values = (await runtime.json_body(request)).get("values", {})
    try:
        mcp_config.write(server, values)
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        raise ApiError(422, str(exc)) from exc
    store = JobStore.runtime()
    if store:
        store.record_audit(
            actor=operator["username"],
            event="mcp.configuration.changed",
            detail=f"Write-only credentials updated for {server.id}.",
        )
    return {"ok": True, "server": _server_view(server_id, operator)}


def _apply_switches(server, actor: str, detail: str) -> None:
    """Push a switch change to the gateway and the app's assistant."""
    from ctl.mcp_ops import rebind_app

    ok, message = rebind_app(server.service_id, lambda _line: None)
    store = JobStore.runtime()
    if store:
        store.record_audit(actor=actor, event="mcp.switches.changed", detail=detail)
    if not ok:
        raise ApiError(502, redact(message))


@router.put("/{server_id}/categories/{category_id}")
async def put_category(
    server_id: str, category_id: str, request: Request, operator: OperatorMutation
) -> dict[str, Any]:
    from ctl.mcp_gateway import review_for

    server = runtime.mcp_server(server_id)
    if not server.gateway or review_for(server).category(category_id) is None:
        raise ApiError(404, "unknown tool category")
    enabled = (await runtime.json_body(request)).get("enabled")
    if not isinstance(enabled, bool):
        raise ApiError(422, "enabled must be true or false")
    McpActivity().set_category(server.id, category_id, enabled)
    await run_in_threadpool(
        _apply_switches,
        server,
        operator["username"],
        f"{server.id}: category {category_id} switched {'on' if enabled else 'off'}.",
    )
    return {"ok": True, "server": _server_view(server_id, operator)}


@router.put("/{server_id}/tools/{tool_name}/permission")
async def put_tool_permission(
    server_id: str, tool_name: str, request: Request, _operator: OperatorMutation
) -> dict[str, Any]:
    from ctl.lobehub_ops import set_tool_permission

    catalog_server = runtime.mcp_server(server_id)
    if catalog_server.gateway:
        return await _put_gateway_tool(catalog_server, tool_name, request, _operator)
    state = ControlState.runtime()
    server_runtime = state.mcp_server(server_id) if state else None
    tools = (server_runtime or {}).get("tool_snapshot", [])
    selected_tool = next((item for item in tools if item.get("id") == tool_name), None)
    if selected_tool is None:
        raise ApiError(404, "tool is not in the verified tool list")
    permission = str((await runtime.json_body(request)).get("permission", ""))
    if selected_tool.get("risk") != "read" and permission == "auto":
        raise ApiError(422, "write tools must require approval or be disabled")
    try:
        linked, detail = await run_in_threadpool(set_tool_permission, server_id, tool_name, permission)
        if not linked:
            raise ApiError(502, detail)
        McpActivity().set_permission(server_id, tool_name, permission)
    except (ValueError, TypeError, KeyError) as exc:
        raise ApiError(422, str(exc)) from exc
    return {"ok": True, "server_id": server_id, "tool_name": tool_name, "permission": permission}


async def _put_gateway_tool(server, tool_name: str, request: Request, operator: IdentityData) -> dict[str, Any]:
    from ctl.mcp_gateway import review_for

    tool = review_for(server).tools.get(tool_name)
    if tool is None:
        raise ApiError(404, "tool is not in this connector's review")
    permission = str((await runtime.json_body(request)).get("permission", ""))
    allowed = {"auto", "disabled"} if tool.access == "read" else {"needs_approval", "disabled"}
    if permission not in allowed:
        raise ApiError(422, "tools that change data must ask for approval or be switched off")
    McpActivity().set_permission(server.id, tool_name, permission)
    await run_in_threadpool(
        _apply_switches, server, operator["username"], f"{server.id}: {tool_name} set to {permission}."
    )
    return {"ok": True, "server_id": server.id, "tool_name": tool_name, "permission": permission}


@router.post("/{server_id}/tools/{tool_name}/prepare")
async def prepare_tool_call(
    server_id: str, tool_name: str, request: Request, operator: OperatorMutation
) -> dict[str, Any]:
    payload = await runtime.json_body(request)
    try:
        return await run_in_threadpool(
            mcp_console.prepare, server_id, tool_name, payload.get("arguments"), operator["username"]
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise ApiError(422, str(exc)) from exc


@router.post("/{server_id}/tools/{tool_name}/execute")
async def execute_tool_call(
    server_id: str, tool_name: str, request: Request, operator: OperatorMutation
) -> dict[str, Any]:
    payload = await runtime.json_body(request)
    try:
        return await run_in_threadpool(
            lambda: mcp_console.execute(
                server_id,
                tool_name,
                payload.get("arguments"),
                operator["username"],
                nonce=str(payload.get("confirmation_token") or ""),
                idempotency_key=request.headers.get("idempotency-key", ""),
            )
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise ApiError(422, str(exc)) from exc


@router.post("/{server_id}/{action}")
def queue_action(server_id: str, action: str, request: Request, operator: OperatorMutation) -> dict[str, Any]:
    if action not in QUEUED_ACTIONS:
        raise ApiError(404, "unknown MCP action")
    reg = runtime.registry()
    server = runtime.mcp_server(server_id, reg)
    if server.status != "accepted" or not server.compose_dir:
        raise ApiError(409, "MCP server has not passed runtime review")
    if action == "update" and not server.reviewed_update:
        raise ApiError(409, "no reviewed MCP update is available")
    if action == "prepare":
        control = ControlState.runtime()
        installation = control.installation(server.service_id) if control else None
        if not installation or not installation.get("installed_at"):
            raise ApiError(409, "install the application before preparing its MCP")
    elif service_states(operator).get(server.service_id) not in {"ready", "running", "degraded"}:
        raise ApiError(409, "install and verify the application first")
    store = runtime.job_store()
    try:
        job = store.create(
            kind="wiring",
            service_id=f"mcp:{server.id}",
            action=action,
            actor=operator["username"],
            detail=f"Operator requested MCP {action} for {server.service_id}.",
            idempotency_key=runtime.idempotency_key(request),
        )
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    return {"ok": True, "job": job}
