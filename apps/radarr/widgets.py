"""Home widgets for Radarr (manifest ``widgets``; ctl/app_widgets.py runs them).

Upcoming films: the household's films released in the next month (in cinemas, digitally or on disc),
and whether each is downloaded, with Radarr's queue. Read with Mu3Lab's Radarr client
(``ctl.servarr``, the same one its setup uses) and Radarr's API key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError
from ctl.engine.loopback import LoopbackError
from ctl.servarr import Servarr

KEY_ENV = "RADARR_API_KEY"
DAYS = 30
RELEASES = (("digitalRelease", "Digital"), ("physicalRelease", "On disc"), ("inCinemas", "In cinemas"))


def _release(movie: dict[str, Any], start: datetime, end: datetime) -> tuple[str, str]:
    """The first release in the window: when, and what kind."""
    found = []
    for field, label in RELEASES:
        stamp = str(movie.get(field) or "")
        if stamp:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if start <= when <= end:
                found.append((when, label))
    if not found:
        return "", ""
    when, label = min(found)
    return f"{when.astimezone():%a %d %b}", label


def upcoming(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.env().get(KEY_ENV, "")
    if not key:
        raise WidgetError("Radarr's API key is missing; reinstall Radarr from its page in Apps.")
    api = Servarr(ctx.name, ctx.app.manifest.service.local_port, key)
    start = datetime.now(UTC)
    end = start + timedelta(days=DAYS)
    try:
        movies = api.get(f"calendar?start={start:%Y-%m-%dT%H:%M:%SZ}&end={end:%Y-%m-%dT%H:%M:%SZ}") or []
        queue = api.get("queue?pageSize=1") or {}
    except LoopbackError as exc:
        raise WidgetError(str(exc)) from None
    items = []
    for movie in movies:
        when, label = _release(movie, start, end)
        year = movie.get("year")
        items.append(
            {
                "title": str(movie.get("title") or "A film"),
                "subtitle": " · ".join(part for part in (label, str(year) if year else "") if part),
                "meta": "Downloaded" if movie.get("hasFile") else when,
                "tone": "good" if movie.get("hasFile") else "",
            }
        )
    return {
        "stats": [
            {"label": "Coming this month", "value": str(len(movies))},
            {"label": "Downloaded", "value": str(sum(1 for movie in movies if movie.get("hasFile")))},
            {"label": "Downloading", "value": str(queue.get("totalRecords", 0))},
        ],
        "items_title": "Coming out",
        "items": items,
        "empty": "No films the household follows come out this month.",
    }
