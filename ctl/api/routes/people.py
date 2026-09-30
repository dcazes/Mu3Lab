"""Household people: who can use this Mu3Lab, and who can run it. Administrators only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import people
from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import Operator, OperatorMutation
from ctl.jobs import JobStore
from ctl.registry import load as load_registry
from ctl.service_state import tailnet_dns_name

router = APIRouter(prefix="/api/v1/people", tags=["people"])
# Invite links sign someone in; never let a browser or proxy keep a copy.
_NO_STORE = {"Cache-Control": "no-store"}


def _authentik_origin() -> str:
    host = tailnet_dns_name()
    port = load_registry().get("authentik").private_https_port
    if not host or not port:
        raise ApiError(503, "Authentik's private address is not ready yet.")
    return f"https://{host}" if port == 443 else f"https://{host}:{port}"


def _audit(actor: str, detail: str) -> None:
    store = JobStore.runtime()
    if store:
        store.record_audit(actor=actor, event="people.changed", detail=detail)


@router.get("")
async def list_people(_admin: Operator) -> dict[str, Any]:
    try:
        return {"ok": True, "people": await run_in_threadpool(people.list_people)}
    except people.PeopleError as exc:
        raise ApiError(503, str(exc)) from exc


@router.post("")
async def add_person(request: Request, admin: OperatorMutation) -> JSONResponse:
    body = await runtime.json_body(request)
    role = str(body.get("role", "member"))
    try:
        result = await run_in_threadpool(
            people.add_person, str(body.get("name", "")), str(body.get("email", "")), role, _authentik_origin()
        )
    except people.PeopleError as exc:
        raise ApiError(422, str(exc), headers=_NO_STORE) from exc
    _audit(str(admin["username"]), f"Added {result['person']['username']} as {role}.")
    return JSONResponse({"ok": True, **result}, headers=_NO_STORE)


@router.post("/{username}/{action}")
async def change_person(username: str, action: str, request: Request, admin: OperatorMutation) -> JSONResponse:
    body = await runtime.json_body(request)
    if action == "deactivate" and username == admin["username"]:
        raise ApiError(409, "You cannot remove yourself.", headers=_NO_STORE)
    try:
        result = await run_in_threadpool(
            people.change, username, action, _authentik_origin(), str(body.get("role", ""))
        )
    except people.PeopleError as exc:
        raise ApiError(409, str(exc), headers=_NO_STORE) from exc
    _audit(str(admin["username"]), f"{action} for {username}.")
    return JSONResponse({"ok": True, **result}, headers=_NO_STORE)
