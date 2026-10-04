"""Per-user calendar: Nextcloud once connected, Mu3Lab's own store until then."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import local_calendar
from ctl import nextcloud_calendar as calendar
from ctl.api import models
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import Identity, IdentityData, mutation_allowed
from ctl.control_state import ControlState
from ctl.platform_apps import by_capability
from ctl.status import snapshots

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"], route_class=ContractRoute)

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
    installation = state.installation(by_capability("files_calendar").id)
    if not installation:
        return "not_installed"
    installation_state = str(installation.get("state", ""))
    # A failed batch can leave durable workflow state behind after the app has
    # become healthy; use the same health contract as the service cards.
    if installation_state == "failed":
        observation = snapshots.read().get(by_capability("files_calendar").id, {})
        installation_state = "running" if observation.get("health_state") == "healthy" else "stopped"
    if installation_state == "stopped":
        return "service_stopped"
    if installation_state not in {"running", "degraded"}:
        return "not_installed"
    identity_state = state.service_identity(by_capability("files_calendar").id)
    if not identity_state or identity_state.get("state") in {"unconfigured", "degraded"}:
        return "sso_not_ready"
    return "ready"


def not_ready_response(value: str) -> dict[str, str | bool | list]:
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
    return ApiError(409, str(not_ready_response(ready)["error"]), state=ready)


@router.get("/connection", response_model=models.CalendarConnection, response_model_exclude_none=True)
def get_connection(reader: Reader) -> models.CalendarConnection:
    state = _state()
    ready = readiness(state)
    if ready != "ready":
        return models.CalendarConnection.model_validate(not_ready_response(ready))
    return models.CalendarConnection.model_validate(calendar.public_connection(str(reader["subject_id"]), state))


@router.post("/connection", response_model=models.CalendarConnection, response_model_exclude_none=True)
async def save_connection(
    payload_model: models.CalendarConnectRequest, request: Request, writer: Writer
) -> models.CalendarConnection:
    payload = payload_model.model_dump()
    try:
        return models.CalendarConnection.model_validate(
            await run_in_threadpool(
                calendar.connect,
                str(writer["subject_id"]),
                str(payload.get("username", "")).strip(),
                str(payload.get("app_password", "")),
            )
        )
    except calendar.CalendarError as exc:
        raise _raise(exc) from exc


@router.put("/connection", response_model=models.CalendarConnection, response_model_exclude_none=True)
async def select_connection(
    payload_model: models.CalendarSelectRequest, request: Request, writer: Writer
) -> models.CalendarConnection:
    state = _state()
    payload = payload_model.model_dump()
    try:
        return models.CalendarConnection.model_validate(
            await run_in_threadpool(
                calendar.select, str(writer["subject_id"]), str(payload.get("calendar_id", "")), state
            )
        )
    except calendar.CalendarError as exc:
        raise _raise(exc) from exc


@router.delete("/connection", response_model=models.OkResponse, response_model_exclude_none=True)
def delete_connection(writer: Writer) -> models.OkResponse:
    return models.OkResponse.model_validate(calendar.disconnect(str(writer["subject_id"])))


@router.post("/auto-connect", response_model=models.CalendarConnection, response_model_exclude_none=True)
def auto_connect(writer: Writer) -> models.CalendarConnection:
    """Create the owner's encrypted device credential without password entry."""
    state = _state()
    ready = readiness(state)
    if ready != "ready":
        raise _not_ready_error(ready)
    try:
        return models.CalendarConnection.model_validate(
            calendar.auto_connect(str(writer["subject_id"]), str(writer.get("username") or ""))
        )
    except calendar.CalendarError as exc:
        raise _raise(exc) from exc


@router.post("/authorization", response_model=models.CalendarAuthorization, response_model_exclude_none=True)
def start_authorization(writer: Writer) -> models.CalendarAuthorization | JSONResponse:
    """Begin Nextcloud Login Flow v2 without sending a password to the browser."""
    state = _state()
    ready = readiness(state)
    # Login Flow v2 also finishes a staged owner migration, so
    # migration_required is intentionally permitted here.
    identity_state = state.service_identity(by_capability("files_calendar").id) or {}
    if ready != "ready" and identity_state.get("state") != "migration_required":
        raise _not_ready_error(ready)
    try:
        return JSONResponse(
            models.CalendarAuthorization.model_validate(
                calendar.start_authorization(str(writer["subject_id"]))
            ).model_dump(mode="json", exclude_none=True),
            status_code=202,
        )
    except calendar.CalendarError as exc:
        raise _raise(exc) from exc


@router.post(
    "/authorization/{authorization_id}/poll",
    response_model=models.CalendarAuthorization,
    response_model_exclude_none=True,
)
def poll_authorization(authorization_id: str, writer: Writer) -> models.CalendarAuthorization:
    try:
        return models.CalendarAuthorization.model_validate(
            calendar.poll_authorization(str(writer["subject_id"]), authorization_id)
        )
    except calendar.CalendarError as exc:
        raise _raise(exc, 404 if exc.state == "not_found" else 400) from exc


