"""Operator-only MCP tool console. Results are returned, never persisted."""

from __future__ import annotations

import json
import time
import urllib.error
from typing import Any

from jsonschema import Draft202012Validator

from ctl import mcp_gateway
from ctl.control_state import ControlState
from ctl.mcp_activity import McpActivity
from ctl.mcp_catalog import load as load_catalog
from ctl.mcp_ops import _rpc_notification, _rpc_request
from ctl.registry import load as load_registry


def _resolve(server_id: str, tool_name: str):
    server = next((item for item in load_catalog(load_registry()) if item.id == server_id), None)
    if server is None or server.status != "accepted":
        raise ValueError("unknown or unavailable MCP connection")
    state = ControlState.runtime()
    runtime = state.mcp_server(server_id) if state else None
    installation = state.installation(server.service_id) if state else None
    if (
        not runtime
        or not runtime["enabled"]
        or runtime["state"] != "live"
        or not installation
        or installation["state"] != "running"
    ):
        raise ValueError("MCP connection and its application must be live")
    tool = next((item for item in runtime.get("tool_snapshot", []) if item.get("id") == tool_name), None)
    if not tool:
        raise ValueError("tool is not in the verified tool list")
    if server.gateway:
        # The console follows the same review and switches as chat does.
        from ctl.mcp_gateway import review_for, tool_states

        review = review_for(server)
        reviewed = review.tools.get(tool_name)
        if reviewed is None:
            raise ValueError(review.blocked.get(tool_name) or "tool has not been reviewed for Mu3Lab")
        states = tool_states(server, review)
        if not states["categories"][reviewed.category] or not states["tools"][tool_name]:
            raise ValueError("tool is switched off")
        return server, tool | {"risk": reviewed.access}, "auto" if reviewed.access == "read" else "needs_approval"
    raise ValueError(
        "This connector needs a tool review and gateway approval enforcement before console calls are available."
    )


def _validate(tool: dict, arguments: Any) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError("tool input must be a JSON object")
    if len(json.dumps(arguments)) > 64_000:
        raise ValueError("tool input exceeds the 64 KB limit")
    schema = tool.get("parameters") or {"type": "object", "properties": {}}
    if not isinstance(schema, dict):
        raise ValueError("tool input schema is invalid")
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(arguments), key=lambda error: list(map(str, error.path)))
    if errors:
        raise ValueError("tool input is invalid: " + errors[0].message[:250])
    return arguments


def prepare(server_id: str, tool_name: str, arguments: Any, actor: str) -> dict:
    _server, tool, permission = _resolve(server_id, tool_name)
    payload = _validate(tool, arguments)
    mcp_gateway.verify_operator(actor)
    needs_confirmation = tool.get("risk") != "read" or permission == "needs_approval"
    nonce = ""
    if needs_confirmation:
        if not _server.gateway:
            raise ValueError("Unreviewed connector writes are unavailable; use an approved gateway connector.")
        policy = mcp_gateway.current_policy()
        app = policy["apps"].get(_server.service_id)
        if not app:
            raise ValueError("This connector is not available for approval.")
        token = mcp_gateway.operator_token(actor, _server.service_id)
        authority = mcp_gateway.authority()
        principal = authority.authenticate(token, _server.service_id)
        operation = authority.prepare(
            principal,
            _server.service_id,
            app,
            tool_name,
            payload,
            tool.get("parameters") or {"type": "object"},
            policy["revision"],
        )
        nonce = operation["id"]
    return {
        "ok": True,
        "server_id": server_id,
        "tool_name": tool_name,
        "risk": tool.get("risk", "write"),
        "permission": permission,
        "confirmation_required": needs_confirmation,
        "confirmation_token": nonce,
        "expires_in_seconds": 300 if nonce else 0,
    }


def execute(
    server_id: str, tool_name: str, arguments: Any, actor: str, *, nonce: str = "", idempotency_key: str = ""
) -> dict:
    server, tool, permission = _resolve(server_id, tool_name)
    payload = _validate(tool, arguments)
    mcp_gateway.verify_operator(actor)
    needs_confirmation = tool.get("risk") != "read" or permission == "needs_approval"
    activity = McpActivity()
    if needs_confirmation:
        if not server.gateway:
            raise ValueError("Unreviewed connector writes are unavailable; use an approved gateway connector.")
        authority = mcp_gateway.authority()
        operation = authority.view(nonce, actor, arguments=True)
        if (
            operation["server"] != server_id
            or operation["tool"] != tool_name
            or not authority.matches_arguments(nonce, actor, payload)
        ):
            raise ValueError("Confirmation does not match these tool arguments.")
        if operation["state"] == "pending":
            authority.decide(nonce, actor, approve=True)
        return mcp_gateway.dispatch_operation(nonce, actor)
    if server.transport != "streamable-http":
        raise ValueError("tool console requires a Streamable HTTP MCP connection")
    endpoint = f"http://127.0.0.1:{mcp_gateway.PORT}/apps/{server.service_id}/mcp"
    token = mcp_gateway.operator_token(actor, server.service_id)
    started = time.monotonic()
    try:
        _, session = _rpc_request(endpoint, "initialize", 1, token=token)
        _rpc_notification(endpoint, "notifications/initialized", token=token, session_id=session)
        result, _ = _rpc_request(
            endpoint,
            "tools/call",
            2,
            token=token,
            session_id=session,
            params={"name": "use_tool", "arguments": {"tool": tool_name, "arguments": payload}},
        )
        outcome = (
            "tool_error" if isinstance(result.get("result"), dict) and result["result"].get("isError") else "succeeded"
        )
        activity.record(server_id, tool_name, "dashboard", actor, outcome, int((time.monotonic() - started) * 1000))
        return {"ok": outcome == "succeeded", "result": result.get("result", {}), "outcome": outcome}
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        activity.record(
            server_id,
            tool_name,
            "dashboard",
            actor,
            "failed",
            int((time.monotonic() - started) * 1000),
            "mcp_call_failed",
        )
        raise ValueError("MCP tool call failed; inspect connection health and server logs") from None
