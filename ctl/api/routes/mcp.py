"""Reviewed application MCP servers: credentials, lifecycle, tools, and activity."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from ctl import actions, mcp_config, mcp_console, mcp_gateway
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, Member, Operator, OperatorMutation
from ctl.api.service_view import service_states
from ctl.control_state import ControlState
from ctl.jobs import JobStore, redact
from ctl.lobehub_ops import set_tool_permission
from ctl.mcp_activity import McpActivity
from ctl.mcp_gateway import review_for
from ctl.mcp_ops import rebind_app
from ctl.mcp_registry import snapshot as mcp_snapshot
from ctl.runtime import RuntimePaths

router = APIRouter(prefix="/api/v1/mcp/servers", tags=["mcp"], route_class=ContractRoute)

QUEUED_ACTIONS = ("enable", "prepare", "install", "restart", "update", "disable", "verify", "switch")


def _server_view(server_id: str, identity: IdentityData) -> dict[str, JsonValue]:
    servers = mcp_snapshot(runtime.registry(), service_states(identity))["servers"]
    server = next((item for item in servers if item["id"] == server_id), None)
    if server is None:
        raise ApiError(404, "unknown MCP server")
    return server


@router.get("", response_model=models.McpRegistryResponse, response_model_exclude_none=True)
def list_servers(operator: Member) -> models.McpRegistryResponse:
    return models.McpRegistryResponse.model_validate(mcp_snapshot(runtime.registry(), service_states(operator)))


@router.get("/{server_id}/tools", response_model=models.McpToolsResponse, response_model_exclude_none=True)
def list_tools(server_id: str, operator: Member) -> models.McpToolsResponse:
    server = _server_view(server_id, operator)
    return models.McpToolsResponse.model_validate(
        {"ok": True, "server_id": server_id, "state": server["state"], "tools": server["tools"]}
    )


@router.get("/{server_id}/activity", response_model=models.McpActivityResponse, response_model_exclude_none=True)
def activity(server_id: str, _operator: Operator, before: int = 0, limit: int = 50) -> models.McpActivityResponse:
    runtime.mcp_server(server_id)
    return models.McpActivityResponse.model_validate(
        {"ok": True, "server_id": server_id, "calls": McpActivity().history(server_id, before=before, limit=limit)}
    )


@router.get("/{server_id}/jobs", response_model=models.McpJobsResponse, response_model_exclude_none=True)
def server_jobs(server_id: str, _operator: Member, limit: int = 30) -> models.McpJobsResponse:
    runtime.mcp_server(server_id)
    store = JobStore.runtime()
    return models.McpJobsResponse.model_validate(
        {
            "ok": True,
            "server_id": server_id,
            "jobs": store.jobs_for_service(f"mcp:{server_id}", limit=limit) if store else [],
        }
    )


@router.get("/{server_id}/updates", response_model=models.McpUpdatesResponse, response_model_exclude_none=True)
def server_updates(server_id: str, _operator: Member) -> models.McpUpdatesResponse:
    server = runtime.mcp_server(server_id)
    return models.McpUpdatesResponse.model_validate(
        {
            "ok": True,
            "server_id": server_id,
            "current_version": server.revision,
            "reviewed_update": server.reviewed_update,
            "detail": "Only checked-in reviewed releases can be installed.",
        }
    )


@router.get("/{server_id}/logs", response_model=models.McpLogsResponse, response_model_exclude_none=True)
def server_logs(server_id: str, _operator: Operator, tail: int = 120) -> models.McpLogsResponse | JSONResponse:
    server = runtime.mcp_server(server_id)
    project = RuntimePaths().projects / f"mcp-{server.id}"
    if not (project / "docker-compose.yml").is_file():
        raise ApiError(409, "MCP runtime is not installed")
    rc, output = actions.compose_logs(project, lambda _line: None, tail=max(20, min(tail, 500)))
    return JSONResponse(
        models.McpLogsResponse.model_validate(
            {"ok": rc == 0, "server_id": server.id, "lines": [redact(line) for line in output[-40000:].splitlines()]}
        ).model_dump(mode="json", exclude_none=True),
        status_code=200 if rc == 0 else 500,
    )


@router.put("/{server_id}/configuration", response_model=models.McpServerResponse, response_model_exclude_none=True)
async def put_configuration(
    payload_model: models.ConfigurationRequest, server_id: str, request: Request, operator: OperatorMutation
) -> models.McpServerResponse:
    server = runtime.mcp_server(server_id)
    values = (payload_model.model_dump()).get("values", {})
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
    return models.McpServerResponse.model_validate({"ok": True, "server": _server_view(server_id, operator)})


def _apply_switches(server, actor: str, detail: str, mutate=None) -> None:
    """Push a switch change to the gateway and the app's assistant."""

    try:
        with mcp_gateway.policy_change():
            if mutate:
                mutate()
            ok, message = rebind_app(server.service_id, lambda _line: None)
    except (OSError, ValueError):
        raise ApiError(
            503,
            "The permission change could not be applied safely. Check gateway policy health.",
            code="gateway_policy_unavailable",
        ) from None
    store = JobStore.runtime()
    if store:
        store.record_audit(actor=actor, event="mcp.switches.changed", detail=detail)
    if not ok:
        raise ApiError(502, redact(message))


