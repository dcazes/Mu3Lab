"""Per-user Nextcloud calendar connection and events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import nextcloud_calendar as calendar
from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import Identity, IdentityData, mutation_allowed
from ctl.control_state import ControlState

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])

_UNAVAILABLE = "calendar state is unavailable"


def calendar_reader(identity: Identity) -> IdentityData:
    """Calendar reads are personal: any authenticated subject may read their own."""
    if not identity.get("subject_id"):
        raise ApiError(403, "authenticated subject required")
    return identity


Reader = Annotated[IdentityData, Depends(calendar_reader)]


def calendar_writer(request: Request, identity: Reader) -> IdentityData:
    # Writes, including Login Flow polling that may store a credential, stay
    # behind the operator boundary and the session-bound same-origin check.
    if not identity.get("writes_enabled"):
        raise ApiError(403, "operator identity required")
    if not mutation_allowed(request):
        raise ApiError(403, "same-origin mutation verification failed")
    return identity


Writer = Annotated[IdentityData, Depends(calendar_writer)]


def _state() -> ControlState:
    state = ControlState.runtime()
    if state is None:
        raise ApiError(503, _UNAVAILABLE)
    return state


def _raise(exc: calendar.CalendarError, status_code: int = 400) -> ApiError:
    return ApiError(status_code, str(exc), state=exc.state)


def _conflict_status(exc: calendar.CalendarError) -> int:
    return 409 if exc.state == "event_changed" else 400


def readiness(state: ControlState) -> str:
    installation = state.installation("nextcloud")
    if not installation:
        return "not_installed"
    installation_state = str(installation.get("state", ""))
    # A failed batch can leave durable workflow state behind after the app has
    # become healthy; use the same health contract as the service cards.
    if installation_state == "failed":
        try:
            from ctl.registry import load as load_registry
            from ctl.service_state import _healthy

            installation_state = "running" if _healthy(load_registry().get("nextcloud"))[0] else "stopped"
        except Exception:
            installation_state = "stopped"
    if installation_state == "stopped":
        return "service_stopped"
    if installation_state not in {"running", "degraded"}:
        return "not_installed"
    identity_state = state.service_identity("nextcloud")
    if not identity_state or identity_state.get("state") in {"unconfigured", "degraded"}:
        return "sso_not_ready"
    return "ready"


def not_ready_response(value: str) -> dict[str, Any]:
    detail = {
        "service_stopped": "Nextcloud is installed but stopped.",
        "sso_not_ready": "Repair Nextcloud sign-in before connecting a calendar.",
        "not_installed": "Install Nextcloud before connecting a calendar.",
    }.get(value, "Calendar is unavailable.")
    return {
        "ok": True,
        "state": value,
        "username_hint": "",
        "selected_calendar_id": "",
        "calendars": [],
        "last_success_at": "",
        "error": detail,
    }


def _not_ready_error(ready: str) -> ApiError:
    return ApiError(409, not_ready_response(ready)["error"], state=ready)


@router.get("/connection")
def get_connection(reader: Reader) -> dict[str, Any]:
    state = _state()
    ready = readiness(state)
    if ready != "ready":
        return not_ready_response(ready)
    return calendar.public_connection(str(reader["subject_id"]), state)


@router.post("/connection")
async def save_connection(request: Request, writer: Writer) -> dict[str, Any]:
    payload = await runtime.json_body(request)
    try:
        return await run_in_threadpool(
            calendar.connect,
            str(writer["subject_id"]),
            str(payload.get("username", "")).strip(),
            str(payload.get("app_password", "")),
        )
    except calendar.CalendarError as exc:
        raise _raise(exc) from exc


@router.put("/connection")
async def select_connection(request: Request, writer: Writer) -> dict[str, Any]:
    state = _state()
    payload = await runtime.json_body(request)
    try:
        return await run_in_threadpool(
            calendar.select, str(writer["subject_id"]), str(payload.get("calendar_id", "")), state
        )
    except calendar.CalendarError as exc:
        raise _raise(exc) from exc


@router.delete("/connection")
def delete_connection(writer: Writer) -> dict[str, Any]:
    return calendar.disconnect(str(writer["subject_id"]))


@router.post("/auto-connect")
def auto_connect(writer: Writer) -> dict[str, Any]:
    """Create the owner's encrypted device credential without password entry."""
    state = _state()
    ready = readiness(state)
    if ready != "ready":
        raise _not_ready_error(ready)
    try:
        return calendar.auto_connect(str(writer["subject_id"]), str(writer.get("username") or ""))
    except calendar.CalendarError as exc:
        raise _raise(exc) from exc


