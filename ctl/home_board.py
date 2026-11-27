"""Each person's Home board: which widgets they show, where, and at what size.

The board is a grid as wide as the page, with as many columns as the person chooses (``COLUMN_CHOICES``)
and rows of a fixed height; it grows downward. Each widget sits at column ``x``, row ``y`` and takes ``w``
columns and ``h`` rows, so it can be moved and sized freely; gaps are allowed. A phone shows the same
widgets in one column, top-left first. Each person edits only their own board; anyone who has not
changed theirs sees ``STARTER``.

A group (kind ``group``) is a titled card holding other widgets on a small grid of its own: its
``children`` sit at columns and rows counted from the group's top-left, as many columns across as the
group is wide, and the group is one row (its title) taller than what it holds. Groups do not nest.

A person can keep several boards as tabs (``tabs``): each tab is a whole board with its own columns,
placement and widgets. Someone who never changed theirs has one tab, "Home", showing ``STARTER``; a
board saved before tabs existed reads as that one tab.

Any widget can carry its own ``title`` (shown in place of its kind's name) and ``hide_title`` (no title
bar outside edit mode). A group keeps its title in its settings, with ``collapsed`` (only its title bar
shows, and the widgets below it move up while it stays collapsed).

Each kind's size limits are set on a 12-column grid and scaled to the person's columns. Widget settings
stay small and are checked here, so a board read back is always one the dashboard can draw. Widgets
only an administrator may use are dropped from anyone else's board.
"""

from __future__ import annotations

import math
import re
import secrets
from dataclasses import dataclass
from typing import Any

from ctl import app_widgets
from ctl.store import records

SCOPE = "home_board"
# Kinds' sizes are written for this many columns; a board with other columns scales them.
BASE_COLUMNS = 12
COLUMN_CHOICES = (4, 6, 8, 12, 16, 24)
# How widgets sit, chosen by each person: where they are put (gaps allowed), or packed to the top.
PLACEMENTS = ("free", "gravity")
COLUMNS = BASE_COLUMNS
# A board laid out before the grid could be chosen: four columns, rows twice today's height.
LEGACY_COLUMNS = 4
LEGACY_ROW = 2
# Only guards against a nonsense position; a board has no limit on how many widgets it holds.
MAX_ROW = 10_000
MAX_SYMBOLS = 12
# The weather widget's forecast length, and how far back the stocks widget compares (Yahoo ranges).
FORECAST_DAYS = (3, 5, 7, 10, 14)
STOCK_RANGES = ("1d", "5d", "1mo", "6mo", "ytd", "1y", "5y")
# What an app's widget shows: its figures and its list, or only one of them.
APP_SHOWS = ("both", "figures", "list")
SYMBOL = re.compile(r"^[A-Za-z0-9.^=\-]{1,15}$")
MAX_CLOCKS = 6
MAX_LINKS = 40
MAX_FEEDS = 8
# What the conversion widget converts; currency rates come from the European Central Bank.
CONVERT_KINDS = ("length", "weight", "temperature", "volume", "cooking", "speed", "area", "data", "currency")
UNIT = re.compile(r"^[A-Za-z0-9_ ]{1,16}$")
# Yahoo Finance's lists of the day's movers, or the person's own tickers.
MOVER_LISTS = ("gainers", "losers", "active", "mine")
WIDGET_ID = re.compile(r"^[a-z0-9-]{1,40}$")
MAX_TABS = 12
MAX_TAB_NAME = 30
FIRST_TAB = "home"


@dataclass(frozen=True)
class Kind:
    # Smallest and largest size, and the size a newly added one starts at: (columns of 12, rows).
    smallest: tuple[int, int]
    largest: tuple[int, int]
    start: tuple[int, int]
    admin_only: bool = False