@router.put(
    "/{server_id}/categories/{category_id}", response_model=models.McpServerResponse, response_model_exclude_none=True
)
async def put_category(
    payload_model: models.ToolSwitchRequest,
    server_id: str,
    category_id: str,
    request: Request,
    operator: OperatorMutation,
) -> models.McpServerResponse:

    server = runtime.mcp_server(server_id)
    if not server.gateway or review_for(server).category(category_id) is None:
        raise ApiError(404, "unknown tool category")
    enabled = payload_model.enabled
    await run_in_threadpool(
        _apply_switches,
        server,
        operator["username"],
        f"{server.id}: category {category_id} switched {'on' if enabled else 'off'}.",
        lambda: McpActivity().set_category(server.id, category_id, enabled),
    )
    return models.McpServerResponse.model_validate({"ok": True, "server": _server_view(server_id, operator)})


@router.put(
    "/{server_id}/tools/{tool_name}/permission",
    response_model=models.ToolPermissionResponse,
    response_model_exclude_none=True,
)
async def put_tool_permission(
    payload_model: models.ToolPermissionRequest,
    server_id: str,
    tool_name: str,
    request: Request,
    _operator: OperatorMutation,
) -> models.ToolPermissionResponse:

    catalog_server = runtime.mcp_server(server_id)
    if catalog_server.gateway:
        return models.ToolPermissionResponse.model_validate(
            await _put_gateway_tool(catalog_server, tool_name, payload_model.permission, _operator)
        )
    state = ControlState.runtime()
    server_runtime = state.mcp_server(server_id) if state else None
    tools = (server_runtime or {}).get("tool_snapshot", [])
    selected_tool = next((item for item in tools if item.get("id") == tool_name), None)
    if selected_tool is None:
        raise ApiError(404, "tool is not in the verified tool list")
    permission = payload_model.permission
    if selected_tool.get("risk") != "read" and permission == "auto":
        raise ApiError(422, "write tools must require approval or be disabled")
    try:
        linked, detail = await run_in_threadpool(set_tool_permission, server_id, tool_name, permission)
        if not linked:
            raise ApiError(502, detail)
        McpActivity().set_permission(server_id, tool_name, permission)
    except (ValueError, TypeError, KeyError) as exc:
        raise ApiError(422, str(exc)) from exc
    return models.ToolPermissionResponse.model_validate(
        {"ok": True, "server_id": server_id, "tool_name": tool_name, "permission": permission}
    )


async def _put_gateway_tool(server, tool_name: str, permission: str, operator: IdentityData) -> dict[str, JsonValue]:

    tool = review_for(server).tools.get(tool_name)
    if tool is None:
        raise ApiError(404, "tool is not in this connector's review")
    allowed = {"auto", "disabled"} if tool.access == "read" else {"needs_approval", "disabled"}
    if permission not in allowed:
        raise ApiError(422, "tools that change data must ask for approval or be switched off")
    await run_in_threadpool(
        _apply_switches,
        server,
        operator["username"],
        f"{server.id}: {tool_name} set to {permission}.",
        lambda: McpActivity().set_permission(server.id, tool_name, permission),
    )
    return {"ok": True, "server_id": server.id, "tool_name": tool_name, "permission": permission}


@router.post(
    "/{server_id}/tools/{tool_name}/prepare",
    response_model=models.ToolPrepareResponse,
    response_model_exclude_none=True,
)
async def prepare_tool_call(
    payload_model: models.ToolCallRequest, server_id: str, tool_name: str, request: Request, operator: OperatorMutation
) -> models.ToolPrepareResponse:
    payload = payload_model.model_dump()
    try:
        return models.ToolPrepareResponse.model_validate(
            await run_in_threadpool(
                mcp_console.prepare, server_id, tool_name, payload.get("arguments"), runtime.mutation_subject(operator)
            )
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise ApiError(422, str(exc)) from exc


@router.post(
    "/{server_id}/tools/{tool_name}/execute",
    response_model=models.ToolExecuteResponse,
    response_model_exclude_none=True,
)
async def execute_tool_call(
    payload_model: models.ToolCallRequest, server_id: str, tool_name: str, request: Request, operator: OperatorMutation
) -> models.ToolExecuteResponse:
    payload = payload_model.model_dump()
    try:
        return models.ToolExecuteResponse.model_validate(
            await run_in_threadpool(
                lambda: mcp_console.execute(
                    server_id,
                    tool_name,
                    payload.get("arguments"),
                    runtime.mutation_subject(operator),
                    nonce=str(payload.get("confirmation_token") or ""),
                    idempotency_key=request.headers.get("idempotency-key", ""),
                )
            )
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise ApiError(422, str(exc)) from exc


@router.post("/{server_id}/{action}", response_model=models.JobResponse, response_model_exclude_none=True)
def queue_action(server_id: str, action: str, request: Request, operator: OperatorMutation) -> models.JobResponse:
    if action not in QUEUED_ACTIONS:
        raise ApiError(404, "unknown MCP action")
    reg = runtime.registry()
    server = runtime.mcp_server(server_id, reg)
    if server.status != "accepted" or not server.compose_dir:
        raise ApiError(409, "MCP server has not passed runtime review")
    store = runtime.job_store()
    subject = runtime.mutation_subject(operator)
    key = runtime.idempotency_key(request)
    previous = store.by_idempotency_key(
        key or "",
        kind="wiring",
        service_id=f"mcp:{server.id}",
        action=action,
        actor=operator["username"],
        actor_subject=subject,
        namespace="mcp.actions",
    )
    if previous:
        return models.JobResponse.model_validate({"ok": True, "duplicate": True, "job": previous})
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
            actor_subject=subject,
            namespace="mcp.actions",
            detail=f"Operator requested MCP {action} for {server.service_id}.",
            idempotency_key=runtime.idempotency_key(request),
        )
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    return models.JobResponse.model_validate({"ok": True, "job": job})
