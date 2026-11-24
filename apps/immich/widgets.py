"""Home widgets for Immich (manifest ``widgets``; ctl/app_widgets.py runs them).

* Photos: the viewer's own library: how many photos and videos, "on this day" memories and their
  newest albums, with their own Immich key (``aioimmich``, the client Home Assistant uses).
* Server (administrators): photos, videos and space used across the household, and the disk.
"""

from __future__ import annotations

from typing import Any

import aiohttp
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
            me = await immich.users.async_get_my_user()
            albums = await immich.albums.async_get_all_albums()
        except ImmichError:
            raise WidgetError("Immich did not give your library.") from None
    counts = ctx.get("/api/assets/statistics", headers=headers) or {}
    memories = ctx.get("/api/memories", params={"for": "today"}, headers=headers) or []
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
