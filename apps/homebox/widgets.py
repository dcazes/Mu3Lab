"""Home widgets for HomeBox (manifest ``widgets``; ctl/app_widgets.py runs them).

Inventory: how many things the household has recorded, what they are worth, what is under warranty,
and the locations holding the most, with Mu3Lab's HomeBox key.
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

KEY_ENV = "HOMEBOX_MU3LAB_KEY"


def _things(entity: dict[str, Any]) -> int:
    """Things kept in a place, its smaller places included (a shelf in the garage)."""
    return sum(
        _things(child) if child.get("type") == "location" else 1 + _things(child)
        for child in entity.get("children") or []
    )


def inventory(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.env().get(KEY_ENV, "")
    if not key:
        raise WidgetError("Mu3Lab's HomeBox key is missing; reinstall HomeBox from its page in Apps.")
    headers = {"Authorization": f"Bearer {key}"}
    figures = ctx.get("/api/v1/groups/statistics", headers=headers) or {}
    # HomeBox 0.27 keeps places and things as one tree of "entities" (``/api/v1/locations`` is gone).
    tree = ctx.get("/api/v1/entities/tree", params={"withItems": "true"}, headers=headers) or []
    places = sorted(
        ({"id": place.get("id"), "name": place.get("name"), "itemCount": _things(place)} for place in tree),
        key=lambda place: place["itemCount"],
        reverse=True,
    )
    worth = float(figures.get("totalItemPrice") or 0)
    return {
        "stats": [
            {"label": "Things", "value": f"{int(figures.get('totalItems') or 0):,}"},
            {"label": "Worth", "value": f"{worth:,.0f}"},
            {"label": "Under warranty", "value": str(figures.get("totalWithWarranty", 0))},
            {"label": "Places", "value": str(figures.get("totalLocations", 0))},
        ],
        "items_title": "Places",
        "items": [
            {
                "title": str(place.get("name") or "A place"),
                "meta": f"{int(place.get('itemCount') or 0)} things",
                "url": ctx.web(f"/location/{place['id']}") if place.get("id") else "",
            }
            for place in places[:8]
        ],
        "empty": "No places yet. Add where things live in HomeBox.",
    }
