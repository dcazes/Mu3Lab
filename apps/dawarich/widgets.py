"""Home widgets for Dawarich (manifest ``widgets``; ctl/app_widgets.py runs them).

Places: the viewer's own places today, with their all-time distance, countries and cities, through
Dawarich's API with their own API key (the one their phone uses, kept for their chat connector).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from ctl.app_widgets import WidgetContext

CONNECTOR = "dawarich-builtin"
KEY_ENV = "MCP_AUTH_TOKEN"


def _clock(stamp: str) -> str:
    try:
        return f"{datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone():%H:%M}"
    except ValueError:
        return ""


def places(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.own_credentials(CONNECTOR).get(KEY_ENV, "")
    headers = {"Authorization": f"Bearer {key}"}
    figures = ctx.get("/api/v1/stats", params={"api_key": key}) or {}
    midnight = datetime.combine(datetime.now().date(), time.min).astimezone()
    visits = (
        ctx.get(
            "/api/v1/visits",
            params={
                "start_at": midnight.astimezone(UTC).isoformat(),
                "end_at": (midnight + timedelta(days=1)).astimezone(UTC).isoformat(),
            },
            headers=headers,
        )
        or []
    )
    items = [
        {
            "title": str(visit.get("name") or visit.get("display_name") or "A place"),
            "subtitle": f"{_clock(str(visit.get('started_at') or ''))}–{_clock(str(visit.get('ended_at') or ''))}",
            "meta": f"{int(visit.get('duration') or 0)} min",
            "tone": "warn" if visit.get("status") == "suggested" else "",
        }
        for visit in sorted(visits, key=lambda visit: str(visit.get("started_at") or ""))
        if visit.get("status") != "declined"
    ]
    return {
        "stats": [
            {"label": "Places today", "value": str(len(items))},
            {"label": "Distance", "value": f"{float(figures.get('totalDistanceKm') or 0):,.0f} km"},
            {"label": "Countries", "value": str(figures.get("totalCountriesVisited", 0))},
            {"label": "Cities", "value": str(figures.get("totalCitiesVisited", 0))},
        ],
        "items_title": "Today",
        "items": items,
        "empty": "No visits yet today.",
    }
