"""Home widgets for Immich (manifest ``widgets``; ctl/app_widgets.py runs them).

* Photos: the viewer's own library: how many photos and videos, "on this day" memories and their
  newest albums, with their own Immich key (``aioimmich``, the client Home Assistant uses).
* Server (administrators): photos, videos and space used across the household, and the disk.
* Slideshow: the viewer's own photos one after another (an album, favourites, this day in past years
  or a random pick), each photo read through Mu3Lab with their own key.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import aiohttp
import httpx
from aioimmich import Immich
from aioimmich.exceptions import ImmichError

from ctl.app_widgets import WidgetContext, WidgetError, ago, run

CONNECTOR = "immich-photo-manager"
KEY_ENV = "IMMICH_API_KEY"
GB = 1024**3


def _size(value: float) -> str:
    return f"{value / GB:,.1f} GB" if value < 1024 * GB else f"{value / GB / 1024:,.2f} TB"


async def _photos(ctx: WidgetContext, key: str) -> dict[str, Any]:
    headers = {"x-api-key": key}
    async with aiohttp.ClientSession() as session:
        immich = Immich(session, key, "127.0.0.1", ctx.app.manifest.service.local_port, use_ssl=False)
        try:
            await immich.async_setup()
            me = await immich.users.async_get_my_user()
            albums = await immich.albums.async_get_all_albums()
        except ImmichError:
            raise WidgetError("Immich did not give your library.") from None
    counts = ctx.get("/api/assets/statistics", headers=headers) or {}
    # Immich wants a plain date ("for" = YYYY-MM-DD) since v2.
    memories = ctx.get("/api/memories", params={"for": date.today().isoformat()}, headers=headers) or []
    items = []
    for memory in memories[:3]:
        year = (memory.get("data") or {}).get("year")
        count = len(memory.get("assets") or [])
        if year and count:
            items.append(
                {
                    "title": f"On this day in {year}",
                    "subtitle": f"{count} photo{'' if count == 1 else 's'}",
                    "url": ctx.web("/memory"),
                    "tone": "good",
                }
            )
    for album in sorted(albums, key=lambda found: str(found.updated_at or ""), reverse=True)[:6]:
        items.append(
            {
                "title": album.album_name or "An album",
                "subtitle": f"{album.asset_count} item{'' if album.asset_count == 1 else 's'}",
                "meta": ago(album.updated_at) if album.updated_at else "",
                "url": ctx.web(f"/albums/{album.album_id}"),
            }
        )
    used = me.quota_usage_in_bytes
    return {
        "stats": [
            {"label": "Photos", "value": f"{counts.get('images', 0):,}"},
            {"label": "Videos", "value": f"{counts.get('videos', 0):,}"},
            {"label": "Albums", "value": str(len(albums))},
            *([{"label": "Space used", "value": _size(used)}] if used else []),
        ],
        "items_title": "Memories and albums",
        "items": items,
        "empty": "No albums yet. Make one in Immich to see it here.",
    }


def photos(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.own_credentials(CONNECTOR).get(KEY_ENV, "")
    return run(_photos(ctx, key))


async def _server(ctx: WidgetContext, key: str) -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        immich = Immich(session, key, "127.0.0.1", ctx.app.manifest.service.local_port, use_ssl=False)
        try:
            await immich.async_setup()
            totals = await immich.server.async_get_server_statistics()
            disk = await immich.server.async_get_storage_info()
        except ImmichError:
            raise WidgetError(
                "Immich did not give its server figures; its key may not be an administrator's."
            ) from None
    return {
        "stats": [
            {"label": "Photos", "value": f"{totals.photos:,}"},
            {"label": "Videos", "value": f"{totals.videos:,}"},
            {"label": "Used", "value": _size(totals.usage)},
            {
                "label": "Disk free",
                "value": disk.disk_available,
                "tone": "bad" if disk.disk_usage_percentage > 90 else "warn" if disk.disk_usage_percentage > 80 else "",
            },
        ],
        "items_title": "Disk",
        "items": [
            {
                "title": f"{disk.disk_use} of {disk.disk_size} used",
                "progress": disk.disk_usage_percentage / 100,
                "meta": f"{disk.disk_usage_percentage:.0f}%",
            }
        ],
    }


def server(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.credentials(CONNECTOR).get(KEY_ENV, "")
    if not key:
        raise WidgetError("Mu3Lab's Immich key is missing; reinstall Immich from its page in Apps.")
    return run(_server(ctx, key))


# ---------- Slideshow: the viewer's own photos, one after another ----------

SOURCES = ("album", "favorites", "memories", "random")
ASSET_ID = re.compile(r"^[0-9a-f-]{36}$")
MAX_SLIDES = 60


def slideshow_settings(given: dict[str, Any]) -> dict[str, Any]:
    source = given.get("source")
    seconds = given.get("seconds")
    album = str(given.get("album_id") or "")
    return {
        "source": source if source in SOURCES else "memories",
        "album_id": album if ASSET_ID.match(album) else "",
        "seconds": seconds if isinstance(seconds, int) and 5 <= seconds <= 300 else 12,
        "fit": "contain" if given.get("fit") == "contain" else "cover",
    }


def _slide(asset: dict[str, Any]) -> dict[str, Any] | None:
    if asset.get("type") != "IMAGE" or not ASSET_ID.match(str(asset.get("id") or "")):
        return None
    exif = asset.get("exifInfo") or {}
    place = ", ".join(str(part) for part in (exif.get("city"), exif.get("country")) if part)
    return {
        "id": str(asset["id"]),
        "taken": str(asset.get("localDateTime") or asset.get("fileCreatedAt") or ""),
        "place": place,
    }


def slideshow(ctx: WidgetContext) -> dict[str, Any]:
    """Photos from the source the viewer chose, read with their own Immich key (only theirs, and what
    is shared with them). The photos themselves come through ``slideshow_file``."""
    headers = {"x-api-key": ctx.own_credentials(CONNECTOR).get(KEY_ENV, "")}
    source = ctx.settings.get("source") or "memories"
    albums = ctx.get("/api/albums", headers=headers) or []
    found: list[dict[str, Any]] = []
    if source == "album" and ctx.settings.get("album_id"):
        album = ctx.get(f"/api/albums/{ctx.settings['album_id']}", headers=headers) or {}
        found = list(album.get("assets") or [])
    elif source == "favorites":
        reply = ctx.request(
            "POST", "/api/search/metadata", json={"isFavorite": True, "type": "IMAGE", "size": 200}, headers=headers
        )
        found = list(((reply or {}).get("assets") or {}).get("items") or [])
    elif source == "random":
        found = list(
            ctx.request("POST", "/api/search/random", json={"size": MAX_SLIDES, "type": "IMAGE"}, headers=headers) or []
        )
    elif source == "memories":
        for memory in ctx.get("/api/memories", params={"for": date.today().isoformat()}, headers=headers) or []:
            found.extend(memory.get("assets") or [])
    slides = [slide for slide in map(_slide, found) if slide][:MAX_SLIDES]
    empty = {
        "album": "Choose an album in this widget's settings."
        if not ctx.settings.get("album_id")
        else "That album has no photos.",
        "favorites": "Star photos in Immich to show them here.",
        "memories": "No memories from this day in past years.",
        "random": "Your Immich library has no photos yet.",
    }[source if source in SOURCES else "memories"]
    return {
        "view": {
            "slides": slides,
            "albums": [
                {"id": str(item["id"]), "name": str(item.get("albumName") or "An album")}
                for item in albums
                if ASSET_ID.match(str(item.get("id") or ""))
            ][:100],
        },
        "empty": empty,
    }


def slideshow_file(ctx: WidgetContext, name: str) -> tuple[bytes, str]:
    """One photo, at Immich's preview size, read with the viewer's own key (Immich decides if they may)."""
    if not ASSET_ID.match(name):
        raise WidgetError("That is not a photo.")
    headers = {"x-api-key": ctx.own_credentials(CONNECTOR).get(KEY_ENV, "")}
    try:
        reply = httpx.get(
            f"{ctx.base}/api/assets/{name}/thumbnail", params={"size": "preview"}, headers=headers, timeout=20
        )
    except httpx.HTTPError:
        raise WidgetError("Immich did not answer.") from None
    if not reply.is_success:
        raise WidgetError(f"Immich did not give that photo (HTTP {reply.status_code}).")
    return reply.content, reply.headers.get("content-type", "image/jpeg").split(";")[0]
