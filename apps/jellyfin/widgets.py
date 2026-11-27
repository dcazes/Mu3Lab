"""Home widgets for Jellyfin (manifest ``widgets``; ctl/app_widgets.py runs them).

* Library: what the household's libraries hold, what was added lately, and how many are watching now
  (a count only: who watches what stays private). Read as Mu3Lab's own Jellyfin account.
* Continue watching: the viewer's own unfinished films and episodes, read for their own Jellyfin
  account (shown only to them).

Jellyfin's current API (10.9 and later): ``/Items/Latest`` and ``/UserItems/Resume`` with ``userId``.
"""

from __future__ import annotations

from typing import Any

from ctl import person_connectors
from ctl.app_widgets import NeedsSetup, WidgetContext, WidgetError, ago

SERVICE = "mu3lab-service"
PASSWORD_ENV = "JELLYFIN_ADMIN_PASSWORD"
CLIENT = 'MediaBrowser Client="Mu3Lab", Device="Mu3Lab widgets", DeviceId="mu3lab-widgets", Version="1"'
TICKS = 10_000_000  # Jellyfin counts time in 100-nanosecond ticks
# Mu3Lab's own sign-in, kept between refreshes: (token, user id).
_signed_in: dict[str, tuple[str, str]] = {}


def _headers(token: str = "") -> dict[str, str]:
    return {"Authorization": CLIENT + (f', Token="{token}"' if token else "")}


def _service(ctx: WidgetContext, *, fresh: bool = False) -> tuple[str, str]:
    if fresh or "service" not in _signed_in:
        password = ctx.env().get(PASSWORD_ENV, "")
        if not password:
            raise WidgetError("Mu3Lab's Jellyfin sign-in is missing; reinstall Jellyfin from its page in Apps.")
        reply = ctx.request(
            "POST", "/Users/AuthenticateByName", json={"Username": SERVICE, "Pw": password}, headers=_headers()
        )
        _signed_in["service"] = (str(reply["AccessToken"]), str(reply["User"]["Id"]))
    return _signed_in["service"]


def _as_service(ctx: WidgetContext, path: str, **params: Any) -> Any:
    token, _user = _service(ctx)
    try:
        return ctx.get(path, params=params, headers=_headers(token))
    except WidgetError:
        # A restarted Jellyfin forgets sign-ins: sign in again once.
        token, _user = _service(ctx, fresh=True)
        return ctx.get(path, params=params, headers=_headers(token))


def _describe(item: dict[str, Any]) -> tuple[str, str]:
    """A title and a line under it, for a film, an episode or an album."""
    kind = item.get("Type")
    if kind == "Episode":
        season, episode = item.get("ParentIndexNumber"), item.get("IndexNumber")
        number = f"S{season:02d}E{episode:02d} · " if isinstance(season, int) and isinstance(episode, int) else ""
        return str(item.get("SeriesName") or item.get("Name")), f"{number}{item.get('Name') or ''}"
    if kind == "MusicAlbum":
        return str(item.get("Name")), str(item.get("AlbumArtist") or "Album")
    if kind == "Series":
        return str(item.get("Name")), "New episodes"
    year = item.get("ProductionYear")
    return str(item.get("Name")), f"{'Film' if kind == 'Movie' else kind or ''}{f' · {year}' if year else ''}"


def library(ctx: WidgetContext) -> dict[str, Any]:
    counts = _as_service(ctx, "/Items/Counts") or {}
    sessions = _as_service(ctx, "/Sessions", activeWithinSeconds=960) or []
    _token, user = _service(ctx)
    latest = _as_service(ctx, "/Items/Latest", userId=user, limit=8, fields="DateCreated") or []
    watching = sum(1 for session in sessions if session.get("NowPlayingItem"))
    stats = [
        {"label": "Films", "value": str(counts.get("MovieCount", 0))},
        {"label": "Shows", "value": str(counts.get("SeriesCount", 0))},
        {"label": "Episodes", "value": str(counts.get("EpisodeCount", 0))},
    ]
    if counts.get("SongCount"):
        stats.append({"label": "Songs", "value": str(counts["SongCount"])})
    stats.append({"label": "Watching now", "value": str(watching), "tone": "good" if watching else ""})
    items = []
    for item in latest:
        title, subtitle = _describe(item)
        items.append(
            {"title": title, "subtitle": subtitle, "meta": ago(item["DateCreated"]) if item.get("DateCreated") else ""}
        )
    return {"stats": stats, "items_title": "Recently added", "items": items, "empty": "Nothing added yet."}


def _viewer(ctx: WidgetContext) -> str:
    """The viewer's Jellyfin user id: the account Mu3Lab made for them, named after their username."""
    person = next((found for found in person_connectors.household() if found["uid"] == ctx.uid), None)
    if person:
        for user in _as_service(ctx, "/Users") or []:
            if str(user.get("Name") or "").casefold() == person["username"].casefold():
                return str(user["Id"])
    raise NeedsSetup("Mu3Lab is still making your Jellyfin account; your shows appear here within a few minutes.")


def _resume(ctx: WidgetContext) -> Any:
    """Read with Mu3Lab's own (administrator) sign-in for the viewer's account, so it needs no password
    of theirs, which they may change in Jellyfin; their library access and limits still apply."""
    return _as_service(ctx, "/UserItems/Resume", userId=_viewer(ctx), limit=10, mediaTypes="Video")


def continue_watching(ctx: WidgetContext) -> dict[str, Any]:
    resume = _resume(ctx)
    items = []
    for item in (resume or {}).get("Items") or []:
        title, subtitle = _describe(item)
        played = (item.get("UserData") or {}).get("PlayedPercentage")
        left = int(item.get("RunTimeTicks") or 0) - int((item.get("UserData") or {}).get("PlaybackPositionTicks") or 0)
        items.append(
            {
                "title": title,
                "subtitle": subtitle,
                "progress": played / 100 if isinstance(played, int | float) else None,
                "meta": f"{max(1, left // TICKS // 60)} min left" if left > 0 else "",
            }
        )
    return {"items": items, "empty": "Nothing half-watched. Start something in Jellyfin and it waits for you here."}
