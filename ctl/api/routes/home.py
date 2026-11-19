"""Home: each person's widget board, and the outside data its widgets show."""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from ctl import home_board, home_widgets, thermostat
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, Member, Operator, OperatorMutation, Owner, OwnerMutation

router = APIRouter(prefix="/api/v1/home", tags=["home"], route_class=ContractRoute)


def _board(uid: str, is_admin: bool) -> models.HomeBoard:
    widgets, customized = home_board.load(uid, is_admin=is_admin)
    return models.HomeBoard.model_validate(
        {
            "widgets": widgets,
            "customized": customized,
            "columns": home_board.COLUMNS,
            "kinds": home_board.kinds(is_admin=is_admin),
        }
    )


@router.get("/board", response_model=models.HomeBoard)
def read_board(person: Owner) -> models.HomeBoard:
    return _board(str(person["subject_id"]), bool(person.get("is_admin")))


@router.put("/board", response_model=models.HomeBoard)
def save_board(payload: models.HomeBoardRequest, person: OwnerMutation) -> models.HomeBoard:
    uid, is_admin = str(person["subject_id"]), bool(person.get("is_admin"))
    try:
        home_board.save(uid, payload.widgets, is_admin=is_admin)
    except home_board.BoardError as exc:
        raise ApiError(400, str(exc), code="board_invalid") from None
    return _board(uid, is_admin)


@router.delete("/board", response_model=models.HomeBoard)
def reset_board(person: OwnerMutation) -> models.HomeBoard:
    """Back to the starter set."""
    home_board.reset(str(person["subject_id"]))
    return _board(str(person["subject_id"]), bool(person.get("is_admin")))


@router.get("/weather", response_model=models.WeatherResponse)
async def read_weather(
    _member: Member,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    units: str = "celsius",
) -> models.WeatherResponse:
    try:
        data = await run_in_threadpool(home_widgets.weather, latitude, longitude, units)
    except home_widgets.WidgetError as exc:
        raise ApiError(503, str(exc), code="weather_unavailable") from None
    return models.WeatherResponse.model_validate(data)


@router.get("/places", response_model=models.PlacesResponse)
async def search_places(_member: Member, q: str = "") -> models.PlacesResponse:
    try:
        found = await run_in_threadpool(home_widgets.places, q)
    except home_widgets.WidgetError as exc:
        raise ApiError(503, str(exc), code="places_unavailable") from None
    return models.PlacesResponse.model_validate({"places": found})


@router.get("/stocks", response_model=models.StocksResponse)
async def read_stocks(_member: Member, symbols: str = "") -> models.StocksResponse:
    wanted = home_board.settings("stocks", {"symbols": symbols.split(",")})["symbols"]
    try:
        quotes = await run_in_threadpool(home_widgets.stocks, wanted)
    except home_widgets.WidgetError as exc:
        raise ApiError(503, str(exc), code="stocks_unavailable") from None
    return models.StocksResponse.model_validate({"quotes": quotes})


@router.post("/ask", response_model=models.AskResponse)
async def ask(payload: models.AskRequest, person: OwnerMutation) -> models.AskResponse:
    name = str(person.get("display_name") or person.get("username") or "").split(" ")[0]
    try:
        answer = await run_in_threadpool(
            lambda: home_widgets.ask(payload.question, person=name, uid=str(person["subject_id"]))
        )
    except home_widgets.WidgetError as exc:
        raise ApiError(503, str(exc), code="ask_unavailable") from None
    return models.AskResponse(answer=answer)


def _audit(admin: IdentityData, event: str, detail: str) -> None:
    runtime.job_store().record_audit(actor=str(admin["username"]), event=event, detail=detail)


def _thermostat_error(exc: thermostat.ThermostatError) -> ApiError:
    return ApiError(503, str(exc), code="thermostat_unavailable")


@router.get("/thermostat", response_model=models.ThermostatStatus)
async def read_thermostat(_member: Member) -> models.ThermostatStatus:
    try:
        return models.ThermostatStatus.model_validate(await thermostat.status())
    except thermostat.ThermostatError as exc:
        raise _thermostat_error(exc) from None


@router.post("/thermostat", response_model=models.ThermostatStatus)
async def change_thermostat(payload: models.ThermostatChange, admin: OperatorMutation) -> models.ThermostatStatus:
    """Everyone sees the thermostat; administrators change it."""
    try:
        reading = await thermostat.change(**payload.model_dump(exclude_none=True))
    except thermostat.ThermostatError as exc:
        raise _thermostat_error(exc) from None
    _audit(admin, "thermostat.changed", _said(payload))
    return models.ThermostatStatus.model_validate(reading)


def _said(change: models.ThermostatChange) -> str:
    parts = [f"mode {change.mode}" if change.mode else ""]
    parts += [
        f"{label} {value:g} °C"
        for label, value in (("target", change.target), ("heat to", change.heat_to), ("cool to", change.cool_to))
        if value is not None
    ]
    return "Thermostat: " + ", ".join(part for part in parts if part) + "."


@router.get("/devices/thermostat", response_model=models.HomeKitDevices)
async def find_thermostats(_admin: Operator) -> models.HomeKitDevices:
    """HomeKit thermostats on the home network (takes several seconds)."""
    try:
        return models.HomeKitDevices.model_validate({"devices": await thermostat.discover()})
    except thermostat.ThermostatError as exc:
        raise _thermostat_error(exc) from None


@router.post("/devices/thermostat", response_model=models.ThermostatStatus)
async def pair_thermostat(payload: models.ThermostatPairRequest, admin: OperatorMutation) -> models.ThermostatStatus:
    try:
        paired = await thermostat.pair(payload.device_id, payload.code)
        reading = await thermostat.status()
    except thermostat.ThermostatError as exc:
        raise ApiError(409, str(exc), code="thermostat_pairing_failed") from None
    _audit(admin, "thermostat.paired", f"Paired {paired['name']} with Mu3Lab.")
    return models.ThermostatStatus.model_validate(reading)


@router.delete("/devices/thermostat", response_model=models.ThermostatStatus)
async def unpair_thermostat(admin: OperatorMutation) -> models.ThermostatStatus:
    await thermostat.unpair()
    _audit(admin, "thermostat.removed", "Removed the thermostat from Mu3Lab.")
    return models.ThermostatStatus(paired=False)
