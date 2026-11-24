"""Home widgets for Paperless-ngx (manifest ``widgets``; ctl/app_widgets.py runs them).

Documents: the viewer's own documents: how many, how many still wait in the inbox, and the newest,
with their own Paperless key (``pypaperless``, the client Home Assistant uses).
"""

from __future__ import annotations

from typing import Any

from pypaperless import Paperless
from pypaperless.exceptions import PaperlessError

from ctl.app_widgets import WidgetContext, WidgetError, ago, run

CONNECTOR = "paperless-community"
KEY_ENV = "PAPERLESS_API_KEY"
NEWEST = 6


async def _documents(ctx: WidgetContext, key: str) -> dict[str, Any]:
    paperless = Paperless(f"http://127.0.0.1:{ctx.app.manifest.service.local_port}", key)
    try:
        await paperless.initialize()
        figures = await paperless.statistics()
        newest = []
        async with paperless.documents.reduce(ordering="-added", page_size=NEWEST) as found:
            async for document in found:
                newest.append(document)
                if len(newest) >= NEWEST:
                    break
    except PaperlessError:
        raise WidgetError("Paperless did not give your documents.") from None
    finally:
        await paperless.close()
    inbox = figures.documents_inbox or 0
    return {
        "stats": [
            {"label": "Documents", "value": f"{figures.documents_total or 0:,}"},
            {"label": "In the inbox", "value": str(inbox), "tone": "warn" if inbox else "good"},
        ],
        "items_title": "Newest",
        "items": [
            {
                "title": document.title or "A document",
                "subtitle": f"Dated {document.created:%d %b %Y}" if document.created else "",
                "meta": ago(document.added) if document.added else "",
                "url": ctx.web(f"/documents/{document.id}/details"),
            }
            for document in newest
        ],
        "empty": "No documents yet. Drop some into Paperless.",
    }


def documents(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.own_credentials(CONNECTOR).get(KEY_ENV, "")
    return run(_documents(ctx, key))
