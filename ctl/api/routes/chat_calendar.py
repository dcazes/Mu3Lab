"""Chat's calendar tools: one person's own calendar, reached with their calendar key.

Only the calendar adapter on this computer calls these routes (``platform/calendar-tools``),
passing on the caller's key (``ctl/calendar_tools.py``). Anything that came through Caddy is
refused, so the dashboard's browser sign-in can never reach them, and a key is refused once its
person is removed from the household. The work itself is the dashboard calendar's own
(``ctl/api/routes/calendar.py``), so chat and the dashboard always see the same events.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from starlette.concurrency import run_in_threadpool

from ctl import access, calendar_tools
from ctl.api import models
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.routes import calendar

router = APIRouter(prefix="/api/v1/chat-tools/calendar", tags=["chat-tools"], route_class=ContractRoute)
DEFAULT_DAYS = 30


def _caller(request: Request) -> str:
    """The person whose key this is; never a browser request relayed by Caddy."""
    if request.headers.get("x-mu3lab-proxy-token") or request.headers.get("x-authentik-uid"):
        raise ApiError(403, "Chat's calendar tools are not reachable from a browser.")
    authorization = request.headers.get("authorization", "")
    uid = calendar_tools.person_for(authorization.removeprefix("Bearer ").strip())
    if not uid:
        raise ApiError(401, "This calendar key is not valid.")
    if access.hold(uid):
        raise ApiError(403, "This person no longer has access to Mu3Lab.")
    return uid


Caller = Annotated[str, Depends(_caller)]


@router.get("/events", response_model=models.CalendarEvents, response_model_exclude_none=True)
def list_events(request: Request, uid: Caller) -> models.CalendarEvents:
    start = request.query_params.get("start", "").strip()
    end = request.query_params.get("end", "").strip()
    if not start and not end:
        now = datetime.now(UTC)
        start, end = now.isoformat(), (now + timedelta(days=DEFAULT_DAYS)).isoformat()
    elif not end:
        end = (calendar._parse_time(start) + timedelta(days=DEFAULT_DAYS)).isoformat()
    elif not start:
        start = datetime.now(UTC).isoformat()
    result = calendar.events_for(uid, start, end, request.query_params.get("limit", "50"))
    query = request.query_params.get("query", "").strip().casefold()
    if query:
        result.events = [event for event in result.events if query in event.title.casefold()]
    return result


@router.post("/events", response_model=models.OkResponse, response_model_exclude_none=True)
async def create_event(payload: models.CalendarEventRequest, uid: Caller) -> models.OkResponse:
    return await run_in_threadpool(calendar.create_for, uid, payload.model_dump())


@router.put("/events/{event_id}", response_model=models.OkResponse, response_model_exclude_none=True)
async def update_event(payload: models.CalendarEventRequest, event_id: str, uid: Caller) -> models.OkResponse:
    return await run_in_threadpool(calendar.update_for, uid, event_id, payload.model_dump())


@router.delete("/events/{event_id}", response_model=models.OkResponse, response_model_exclude_none=True)
async def delete_event(
    event_id: str, uid: Caller, payload: Annotated[models.CalendarDeleteRequest | None, Body()] = None
) -> models.OkResponse:
    return await run_in_threadpool(calendar.delete_for, uid, event_id, payload.revision if payload else "")