@router.post("/authorization")
def start_authorization(writer: Writer) -> JSONResponse:
    """Begin Nextcloud Login Flow v2 without sending a password to the browser."""
    state = _state()
    ready = readiness(state)
    # Login Flow v2 also finishes a staged owner migration, so
    # migration_required is intentionally permitted here.
    identity_state = state.service_identity("nextcloud") or {}
    if ready != "ready" and identity_state.get("state") != "migration_required":
        raise _not_ready_error(ready)
    try:
        return JSONResponse(calendar.start_authorization(str(writer["subject_id"])), status_code=202)
    except calendar.CalendarError as exc:
        raise _raise(exc) from exc


@router.post("/authorization/{authorization_id}/poll")
def poll_authorization(authorization_id: str, writer: Writer) -> dict[str, Any]:
    try:
        return calendar.poll_authorization(str(writer["subject_id"]), authorization_id)
    except calendar.CalendarError as exc:
        raise _raise(exc, 404 if exc.state == "not_found" else 400) from exc


@router.delete("/authorization/{authorization_id}")
def cancel_authorization(authorization_id: str, writer: Writer) -> dict[str, Any]:
    try:
        calendar.cancel_authorization(str(writer["subject_id"]), authorization_id)
    except calendar.CalendarError as exc:
        raise _raise(exc, 404) from exc
    return {"ok": True}


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@router.get("/events")
def list_events(request: Request, reader: Reader) -> dict[str, Any]:
    owner_uid = str(reader["subject_id"])
    state = ControlState.runtime()
    if state is None:
        raise ApiError(503, _UNAVAILABLE, state="unavailable")
    ready = readiness(state)
    if ready != "ready":
        cached = calendar.stale_events(owner_uid)
        if cached:
            return cached
        return {"ok": True, "state": ready, "events": [], "error": not_ready_response(ready)["error"]}
    start_value = request.query_params.get("start", "").strip()
    end_value = request.query_params.get("end", "").strip()
    if bool(start_value) != bool(end_value):
        raise ApiError(400, "Calendar start and end must be supplied together.", state="invalid_range")
    try:
        start, end = _parse_time(start_value), _parse_time(end_value)
        limit = int(request.query_params.get("limit", "5"))
    except ValueError as exc:
        raise ApiError(400, "Calendar range values must be valid ISO 8601 datetimes.", state="invalid_range") from exc
    try:
        return calendar.events(owner_uid, start=start, end=end, limit=limit)
    except calendar.CalendarError as exc:
        if exc.state == "unavailable":
            cached = calendar.stale_events(owner_uid)
            if cached:
                return cached
        raise _raise(exc, 401 if exc.state == "authentication_expired" else 503) from exc


@router.post("/events")
async def create_event(request: Request, writer: Writer) -> dict[str, Any]:
    payload = await runtime.json_body(request)
    try:
        return await run_in_threadpool(calendar.create_event, str(writer["subject_id"]), payload)
    except calendar.CalendarError as exc:
        raise _raise(exc, _conflict_status(exc)) from exc


@router.put("/events/{event_id}")
async def update_event(event_id: str, request: Request, writer: Writer) -> dict[str, Any]:
    payload = await runtime.json_body(request)
    try:
        return await run_in_threadpool(calendar.update_event, str(writer["subject_id"]), event_id, payload)
    except calendar.CalendarError as exc:
        raise _raise(exc, _conflict_status(exc)) from exc


@router.delete("/events/{event_id}")
async def delete_event(event_id: str, request: Request, writer: Writer) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    revision = str(payload.get("revision", "")) if isinstance(payload, dict) else ""
    try:
        return await run_in_threadpool(calendar.delete_event, str(writer["subject_id"]), event_id, revision)
    except calendar.CalendarError as exc:
        raise _raise(exc, _conflict_status(exc)) from exc
