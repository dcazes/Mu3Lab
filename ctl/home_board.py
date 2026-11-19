"""Each person's Home board: which widgets they show, in what order, at what size.

The board is a grid four columns wide on a computer (two on a tablet, one on a phone). Widgets
flow in the saved order and each takes ``w`` columns and ``h`` rows, so a phone shows the same
board as one column in the same order. Each person edits only their own board; anyone who has
not changed theirs sees ``STARTER``.

Widget settings stay small and are checked here, so a board read back is always one the
dashboard can draw. Widgets only an administrator may use are dropped from anyone else's board.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any

from ctl.store import records

SCOPE = "home_board"
COLUMNS = 4
MAX_ROWS = 4
MAX_WIDGETS = 24
MAX_SYMBOLS = 12
SYMBOL = re.compile(r"^[A-Za-z0-9.^=\-]{1,15}$")
WIDGET_ID = re.compile(r"^[a-z0-9-]{1,40}$")


@dataclass(frozen=True)
class Kind:
    # Smallest and largest size, and the size a newly added one starts at: (columns, rows).
    smallest: tuple[int, int]
    largest: tuple[int, int]
    start: tuple[int, int]
    admin_only: bool = False


KINDS: dict[str, Kind] = {
    "apps": Kind((1, 1), (4, 2), (4, 1)),
    "weather": Kind((1, 1), (4, 2), (2, 1)),
    "calendar": Kind((1, 1), (4, 4), (2, 2)),
    "stocks": Kind((1, 1), (4, 3), (2, 1)),
    "ask": Kind((2, 2), (4, 4), (2, 2)),
    "thermostat": Kind((1, 1), (2, 2), (1, 1)),
    "camera": Kind((1, 1), (4, 4), (2, 2), admin_only=True),
}

# Weather and Apps across the top, then Ask (room for an answer) beside the week's events.
STARTER: list[dict[str, Any]] = [
    {"id": "weather", "kind": "weather", "w": 2, "h": 1, "settings": {}},
    {"id": "apps", "kind": "apps", "w": 2, "h": 1, "settings": {}},
    {"id": "ask", "kind": "ask", "w": 2, "h": 2, "settings": {}},
    {"id": "calendar", "kind": "calendar", "w": 2, "h": 2, "settings": {}},
]


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
        days = given.get("days")
        return {"days": days if days in (1, 3, 7, 14) else 7}
    return {}


def _widget(value: Any, *, is_admin: bool) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    kind = KINDS.get(str(value.get("kind")))
    if kind is None or (kind.admin_only and not is_admin):
        return None
    widget_id = str(value.get("id") or "")
    if not WIDGET_ID.match(widget_id):
        widget_id = new_id(str(value["kind"]))
    try:
        width, height = int(value.get("w") or kind.start[0]), int(value.get("h") or kind.start[1])
    except (TypeError, ValueError):
        width, height = kind.start
    return {
        "id": widget_id,
        "kind": str(value["kind"]),
        "w": min(max(width, kind.smallest[0]), kind.largest[0]),
        "h": min(max(height, kind.smallest[1]), kind.largest[1]),
        "settings": settings(str(value["kind"]), value.get("settings")),
    }


def new_id(kind: str) -> str:
    return f"{kind}-{secrets.token_hex(3)}"


def clean(widgets: Any, *, is_admin: bool) -> list[dict[str, Any]]:
    """The board as it is stored and drawn: known kinds, sizes in range, ids unique."""
    if not isinstance(widgets, list):
        raise BoardError("The board must be a list of widgets.")
    if len(widgets) > MAX_WIDGETS:
        raise BoardError(f"A board holds at most {MAX_WIDGETS} widgets.")
    board: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in widgets:
        widget = _widget(value, is_admin=is_admin)
        if widget is None:
            continue
        while widget["id"] in seen:
            widget["id"] = new_id(widget["kind"])
        seen.add(widget["id"])
        board.append(widget)
    return board


def load(uid: str, *, is_admin: bool) -> tuple[list[dict[str, Any]], bool]:
    """A person's board and whether they have changed it from the starter set."""
    saved = records.get(SCOPE, uid) if uid else {}
    if "widgets" not in saved:
        return clean(STARTER, is_admin=is_admin), False
    try:
        return clean(saved["widgets"], is_admin=is_admin), True
    except BoardError:
        return clean(STARTER, is_admin=is_admin), False


def save(uid: str, widgets: Any, *, is_admin: bool) -> list[dict[str, Any]]:
    board = clean(widgets, is_admin=is_admin)
    records.put(SCOPE, uid, {"widgets": board})
    return board


def reset(uid: str) -> None:
    records.delete(SCOPE, uid)


def kinds(*, is_admin: bool) -> list[dict[str, Any]]:
    """What the dashboard may offer this person in "Add widget", with each kind's size limits."""
    return [
        {
            "kind": name,
            "min_w": kind.smallest[0],
            "min_h": kind.smallest[1],
            "max_w": kind.largest[0],
            "max_h": kind.largest[1],
            "w": kind.start[0],
            "h": kind.start[1],
        }
        for name, kind in KINDS.items()
        if is_admin or not kind.admin_only
    ]
