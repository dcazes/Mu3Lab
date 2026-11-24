"""Home widgets for Seerr (manifest ``widgets``; ctl/app_widgets.py runs them).

Requests: how many requests wait, are on their way or are ready to watch, and the newest ones with
their titles, through python-overseerr (the client Home Assistant uses), with Seerr's API key.
"""

from __future__ import annotations

from typing import Any

from python_overseerr import OverseerrClient, OverseerrError
from python_overseerr.models import MediaStatus, MediaType, RequestSortStatus, RequestStatus

from ctl.app_widgets import WidgetContext, WidgetError, ago, run

KEY_ENV = "SEERR_API_KEY"
NEWEST = 6


def _where(status: RequestStatus, media: MediaStatus) -> tuple[str, str]:
    if status == RequestStatus.DECLINED:
        return "Declined", "bad"
    if status == RequestStatus.PENDING_APPROVAL:
        return "Waiting for approval", "warn"
    if media == MediaStatus.AVAILABLE:
        return "Ready to watch", "good"
    if media == MediaStatus.PARTIALLY_AVAILABLE:
        return "Partly ready", "good"
    return "On its way", ""


async def _requests(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.env().get(KEY_ENV, "")
    if not key:
        raise WidgetError("Seerr's API key is missing; reinstall Seerr from its page in Apps.")
    client = OverseerrClient("127.0.0.1", ctx.app.manifest.service.local_port, key, ssl=False)
    try:
        count = await client.get_request_count()
        newest = (await client.get_requests(sort=RequestSortStatus.ADDED))[:NEWEST]
        items = []
        for request in newest:
            media = request.media
            if media.media_type == MediaType.MOVIE and media.tmdb_id:
                title = (await client.get_movie_details(media.tmdb_id)).title
            elif media.tmdb_id:
                title = (await client.get_tv_details(media.tmdb_id)).name
            else:
                title = "A request"
            label, tone = _where(request.status, media.status)
            items.append(
                {
                    "title": title,
                    "subtitle": f"{'Film' if media.media_type == MediaType.MOVIE else 'Show'} · requested {ago(request.created_at)}",
                    "meta": label,
                    "tone": tone,
                }
            )
    except OverseerrError:
        raise WidgetError("Seerr did not give its requests.") from None
    finally:
        await client.close()
    return {
        "stats": [
            {"label": "Waiting", "value": str(count.pending), "tone": "warn" if count.pending else ""},
            {"label": "On their way", "value": str(count.processing)},
            {"label": "Ready", "value": str(count.available), "tone": "good" if count.available else ""},
        ],
        "items_title": "Newest requests",
        "items": items,
        "empty": "No requests yet. Ask for a film or show in Seerr.",
    }


def requests(ctx: WidgetContext) -> dict[str, Any]:
    return run(_requests(ctx))
