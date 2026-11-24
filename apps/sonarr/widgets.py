"""Home widgets for Sonarr (manifest ``widgets``; ctl/app_widgets.py runs them).

Upcoming episodes: what airs in the next two weeks for the shows the household follows, and whether
each is downloaded, with Sonarr's queue and missing episodes. Read with Mu3Lab's Sonarr client
(``ctl.servarr``, the same one its setup uses) and Sonarr's API key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError
from ctl.engine.loopback import LoopbackError
from ctl.servarr import Servarr

KEY_ENV = "SONARR_API_KEY"
DAYS = 14


def _api(ctx: WidgetContext) -> Servarr:
    key = ctx.env().get(KEY_ENV, "")
    if not key:
        raise WidgetError("Sonarr's API key is missing; reinstall Sonarr from its page in Apps.")
    return Servarr(ctx.name, ctx.app.manifest.service.local_port, key)


def _day(stamp: str) -> str:
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
    today = datetime.now().astimezone().date()
    if when.date() == today:
        return f"Today {when:%H:%M}"
    if when.date() == today + timedelta(days=1):
        return f"Tomorrow {when:%H:%M}"
    return f"{when:%a %d %b}"


def upcoming(ctx: WidgetContext) -> dict[str, Any]:
    api = _api(ctx)
    start = datetime.now(UTC)
    try:
        episodes = api.get(
            f"calendar?start={start:%Y-%m-%dT%H:%M:%SZ}&end={start + timedelta(days=DAYS):%Y-%m-%dT%H:%M:%SZ}"
            "&includeSeries=true"
        )
        queue = api.get("queue?pageSize=1") or {}
        missing = api.get("wanted/missing?pageSize=1&monitored=true") or {}
    except LoopbackError as exc:
        raise WidgetError(str(exc)) from None
    week = start + timedelta(days=7)
    items = []
    for episode in episodes or []:
        series = (episode.get("series") or {}).get("title") or "A show"
        season, number = episode.get("seasonNumber"), episode.get("episodeNumber")
        code = f"S{season:02d}E{number:02d}" if isinstance(season, int) and isinstance(number, int) else ""
        items.append(
            {
                "title": series,
                "subtitle": " · ".join(part for part in (code, str(episode.get("title") or "")) if part),
                "meta": "Downloaded" if episode.get("hasFile") else _day(str(episode.get("airDateUtc") or "")),
                "tone": "good" if episode.get("hasFile") else "",
            }
        )
    this_week = sum(
        1 for episode in episodes or [] if str(episode.get("airDateUtc") or "") < f"{week:%Y-%m-%dT%H:%M:%SZ}"
    )
    return {
        "stats": [
            {"label": "This week", "value": str(this_week)},
            {"label": "Downloading", "value": str(queue.get("totalRecords", 0))},
            {
                "label": "Missing",
                "value": str(missing.get("totalRecords", 0)),
                "tone": "warn" if missing.get("totalRecords") else "",
            },
        ],
        "items_title": "Airing soon",
        "items": items,
        "empty": "Nothing airs in the next two weeks.",
    }
