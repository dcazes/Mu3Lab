"""Application MCP connectors: credentials, lifecycle and tools. ContextForge owns tool choice and call logs."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import JsonValue

from ctl import actions, mcp_config
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, Member, Operator, OperatorMutation
from ctl.api.service_view import service_states
from ctl.control_state import ControlState
from ctl.jobs import JobStore, redact
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
        installation = control.app_installation(server.service_id) if control else None
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
