"""Each person's Home board: which widgets they show, where, and at what size.

The board is a grid as wide as the page, with as many columns as the person chooses (``COLUMN_CHOICES``)
and rows of a fixed height; it grows downward. Each widget sits at column ``x``, row ``y`` and takes ``w``
columns and ``h`` rows, so it can be moved and sized freely; gaps are allowed. A phone shows the same
widgets in one column, top-left first. Each person edits only their own board; anyone who has not
changed theirs sees ``STARTER``.

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
MAX_ROW = 200
MAX_WIDGETS = 24
MAX_SYMBOLS = 12
SYMBOL = re.compile(r"^[A-Za-z0-9.^=\-]{1,15}$")
WIDGET_ID = re.compile(r"^[a-z0-9-]{1,40}$")


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
    "thermostat": Kind((2, 2), (6, 6), (3, 2)),
    "camera": Kind((3, 2), (12, 12), (6, 4), admin_only=True),
}

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
        chosen: dict[str, Any] = {"units": "fahrenheit" if given.get("units") == "fahrenheit" else "celsius"}
        if place := _place(given.get("place")):
            chosen["place"] = place
        return chosen
    if kind == "stocks":
        symbols: list[str] = []
        for item in given.get("symbols") or []:
            symbol = str(item).strip().upper()
            if SYMBOL.match(symbol) and symbol not in symbols:
                symbols.append(symbol)
        return {"symbols": symbols[:MAX_SYMBOLS]}
    if kind == "calendar":
        # auto: a wide, short widget opens on the week, a square or tall one on the month.
        view = given.get("view")
        week_start = given.get("week_start")
        return {
            "view": view if view in ("auto", "month", "week") else "auto",
            "week_start": week_start if week_start in ("sunday", "monday") else "sunday",
        }
    return {}


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


def _widget(value: Any, *, is_admin: bool, columns: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    kind = kind_of(str(value.get("kind")), is_admin=is_admin)
    if kind is None:
        return None
    widget_id = str(value.get("id") or "")
    if not WIDGET_ID.match(widget_id):
        widget_id = new_id(str(value["kind"]))
    min_w, max_w, min_h, max_h = limits(kind, columns)
    width = min(max(_number(value.get("w"), scaled(kind.start[0], columns)), min_w), max_w)
    height = min(max(_number(value.get("h"), kind.start[1]), min_h), max_h)
    placed = "x" in value and "y" in value
    return {
        "id": widget_id,
        "kind": str(value["kind"]),
        "x": min(max(_number(value.get("x"), 0), 0), columns - width) if placed else -1,
        "y": min(max(_number(value.get("y"), 0), 0), MAX_ROW) if placed else -1,
        "w": width,
        "h": height,
        # A phone shows the board as one column; some widgets are only worth a computer screen.
        "hide_on_phone": value.get("hide_on_phone") is True,
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
    if len(widgets) > MAX_WIDGETS:
        raise BoardError(f"A board holds at most {MAX_WIDGETS} widgets.")
    board: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in widgets:
        widget = _widget(value, is_admin=is_admin, columns=columns)
        if widget is None:
            continue
        while widget["id"] in seen:
            widget["id"] = new_id(widget["kind"])
        seen.add(widget["id"])
        board.append(widget)
    _settle(board, columns)
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


def load(uid: str, *, is_admin: bool) -> Board:
    """A person's board: its widgets, columns and placement, and whether they changed it from the starter set."""
    saved = records.get(SCOPE, uid) if uid else {}
    if "widgets" not in saved:
        return Board(clean(STARTER, is_admin=is_admin), BASE_COLUMNS, PLACEMENTS[0], False)
    columns = columns_of(saved.get("columns"))
    widgets = saved["widgets"] if "columns" in saved else _upgrade(saved["widgets"])
    try:
        board = clean(widgets, is_admin=is_admin, columns=columns)
    except BoardError:
        return Board(clean(STARTER, is_admin=is_admin), BASE_COLUMNS, PLACEMENTS[0], False)
    return Board(board, columns, placement_of(saved.get("placement")), True)


def save(
    uid: str, widgets: Any, *, is_admin: bool, columns: Any = BASE_COLUMNS, placement: Any = PLACEMENTS[0]
) -> list[dict[str, Any]]:
    chosen = columns_of(columns)
    board = clean(widgets, is_admin=is_admin, columns=chosen)
    records.put(SCOPE, uid, {"columns": chosen, "placement": placement_of(placement), "widgets": board})
    return board


def reset(uid: str) -> None:
    records.delete(SCOPE, uid)


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
            }
        )
    return found