@router.delete("/authorization/{authorization_id}", response_model=models.OkResponse, response_model_exclude_none=True)
def cancel_authorization(authorization_id: str, writer: Writer) -> models.OkResponse:
    try:
        calendar.cancel_authorization(str(writer["subject_id"]), authorization_id)
    except calendar.CalendarError as exc:
        raise _raise(exc, 404) from exc
    return models.OkResponse.model_validate({"ok": True})


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _connected(owner_uid: str, state: ControlState | None) -> bool:
    """Owners without a Nextcloud connection use the local calendar."""
    return bool(state and state.calendar_connection(owner_uid))


def _with_local(
    owner_uid: str, result: models.CalendarEvents, start: datetime | None, end: datetime | None, limit: int
) -> models.CalendarEvents:
    """Upload local events after connecting; show any that could not move yet."""
    try:
        if not local_calendar.has_events(owner_uid):
            return result
        moved = local_calendar.push_to_nextcloud(owner_uid)
        if moved:
            result = models.CalendarEvents.model_validate(calendar.events(owner_uid, start=start, end=end, limit=limit))
        if not local_calendar.has_events(owner_uid):
            return result
        now = datetime.now(UTC)
        start = start or now.replace(second=0, microsecond=0)
        remaining = local_calendar.list_events(owner_uid, start, end or start + timedelta(days=30), limit)
    except calendar.CalendarError:
        return result
    merged = sorted(
        [*result.events, *(models.CalendarEvent.model_validate(item) for item in remaining)],
        key=lambda item: item.start,
    )
    return result.model_copy(update={"events": merged[: max(1, min(limit, 100))]})


@router.get("/events", response_model=models.CalendarEvents, response_model_exclude_none=True)
def list_events(request: Request, reader: Reader) -> models.CalendarEvents:
    owner_uid = str(reader["subject_id"])
    state = ControlState.runtime()
    if state is None:
        raise ApiError(503, _UNAVAILABLE, state="unavailable")
    start_value = request.query_params.get("start", "").strip()
    end_value = request.query_params.get("end", "").strip()
    if bool(start_value) != bool(end_value):
        raise ApiError(400, "Calendar start and end must be supplied together.", state="invalid_range")
    try:
        start, end = _parse_time(start_value), _parse_time(end_value)
        limit = int(request.query_params.get("limit", "5"))
    except ValueError as exc:
        raise ApiError(400, "Calendar range values must be valid ISO 8601 datetimes.", state="invalid_range") from exc
    ready = readiness(state)
    if not _connected(owner_uid, state):
        try:
            return models.CalendarEvents.model_validate(
                local_calendar.events(owner_uid, start=start, end=end, limit=limit, nextcloud_ready=ready == "ready")
            )
        except calendar.CalendarError as exc:
            raise _raise(exc, 400 if exc.state == "invalid_range" else 503) from exc
    if ready != "ready":
        cached = calendar.stale_events(owner_uid)
        if cached:
            return models.CalendarEvents.model_validate(cached)
        return models.CalendarEvents.model_validate(
            {"ok": True, "state": ready, "events": [], "error": str(not_ready_response(ready)["error"])}
        )
    try:
        return models.CalendarEvents.model_validate(
            _with_local(
                owner_uid,
                models.CalendarEvents.model_validate(calendar.events(owner_uid, start=start, end=end, limit=limit)),
                start,
                end,
                limit,
            )
        )
    except calendar.CalendarError as exc:
        if exc.state == "unavailable":
            cached = calendar.stale_events(owner_uid)
            if cached:
                return models.CalendarEvents.model_validate(cached)
        raise _raise(exc, 401 if exc.state == "authentication_expired" else 503) from exc


def _is_local(event_id: str) -> bool:
    return event_id.startswith(local_calendar.ID_PREFIX)


@router.post("/events", response_model=models.OkResponse, response_model_exclude_none=True)
async def create_event(
    payload_model: models.CalendarEventRequest, request: Request, writer: Writer
) -> models.OkResponse:
    owner_uid = str(writer["subject_id"])
    payload = payload_model.model_dump()
    store = calendar if _connected(owner_uid, ControlState.runtime()) else local_calendar
    try:
        return models.OkResponse.model_validate(await run_in_threadpool(store.create_event, owner_uid, payload))
    except calendar.CalendarError as exc:
        raise _raise(exc, _conflict_status(exc)) from exc


@router.put("/events/{event_id}", response_model=models.OkResponse, response_model_exclude_none=True)
async def update_event(
    payload_model: models.CalendarEventRequest, event_id: str, request: Request, writer: Writer
) -> models.OkResponse:
    payload = payload_model.model_dump()
    store = local_calendar if _is_local(event_id) else calendar
    try:
        return models.OkResponse.model_validate(
            await run_in_threadpool(store.update_event, str(writer["subject_id"]), event_id, payload)
        )
    except calendar.CalendarError as exc:
        raise _raise(exc, _conflict_status(exc)) from exc


@router.delete("/events/{event_id}", response_model=models.OkResponse, response_model_exclude_none=True)
async def delete_event(
    event_id: str,
    request: Request,
    writer: Writer,
    payload_model: Annotated[models.CalendarDeleteRequest | None, Body()] = None,
) -> models.OkResponse:
    revision = payload_model.revision if payload_model else ""
    store = local_calendar if _is_local(event_id) else calendar
    try:
        return models.OkResponse.model_validate(
            await run_in_threadpool(store.delete_event, str(writer["subject_id"]), event_id, revision)
        )
    except calendar.CalendarError as exc:
        raise _raise(exc, _conflict_status(exc)) from exc