KINDS: dict[str, Kind] = {
    "apps": Kind((2, 2), (12, 8), (6, 2)),
    "weather": Kind((2, 2), (12, 8), (6, 2)),
    "calendar": Kind((2, 2), (12, 14), (6, 4)),
    "stocks": Kind((2, 2), (12, 10), (6, 2)),
    "ask": Kind((3, 2), (12, 14), (6, 4)),
    "search": Kind((3, 2), (12, 14), (6, 4)),
    "thermostat": Kind((2, 2), (6, 6), (3, 2)),
    "camera": Kind((3, 2), (12, 12), (6, 4), admin_only=True),
    "group": Kind((2, 2), (12, 60), (6, 3)),
    "clock": Kind((2, 1), (12, 6), (3, 2)),
    "bookmarks": Kind((2, 1), (12, 10), (4, 2)),
    "convert": Kind((2, 2), (12, 6), (4, 3)),
    "rss": Kind((2, 2), (12, 14), (4, 4)),
    "resources": Kind((2, 2), (12, 6), (4, 2)),
    "movers": Kind((2, 1), (12, 4), (12, 1)),
}
GROUP = "group"
MAX_TITLE = 40

# Weather and Apps across the top, then Ask (room for an answer) beside the week's events.
STARTER: list[dict[str, Any]] = [
    {"id": "weather", "kind": "weather", "x": 0, "y": 0, "w": 6, "h": 2, "settings": {}},
    {"id": "apps", "kind": "apps", "x": 6, "y": 0, "w": 6, "h": 2, "settings": {}},
    {"id": "ask", "kind": "ask", "x": 0, "y": 2, "w": 6, "h": 4, "settings": {}},
    {"id": "calendar", "kind": "calendar", "x": 6, "y": 2, "w": 6, "h": 4, "settings": {}},
]


def scaled(width: int, columns: int, *, up: bool = False) -> int:
    """A width written for ``BASE_COLUMNS`` on a board of ``columns`` (at least one column)."""
    exact = width * columns / BASE_COLUMNS
    return max(1, min(columns, math.ceil(exact) if up else round(exact)))


def limits(kind: Kind, columns: int) -> tuple[int, int, int, int]:
    """(min w, max w, min h, max h) of a kind on a board of ``columns``."""
    return (
        scaled(kind.smallest[0], columns, up=True),
        scaled(kind.largest[0], columns),
        kind.smallest[1],
        kind.largest[1],
    )


class BoardError(ValueError):
    """A board the dashboard sent that cannot be saved; the message is for the person."""


