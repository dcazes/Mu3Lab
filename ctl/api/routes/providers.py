"""Inference provider credentials routed through FreeLLMAPI and LiteLLM."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import Operator, OperatorMutation
from ctl.control_state import ControlState
from ctl.provider_catalog import RECOMMENDED_MINIMUM, prefix_warning, setup_progress
from ctl.provider_catalog import catalog as provider_catalog
from ctl.provider_catalog import detect as detect_provider
from ctl.provider_catalog import get as get_provider
from ctl.provider_oauth import OAuthError, exchange_openrouter_code

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])


def _provider_view(item: dict[str, Any]) -> dict[str, Any]:
    try:
        definition = get_provider(str(item["provider_id"]))
        hint, name, examples, supported = definition.key_hint, definition.name, list(definition.example_models), True
    except ValueError:
        hint, name, examples, supported = "Unknown legacy format", str(item["label"]), [], False
    last_error = item["last_error"] or {}
    return {
        "id": item["provider_id"],
        "name": name,
        "label": item["label"],
        "enabled": item["enabled"],
        "state": item["state"],
        "key_hint": hint,
        "credential_indicator": hint.replace("…", "••••"),
        "model_samples": item["model_samples"] or examples,
        "models_are_examples": not bool(item["model_samples"]),
        "last_attempt_at": item["last_attempt_at"],
        "last_verified_at": item["last_verified_at"],
        "updated_at": item["updated_at"],
        "active_job_id": item["active_job_id"],
        "error": last_error.get("message", ""),
        "supported": supported,
        "error_code": last_error.get("code", ""),
        "recommended_action": last_error.get("recommended_action", ""),
        "routed_via": last_error.get("routed_via", ""),
    }


def _connections() -> list[dict[str, Any]]:
    from ctl.provider_ops import migrate_legacy

    state = ControlState.runtime()
    if state is None:
        raise ApiError(503, "provider state is not initialized")
    try:
        migrate_legacy(state)
    except ValueError as exc:
        raise ApiError(503, str(exc)) from exc
    return state.providers()


def _providers() -> list[dict[str, Any]]:
    return [_provider_view(item) for item in _connections()]


@router.get("")
def list_providers(_operator: Operator) -> dict[str, Any]:
    """List safe connection state; encrypted keys never cross this boundary."""
    connections = _connections()
    return {
        "ok": True,
        "providers": [_provider_view(item) for item in connections],
        "setup": setup_progress(connections),
    }


@router.get("/catalog")
def providers_catalog(_operator: Operator) -> dict[str, Any]:
    return {"ok": True, "providers": provider_catalog(), "recommended_minimum": RECOMMENDED_MINIMUM}


def _save_key(provider_id: str, label: str, api_key: str, operator: dict[str, Any], key: str | None) -> dict[str, Any]:
    from ctl.provider_secrets import save

    try:
        definition = get_provider(provider_id) if provider_id else detect_provider(api_key)
        if definition is None:
            raise ValueError("Mu3Lab could not tell which provider this key is for. Choose the provider and try again.")
        result = save(definition.id, label.strip() or definition.name, api_key)
    except (ValueError, TypeError) as exc:
        raise ApiError(400, str(exc)) from exc
    warning = prefix_warning(definition.id, api_key)
    control = ControlState.runtime()
    if control:
        control.set_provider(result["id"], result["label"], enabled=True, state="verifying")
    store = runtime.job_store()
    job = store.create(
        kind="wiring",
        service_id=f"provider:{result['id']}",
        action="save",
        actor=operator["username"],
        detail=f"provider:{result['id']}",
        idempotency_key=key,
    )
    if control:
        control.set_provider(
            result["id"], result["label"], enabled=True, state="verifying", attempted=True, job_id=str(job["id"])
        )
    return {
        "ok": True,
        "provider": result,
        "job": job,
        "warning": warning,
        "routing": {
            "configured": False,
            "detail": "Credential saved privately; the durable worker is verifying model discovery "
            "and streamed routing.",
        },
    }


@router.post("")
async def save_provider(request: Request, operator: OperatorMutation) -> dict[str, Any]:
    """Accept one provider key without ever echoing or logging its value.

    ``provider_id`` is optional: without it the provider is detected from the key."""
    store = runtime.job_store()
    key = runtime.idempotency_key(request)
    previous = store.by_idempotency_key(key or "")
    if previous:
        return {"ok": True, "duplicate": True, "job": previous}
    payload = await runtime.json_body(request)
    return _save_key(
        str(payload.get("provider_id", "")),
        str(payload.get("label", "")),
        str(payload.get("api_key", "")),
        operator,
        key,
    )


@router.post("/openrouter/oauth")
async def openrouter_oauth(request: Request, operator: OperatorMutation) -> dict[str, Any]:
    """Trade OpenRouter's one-time sign-in code for an API key, then save it like a pasted key."""
    store = runtime.job_store()
    key = runtime.idempotency_key(request)
    previous = store.by_idempotency_key(key or "")
    if previous:
        return {"ok": True, "duplicate": True, "job": previous}
    payload = await runtime.json_body(request)
    try:
        api_key = await run_in_threadpool(
            exchange_openrouter_code, str(payload.get("code", "")), str(payload.get("code_verifier", ""))
        )
    except OAuthError as exc:
        raise ApiError(502, str(exc)) from exc
    return _save_key("openrouter", "", api_key, operator, key)


@router.get("/{provider_id}/models")
def provider_models(provider_id: str, _operator: Operator) -> dict[str, Any]:
    provider = next((item for item in _providers() if item["id"] == provider_id), None)
    if not provider:
        raise ApiError(404, "provider connection does not exist")
    return {
        "ok": True,
        "provider_id": provider_id,
        "models": provider["model_samples"],
        "examples": provider["models_are_examples"],
    }


def _queue_provider_action(provider_id: str, action: str, request: Request, operator: dict[str, Any]) -> dict[str, Any]:
    state = ControlState.runtime()
    try:
        resolved_id = get_provider(provider_id).id
    except ValueError as exc:
        # Unsupported legacy rows can only be removed.
        legacy = state.provider(provider_id) if state else None
        if action != "remove" or not legacy or legacy.get("state") != "unsupported_legacy":
            raise ApiError(404, str(exc)) from exc
        resolved_id = provider_id
    if state is None or not state.provider(resolved_id):
        raise ApiError(404, "provider connection does not exist")
    store = runtime.job_store()
    try:
        job = store.create(
            kind="wiring",
            service_id=f"provider:{resolved_id}",
            action=action,
            actor=operator["username"],
            detail=f"Operator requested provider {action}.",
            idempotency_key=runtime.idempotency_key(request),
        )
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    return {"ok": True, "job": job}


@router.post("/{provider_id}/verify")
def verify_provider(provider_id: str, request: Request, operator: OperatorMutation) -> dict[str, Any]:
    return _queue_provider_action(provider_id, "verify", request, operator)


@router.post("/{provider_id}/enable")
def enable_provider(provider_id: str, request: Request, operator: OperatorMutation) -> dict[str, Any]:
    return _queue_provider_action(provider_id, "enable", request, operator)


@router.post("/{provider_id}/disable")
def disable_provider(provider_id: str, request: Request, operator: OperatorMutation) -> dict[str, Any]:
    return _queue_provider_action(provider_id, "disable", request, operator)


@router.delete("/{provider_id}")
def remove_provider(provider_id: str, request: Request, operator: OperatorMutation) -> dict[str, Any]:
    return _queue_provider_action(provider_id, "remove", request, operator)
