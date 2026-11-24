"""Home: each person's widget board, and the outside data its widgets show."""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from ctl import app_widgets, chat_ops, chat_recent, home_board, home_search, home_widgets, thermostat
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, Member, MemberMutation, Operator, OperatorMutation, Owner, OwnerMutation

router = APIRouter(prefix="/api/v1/home", tags=["home"], route_class=ContractRoute)


def _board(uid: str, is_admin: bool) -> models.HomeBoard:
    board = home_board.load(uid, is_admin=is_admin)
    return models.HomeBoard.model_validate(
        {
            "widgets": board.widgets,
            "customized": board.customized,
            "columns": board.columns,
            "placement": board.placement,
            "column_choices": list(home_board.COLUMN_CHOICES),
            "base_columns": home_board.BASE_COLUMNS,
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
        home_board.save(uid, payload.widgets, is_admin=is_admin, columns=payload.columns, placement=payload.placement)
    except home_board.BoardError as exc:
        raise ApiError(400, str(exc), code="board_invalid") from None
    return _board(uid, is_admin)


@router.delete("/board", response_model=models.HomeBoard)
def reset_board(person: OwnerMutation) -> models.HomeBoard:
    """Back to the starter set."""
    home_board.reset(str(person["subject_id"]))
    return _board(str(person["subject_id"]), bool(person.get("is_admin")))


@router.get("/widgets/{kind}/data", response_model=models.HomeWidgetData)
async def widget_data(kind: str, person: Member, fresh: bool = False) -> models.HomeWidgetData:
    """An app widget's figures for the person's board; ``fresh`` asks the app again (Refresh)."""
    from ctl.api.service_view import service_states

    is_admin = bool(person.get("is_admin"))
    widget = app_widgets.find(kind, is_admin=is_admin)
    if widget is None:
        raise ApiError(404, "This widget is not available to you.", code="widget_unavailable")
    if fresh:
        app_widgets.forget(kind)
    states = await run_in_threadpool(service_states, person)
    data = await run_in_threadpool(
        app_widgets.read,
        widget,
        service_state=states.get(widget.app.id, "not_installed"),
        is_admin=is_admin,
        uid=str(person["subject_id"]),
        email=str(person.get("email") or ""),
    )
    return models.HomeWidgetData.model_validate(data)


def _widget(kind: str, person: IdentityData) -> app_widgets.Offered:
    widget = app_widgets.find(kind, is_admin=bool(person.get("is_admin")))
    if widget is None:
        raise ApiError(404, "This widget is not available to you.", code="widget_unavailable")
    return widget


@router.post("/widgets/{kind}/action", response_model=models.OkResponse)
async def widget_action(kind: str, payload: models.HomeWidgetAction, person: MemberMutation) -> models.OkResponse:
    """A ticked box in an app widget (a habit done, a chore finished): the app records it."""
    widget = _widget(kind, person)
    try:
        await run_in_threadpool(
            app_widgets.act,
            widget,
            payload.action,
            is_admin=bool(person.get("is_admin")),
            uid=str(person["subject_id"]),
            email=str(person.get("email") or ""),
        )
    except app_widgets.WidgetError as exc:
        raise ApiError(502, str(exc), code="widget_action_failed") from None
    return models.OkResponse(ok=True)


@router.get("/widgets/{kind}/key", response_model=models.HomeWidgetKeyStatus)
def widget_key_status(kind: str, person: Member) -> models.HomeWidgetKeyStatus:
    """Whether a key is saved for this widget; the key itself is never sent back."""
    widget = _widget(kind, person)
    return models.HomeWidgetKeyStatus(saved=app_widgets.has_key(widget, uid=str(person["subject_id"])))


@router.put("/widgets/{kind}/key", response_model=models.HomeWidgetKeyStatus)
def save_widget_key(
    kind: str, payload: models.HomeWidgetKeyRequest, person: MemberMutation
) -> models.HomeWidgetKeyStatus:
    widget = _widget(kind, person)
    if widget.spec.key is None:
        raise ApiError(400, "This widget does not take a key.", code="widget_key_unused")
    # Administrators-only widgets share one key, which only administrators may change.
    app_widgets.save_key(widget, payload.key, uid=str(person["subject_id"]))
    return models.HomeWidgetKeyStatus(saved=bool(payload.key.strip()))


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


@router.get("/ask/chats", response_model=models.AskChats)
async def ask_chats(person: Owner, app: str = "") -> models.AskChats:
    """The chat apps the Ask widget can switch between, and the person's newest chats in one."""
    apps = await run_in_threadpool(chat_recent.choices)
    household = await run_in_threadpool(chat_ops.selected)
    chosen = next((item for item in apps if item.id == app), None) or (apps[0] if apps else None)
    reply: dict = {
        "apps": [{"id": item.id, "name": item.manifest.name} for item in apps],
        "household_id": household.id,
    }
    if chosen is None:
        return models.AskChats.model_validate(reply | {"detail": "No chat app is installed yet."})
    uid = str(person["subject_id"])
    reply |= {
        "app_id": chosen.id,
        "app_name": chosen.manifest.name,
        "prompt_url": await run_in_threadpool(chat_recent.prompt_url, chosen, uid),
    }
    try:
        reply["chats"] = await run_in_threadpool(
            lambda: chat_recent.recent(chosen, uid=uid, email=str(person.get("email") or ""))
        )
    except chat_recent.RecentError as exc:
        reply["detail"] = str(exc)
    return models.AskChats.model_validate(reply)


@router.get("/search", response_model=models.SearchModes)
def search_modes(_member: Member) -> models.SearchModes:
    """Which halves of the Search widget work now."""
    return models.SearchModes.model_validate(home_search.available())


@router.post("/search", response_model=models.SearchResponse)
async def search(payload: models.SearchRequest, _person: MemberMutation) -> models.SearchResponse:
    """The Search widget: SearXNG's results, or a page or a deep search through Firecrawl."""
    try:
        found = await run_in_threadpool(home_search.search, payload.query, payload.mode)
    except home_search.SearchError as exc:
        raise ApiError(503, str(exc), code="search_unavailable") from None
    return models.SearchResponse.model_validate(found)


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
