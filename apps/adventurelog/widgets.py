"""Home widgets for AdventureLog (manifest ``widgets``; ctl/app_widgets.py runs them).

Trips: the viewer's own trips: the next one and how many days until it, and the ones after, through
AdventureLog's API with their own key (AdventureLog reads it as "Api-Key", not "Bearer").
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ctl.app_widgets import WidgetContext

CONNECTOR = "adventurelog"
KEY_ENV = "ADVENTURELOG_API_KEY"


def _day(stamp: Any) -> date | None:
    try:
        return date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return None


def trips(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.own_credentials(CONNECTOR).get(KEY_ENV, "")
    reply = ctx.get("/api/collections/", headers={"Authorization": f"Api-Key {key}"})
    found = reply.get("results", []) if isinstance(reply, dict) else reply or []
    today = date.today()
    ahead = []
    for trip in found:
        start, end = _day(trip.get("start_date")), _day(trip.get("end_date"))
        if trip.get("is_archived") or start is None or (end or start) < today:
            continue
        ahead.append((start, end, trip))
    ahead.sort(key=lambda entry: entry[0])
    items = []
    for start, end, trip in ahead[:8]:
        days = (start - today).days
        items.append(
            {
                "title": str(trip.get("name") or "A trip"),
                "subtitle": f"{start:%d %b}" + (f" – {end:%d %b}" if end and end != start else ""),
                "meta": "Now" if days <= 0 else "Tomorrow" if days == 1 else f"In {days} days",
                "tone": "good" if days <= 0 else "",
                "url": ctx.web(f"/collections/{trip['id']}") if trip.get("id") else "",
            }
        )
    first = ahead[0][0] if ahead else None
    return {
        "stats": [
            {
                "label": "Next trip",
                "value": "Now"
                if first and first <= today
                else f"{(first - today).days} days"
                if first
                else "None planned",
            },
            {"label": "Planned", "value": str(len(ahead))},
        ],
        "items_title": "Coming up",
        "items": items,
        "empty": "No trips planned. Plan one in AdventureLog.",
    }
