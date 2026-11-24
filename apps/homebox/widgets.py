"""Home widgets for HomeBox (manifest ``widgets``; ctl/app_widgets.py runs them).

Inventory: how many things the household has recorded, what they are worth, what is under warranty,
and the locations holding the most, with Mu3Lab's HomeBox key.
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

KEY_ENV = "HOMEBOX_MU3LAB_KEY"


def inventory(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.env().get(KEY_ENV, "")
    if not key:
        raise WidgetError("Mu3Lab's HomeBox key is missing; reinstall HomeBox from its page in Apps.")
    headers = {"Authorization": f"Bearer {key}"}
    figures = ctx.get("/api/v1/groups/statistics", headers=headers) or {}
    places = ctx.get("/api/v1/locations", params={"filterChildren": "false"}, headers=headers) or []
    places = sorted(places, key=lambda place: int(place.get("itemCount") or 0), reverse=True)
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
