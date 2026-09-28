"""Access to runtime stores and registries, failing with API errors."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from ctl import workflow_secrets
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, job_identity
from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.mcp_catalog import McpServer
from ctl.mcp_catalog import load as load_mcp_catalog
from ctl.registry import Registry, RegistryError, Service
from ctl.registry import load as load_registry


def job_store() -> JobStore:
    store = JobStore.runtime()
    if store is None:
        raise ApiError(503, "runtime job store is not initialized")
    return store


def control_state() -> ControlState:
    state = ControlState.runtime()
    if state is None:
        raise ApiError(503, "runtime state is not initialized")
    return state


def registry() -> Registry:
    try:
        return load_registry()
    except RegistryError as exc:
        raise ApiError(503, str(exc)) from exc


def service(service_id: str) -> Service:
    try:
        return load_registry().get(service_id)
    except RegistryError as exc:
        raise ApiError(404, str(exc)) from exc


def mcp_server(server_id: str, reg: Registry | None = None) -> McpServer:
    server = next((item for item in load_mcp_catalog(reg or registry()) if item.id == server_id), None)
    if server is None:
        raise ApiError(404, "unknown MCP server")
    return server


def idempotency_key(request: Request) -> str | None:
    return request.headers.get("idempotency-key") or None


async def json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise ApiError(400, "invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise ApiError(400, "JSON body must be an object")
    return payload


def hand_identity_to_job(
    store: JobStore, job: dict[str, Any], identity: IdentityData, *, detail: str, error_code: str, step_id: str
) -> None:
    """Store the caller's identity encrypted for a job that provisions their account."""
    try:
        workflow_secrets.save_job_identity(str(job["id"]), **job_identity(identity))
    except workflow_secrets.WorkflowSecretError as exc:
        store.transition(
            str(job["id"]),
            "failed",
            actor=str(identity["username"]),
            detail=detail,
            error_code=error_code,
            step_id=step_id,
        )
        raise ApiError(503, str(exc)) from exc
