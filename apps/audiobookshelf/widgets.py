"""Home widgets for Audiobookshelf (manifest ``widgets``; ctl/app_widgets.py runs them).

Audiobooks: what each household library holds and how many hours of listening that is, read with the
API key Mu3Lab made for the owner's account (the chat connector's).
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

CONNECTOR = "audiobookshelf-adapter"
KEY_ENV = "ABS_API_KEY"


def libraries(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.credentials(CONNECTOR).get(KEY_ENV, "")
    if not key:
        raise WidgetError("Mu3Lab's Audiobookshelf key is missing; reinstall Audiobookshelf from its page in Apps.")
    headers = {"Authorization": f"Bearer {key}"}
    found = (ctx.get("/api/libraries", headers=headers) or {}).get("libraries") or []
    books = podcasts = 0
    seconds = 0.0
    items = []
    for library in found:
        figures = ctx.get(f"/api/libraries/{library['id']}/stats", headers=headers) or {}
        count = int(figures.get("totalItems") or 0)
        hours = float(figures.get("totalDuration") or 0) / 3600
        seconds += float(figures.get("totalDuration") or 0)
        if library.get("mediaType") == "podcast":
            podcasts += count
            noun = "podcast" if count == 1 else "podcasts"
        else:
            books += count
            noun = "book" if count == 1 else "books"
        items.append(
            {
                "title": str(library.get("name") or "A library"),
                "subtitle": f"{count} {noun} · {figures.get('totalAuthors', 0)} authors",
                "meta": f"{hours:,.0f} h",
            }
        )
    return {
        "stats": [
            {"label": "Books", "value": str(books)},
            {"label": "Podcasts", "value": str(podcasts)},
            {"label": "Hours", "value": f"{seconds / 3600:,.0f}"},
        ],
        "items_title": "Libraries",
        "items": items,
        "empty": "No libraries yet.",
    }
