"""Household people: who can use this Mu3Lab, and who can run it. Administrators only."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import people, voice_keys
from ctl.api import models
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import Operator, OperatorMutation
from ctl.integrations.litellm import LiteLLMError
from ctl.jobs import JobStore
from ctl.platform_apps import by_capability
from ctl.registry import load as load_registry
from ctl.service_state import tailnet_dns_name

router = APIRouter(prefix="/api/v1/people", tags=["people"], route_class=ContractRoute)
# Invite links sign someone in; never let a browser or proxy keep a copy.
_NO_STORE = {"Cache-Control": "no-store"}


def _authentik_origin() -> str:
    host = tailnet_dns_name()
    port = load_registry().get(by_capability("identity_provider").id).private_https_port
    if not host or not port:
        raise ApiError(503, "Authentik's private address is not ready yet.")
    return f"https://{host}" if port == 443 else f"https://{host}:{port}"


def _audit(actor: str, detail: str) -> None:
    store = JobStore.runtime()
    if store:
        store.record_audit(actor=actor, event="people.changed", detail=detail)


@router.get("", response_model=models.PeopleResponse, response_model_exclude_none=True)
async def list_people(_admin: Operator) -> models.PeopleResponse:
    try:
        return models.PeopleResponse.model_validate({"ok": True, "people": await run_in_threadpool(people.list_people)})
    except people.PeopleError as exc:
        raise ApiError(503, str(exc)) from exc


@router.post("", response_model=models.PersonResponse, response_model_exclude_none=True)
async def add_person(
    payload_model: models.PersonRequest, request: Request, admin: OperatorMutation
) -> models.PersonResponse | JSONResponse:
    body = payload_model.model_dump()
    role = str(body.get("role", "member"))
    try:
        result = await run_in_threadpool(
            people.add_person, str(body.get("name", "")), str(body.get("email", "")), role, _authentik_origin()
        )
    except people.PeopleError as exc:
        raise ApiError(422, str(exc), headers=_NO_STORE) from exc
    _audit(str(admin["username"]), f"Added {result['person']['username']} as {role}.")
    return JSONResponse(
        models.PersonResponse.model_validate({"ok": True, **result}).model_dump(mode="json", exclude_none=True),
        headers=_NO_STORE,
    )


@router.post("/{username}/{action}", response_model=models.PersonResponse, response_model_exclude_none=True)
async def change_person(
    payload_model: models.PersonChangeRequest, username: str, action: str, request: Request, admin: OperatorMutation
) -> models.PersonResponse | JSONResponse:
    body = payload_model.model_dump()
    if action == "deactivate" and username == admin["username"]:
        raise ApiError(409, "You cannot remove yourself.", headers=_NO_STORE)
    try:
        result = await run_in_threadpool(
            people.change, username, action, _authentik_origin(), str(body.get("role", ""))
        )
    except people.PeopleError as exc:
        raise ApiError(409, str(exc), headers=_NO_STORE) from exc
    _audit(str(admin["username"]), f"{action} for {username}.")
    uid = str((result.get("person") or {}).get("uid") or "")
    if action == "deactivate" and uid:
        # A removed person's devices must stop reaching Mu3Lab's speech at once.
        try:
            await run_in_threadpool(voice_keys.revoke, uid)
        except LiteLLMError as exc:
            _audit(str(admin["username"]), f"Voice key for {username} could not be revoked yet: {exc}")
    return JSONResponse(
        models.PersonResponse.model_validate({"ok": True, **result}).model_dump(mode="json", exclude_none=True),
        headers=_NO_STORE,
    )