def _place(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        latitude, longitude = float(value["latitude"]), float(value["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    name = str(value.get("name") or "").strip()[:80]
    return {"name": name or f"{latitude:.2f}, {longitude:.2f}", "latitude": latitude, "longitude": longitude}


def settings(kind: str, value: Any) -> dict[str, Any]:
    """Only the settings each kind understands, each checked; anything else is dropped."""
    given = value if isinstance(value, dict) else {}
    if kind == "weather":
        days = _number(given.get("days"), 7)
        chosen: dict[str, Any] = {
            "units": "fahrenheit" if given.get("units") == "fahrenheit" else "celsius",
            "days": days if days in FORECAST_DAYS else 7,
        }
        if place := _place(given.get("place")):
            chosen["place"] = place
        return chosen
    if kind == "stocks":
        symbols: list[str] = []
        for item in given.get("symbols") or []:
            symbol = str(item).strip().upper()
            if SYMBOL.match(symbol) and symbol not in symbols:
                symbols.append(symbol)
        span = given.get("range")
        return {"symbols": symbols[:MAX_SYMBOLS], "range": span if span in STOCK_RANGES else STOCK_RANGES[0]}
    if kind == GROUP:
        title = " ".join(str(given.get("title") or "").split())[:MAX_TITLE]
        return {"title": title or "Group", "collapsed": given.get("collapsed") is True}
    if kind == "calendar":
        # auto: a wide, short widget opens on the week, a square or tall one on the month.
        view = given.get("view")
        week_start = given.get("week_start")
        return {
            "view": view if view in ("auto", "month", "week") else "auto",
            "week_start": week_start if week_start in ("sunday", "monday") else "sunday",
        }
    if kind == "clock":
        return _clock(given)
    if kind == "bookmarks":
        return _bookmarks(given)
    if kind == "convert":
        what = given.get("kind")
        chosen = {"kind": what if what in CONVERT_KINDS else CONVERT_KINDS[0]}
        for key in ("from", "to"):
            unit = str(given.get(key) or "")
            if UNIT.match(unit):
                chosen[key] = unit
        return chosen
    if kind == "rss":
        feeds: list[str] = []
        for item in given.get("feeds") or []:
            url = str(item or "").strip()
            if url.lower().startswith(("http://", "https://")) and len(url) <= 500 and url not in feeds:
                feeds.append(url)
        return {"feeds": feeds[:MAX_FEEDS], "summaries": given.get("summaries") is True}
    if kind == "movers":
        which = given.get("list")
        tickers = [str(item).strip().upper() for item in given.get("symbols") or []]
        return {
            "list": which if which in MOVER_LISTS else MOVER_LISTS[0],
            "symbols": [symbol for symbol in dict.fromkeys(tickers) if SYMBOL.match(symbol)][: MAX_SYMBOLS * 2],
            "still": given.get("still") is True,
        }
    if app_widgets.SEPARATOR in kind:
        show = given.get("show")
        return {"show": show if show in APP_SHOWS else APP_SHOWS[0]} | app_widgets.own_settings(kind, given)
    return {}


def _clock(given: dict[str, Any]) -> dict[str, Any]:
    """Clocks: the device's own time first unless left out, then up to ``MAX_CLOCKS`` places."""
    from zoneinfo import available_timezones

    known = available_timezones()
    zones: list[dict[str, str]] = []
    for item in given.get("zones") or []:
        if not isinstance(item, dict):
            continue
        zone = str(item.get("zone") or "")
        if zone in known and all(found["zone"] != zone for found in zones):
            label = " ".join(str(item.get("label") or "").split())[:30]
            zones.append({"zone": zone, "label": label})
    hours = given.get("hours")
    return {
        "zones": zones[:MAX_CLOCKS],
        "local": given.get("local") is not False,
        "hours": hours if hours in ("auto", "12", "24") else "auto",
        "seconds": given.get("seconds") is True,
        "date": given.get("date") is not False,
    }


def _bookmarks(given: dict[str, Any]) -> dict[str, Any]:
    links: list[dict[str, str]] = []
    for item in given.get("links") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")) or len(url) > 2000:
            continue
        label = " ".join(str(item.get("label") or "").split())[:40]
        links.append({"url": url, "label": label})
    layout = given.get("layout")
    return {"links": links[:MAX_LINKS], "layout": layout if layout in ("tiles", "list") else "tiles"}


def _number(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def kind_of(name: str, *, is_admin: bool) -> Kind | None:
    """A kind this person may have on their board: Mu3Lab's own, or a widget an app offers.

    An app widget stays valid after its app is removed; it then says so, and the person removes it.
    """
    kind = KINDS.get(name)
    if kind is None:
        widget = app_widgets.find(name, is_admin=is_admin)
        if widget is None:
            return None
        spec = widget.spec
        kind = Kind(spec.smallest, spec.largest, spec.start, admin_only=widget.admin_only)
    return None if kind.admin_only and not is_admin else kind


def _widget(value: Any, *, is_admin: bool, columns: int, space: int, nested: bool) -> dict[str, Any] | None:
    """One widget, checked: sizes from the board's ``columns``, placed within ``space`` columns (a group's
    width, inside one). ``nested``: inside a group, where another group is not allowed."""
    if not isinstance(value, dict):
        return None
    if nested and value.get("kind") == GROUP:
        return None
    kind = kind_of(str(value.get("kind")), is_admin=is_admin)
    if kind is None:
        return None
    widget_id = str(value.get("id") or "")
    if not WIDGET_ID.match(widget_id):
        widget_id = new_id(str(value["kind"]))
    min_w, max_w, min_h, max_h = limits(kind, columns)
    width = min(max(_number(value.get("w"), scaled(kind.start[0], columns)), min_w), max_w, space)
    height = min(max(_number(value.get("h"), kind.start[1]), min_h), max_h)
    placed = "x" in value and "y" in value
    return {
        "id": widget_id,
        "kind": str(value["kind"]),
        "x": min(max(_number(value.get("x"), 0), 0), space - width) if placed else -1,
        "y": min(max(_number(value.get("y"), 0), 0), MAX_ROW) if placed else -1,
        "w": width,
        "h": height,
        # A phone shows the board as one column; some widgets are only worth a computer screen.
        "hide_on_phone": value.get("hide_on_phone") is True,
        # The person's own name for it (a group's is in its settings), and no title bar outside edit mode.
        "title": "" if value.get("kind") == GROUP else " ".join(str(value.get("title") or "").split())[:MAX_TITLE],
        "hide_title": value.get("hide_title") is True and value.get("kind") != GROUP,
        "settings": settings(str(value["kind"]), value.get("settings")),
    }


def new_id(kind: str) -> str:
    return f"{kind}-{secrets.token_hex(3)}"


def _fits(taken: set[tuple[int, int]], x: int, y: int, w: int, h: int) -> bool:
    return all((col, row) not in taken for col in range(x, x + w) for row in range(y, y + h))


def _take(taken: set[tuple[int, int]], widget: dict[str, Any]) -> None:
    taken.update(
        (col, row)
        for col in range(widget["x"], widget["x"] + widget["w"])
        for row in range(widget["y"], widget["y"] + widget["h"])
    )


def _settle(board: list[dict[str, Any]], columns: int) -> None:
    """Place widgets with no position (or one that overlaps another) in the first space that fits, in order."""
    taken: set[tuple[int, int]] = set()
    waiting = []
    for widget in board:
        if widget["x"] >= 0 and _fits(taken, widget["x"], widget["y"], widget["w"], widget["h"]):
            _take(taken, widget)
        else:
            waiting.append(widget)
    for widget in waiting:
        y = 0
        while True:
            spot = next(
                (x for x in range(columns - widget["w"] + 1) if _fits(taken, x, y, widget["w"], widget["h"])),
                None,
            )
            if spot is not None:
                widget["x"], widget["y"] = spot, y
                _take(taken, widget)
                break
            y += 1


def columns_of(value: Any) -> int:
    number = _number(value, BASE_COLUMNS)
    return number if number in COLUMN_CHOICES else BASE_COLUMNS


def clean(widgets: Any, *, is_admin: bool, columns: int = BASE_COLUMNS) -> list[dict[str, Any]]:
    """The board as it is stored and drawn: known kinds, sizes in range, ids unique, nothing overlapping."""
    if not isinstance(widgets, list):
        raise BoardError("The board must be a list of widgets.")
    return _cleaned(widgets, is_admin=is_admin, columns=columns, space=columns, seen=set())


def _cleaned(
    widgets: list[Any], *, is_admin: bool, columns: int, space: int, seen: set[str], nested: bool = False
) -> list[dict[str, Any]]:
    board: list[dict[str, Any]] = []
    for value in widgets:
        widget = _widget(value, is_admin=is_admin, columns=columns, space=space, nested=nested)
        if widget is None:
            continue
        while widget["id"] in seen:
            widget["id"] = new_id(widget["kind"])
        seen.add(widget["id"])
        if widget["kind"] == GROUP:
            given = value.get("children")
            children = _cleaned(
                given if isinstance(given, list) else [],
                is_admin=is_admin,
                columns=columns,
                space=widget["w"],
                seen=seen,
                nested=True,
            )
            widget["children"] = children
            # One row for the title, then as many as its widgets reach down to.
            bottom = max((child["y"] + child["h"] for child in children), default=0)
            _, _, min_h, max_h = limits(KINDS[GROUP], columns)
            widget["h"] = min(max(bottom + 1, min_h), max_h)
        board.append(widget)
    _settle(board, space)
    return board


def _upgrade(widgets: list[Any]) -> list[Any]:
    """A board saved before positions: four columns in saved order, as widths and heights on today's grid."""
    factor = BASE_COLUMNS // LEGACY_COLUMNS
    return [
        {key: item for key, item in value.items() if key not in ("x", "y")}
        | {"w": _number(value.get("w"), 1) * factor, "h": _number(value.get("h"), 1) * LEGACY_ROW}
        for value in widgets
        if isinstance(value, dict)
    ]


def placement_of(value: Any) -> str:
    return value if value in PLACEMENTS else PLACEMENTS[0]


@dataclass(frozen=True)
class Board:
    widgets: list[dict[str, Any]]
    columns: int
    placement: str
    # False until the person changes their board: they see the starter set.
    customized: bool
    # Which tab this is, and every tab's id and name in the person's order.
    tab: str = FIRST_TAB
    tabs: tuple[dict[str, str], ...] = ({"id": FIRST_TAB, "name": "Home"},)


def _tab_name(value: Any, fallback: str = "Board") -> str:
    return " ".join(str(value or "").split())[:MAX_TAB_NAME] or fallback


def _saved_tabs(uid: str) -> list[dict[str, Any]]:
    """The person's tabs as stored; a board saved before tabs is its first and only tab."""
    saved = records.get(SCOPE, uid) if uid else {}
    if isinstance(saved.get("tabs"), list):
        tabs = []
        for value in saved["tabs"]:
            if isinstance(value, dict) and WIDGET_ID.match(str(value.get("id") or "")):
                tabs.append(value)
        if tabs:
            return tabs[:MAX_TABS]
    if "widgets" in saved:
        widgets = saved["widgets"] if "columns" in saved else _upgrade(saved["widgets"])
        return [
            {
                "id": FIRST_TAB,
                "name": "Home",
                "columns": saved.get("columns", BASE_COLUMNS),
                "placement": saved.get("placement"),
                "widgets": widgets,
            }
        ]
    return []


def _store(uid: str, tabs: list[dict[str, Any]]) -> None:
    records.put(SCOPE, uid, {"tabs": tabs})


def _pick(tabs: list[dict[str, Any]], tab: str) -> dict[str, Any] | None:
    return next((item for item in tabs if item["id"] == tab), tabs[0] if tabs else None)


def _names(tabs: list[dict[str, Any]]) -> tuple[dict[str, str], ...]:
    return tuple({"id": str(item["id"]), "name": _tab_name(item.get("name"), "Home")} for item in tabs)


def load(uid: str, *, is_admin: bool, tab: str = "") -> Board:
    """One of the person's boards (``tab``, else the first): its widgets, columns and placement, the
    names of all their tabs, and whether they changed anything from the starter set."""
    tabs = _saved_tabs(uid)
    chosen = _pick(tabs, tab)
    if chosen is None:
        return Board(clean(STARTER, is_admin=is_admin), BASE_COLUMNS, PLACEMENTS[0], False)
    columns = columns_of(chosen.get("columns"))
    try:
        board = clean(chosen.get("widgets") or [], is_admin=is_admin, columns=columns)
    except BoardError:
        board = clean(STARTER, is_admin=is_admin) if chosen is tabs[0] else []
    return Board(board, columns, placement_of(chosen.get("placement")), True, str(chosen["id"]), _names(tabs))


def save(
    uid: str,
    widgets: Any,
    *,
    is_admin: bool,
    columns: Any = BASE_COLUMNS,
    placement: Any = PLACEMENTS[0],
    tab: str = "",
) -> list[dict[str, Any]]:
    """Save one tab's board (``tab``, else the first); the other tabs stay as they are."""
    chosen = columns_of(columns)
    board = clean(widgets, is_admin=is_admin, columns=chosen)
    tabs = _saved_tabs(uid) or [{"id": FIRST_TAB, "name": "Home"}]
    target = _pick(tabs, tab)
    if target is None or (tab and target["id"] != tab):
        raise BoardError("That board was removed. Reload the page.")
    target.update({"columns": chosen, "placement": placement_of(placement), "widgets": board})
    _store(uid, tabs)
    return board


def reset(uid: str, tab: str = "") -> None:
    """Back to the starter set: the whole record when there is one tab, else only that tab's widgets."""
    tabs = _saved_tabs(uid)
    if len(tabs) <= 1:
        records.delete(SCOPE, uid)
        return
    target = _pick(tabs, tab)
    if target is not None:
        target.update(
            {"columns": BASE_COLUMNS, "placement": PLACEMENTS[0], "widgets": [dict(item) for item in STARTER]}
        )
        _store(uid, tabs)


def save_tabs(uid: str, wanted: Any) -> tuple[dict[str, str], ...]:
    """Rename, reorder, add and remove tabs in one go: ``wanted`` is the whole list, in order. A tab
    without a known id is new (and empty); a tab left out is removed with its widgets."""
    if not isinstance(wanted, list) or not wanted:
        raise BoardError("Keep at least one board.")
    if len(wanted) > MAX_TABS:
        raise BoardError(f"Keep at most {MAX_TABS} boards.")
    tabs = _saved_tabs(uid)
    if not tabs:
        tabs = [
            {"id": FIRST_TAB, "name": "Home", "columns": BASE_COLUMNS, "placement": PLACEMENTS[0], "widgets": STARTER}
        ]
    known = {item["id"]: item for item in tabs}
    result: list[dict[str, Any]] = []
    for value in wanted:
        if not isinstance(value, dict):
            continue
        tab_id = str(value.get("id") or "")
        name = _tab_name(value.get("name"))
        if tab_id in known and all(item["id"] != tab_id for item in result):
            result.append({**known[tab_id], "name": name})
            continue
        new = new_id("board")
        result.append({"id": new, "name": name, "columns": BASE_COLUMNS, "placement": PLACEMENTS[0], "widgets": []})
    if not result:
        raise BoardError("Keep at least one board.")
    _store(uid, result)
    return _names(result)


def kinds(*, is_admin: bool) -> list[dict[str, Any]]:
    """What the dashboard may offer this person in "Add widget", with each kind's size limits.

    Widths are columns of ``BASE_COLUMNS``; the dashboard scales them to the board's own columns
    (``scaled``). Mu3Lab's own widgets come first, then those of each installed app.
    """

    def entry(name: str, kind: Kind) -> dict[str, Any]:
        return {
            "kind": name,
            "min_w": kind.smallest[0],
            "min_h": kind.smallest[1],
            "max_w": kind.largest[0],
            "max_h": kind.largest[1],
            "w": kind.start[0],
            "h": kind.start[1],
        }

    found = [entry(name, kind) for name, kind in KINDS.items() if is_admin or not kind.admin_only]
    for widget in app_widgets.offered(is_admin=is_admin):
        spec = widget.spec
        found.append(
            entry(widget.kind, Kind(spec.smallest, spec.largest, spec.start, admin_only=widget.admin_only))
            | {
                "title": spec.title,
                "description": spec.description,
                "app_id": widget.app.id,
                "app_name": widget.app.manifest.name,
                "refresh_seconds": spec.refresh_seconds,
                "personal": spec.personal,
                "key_label": spec.key.label if spec.key else None,
                "key_help": spec.key.help if spec.key else None,
                "view": spec.view,
            }
        )
    return found
