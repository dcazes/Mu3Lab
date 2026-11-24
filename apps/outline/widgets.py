"""Home widgets for Outline (manifest ``widgets``; ctl/app_widgets.py runs them).

Wiki: the household wiki's most recently updated pages and who changed them, through Outline's API
with the viewer's own key (so it lists only the pages they can open).
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, ago

CONNECTOR = "outline-builtin"
KEY_ENV = "MCP_AUTH_TOKEN"


def wiki(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.own_credentials(CONNECTOR).get(KEY_ENV, "")
    reply = ctx.request(
        "POST",
        "/api/documents.list",
        json={"sort": "updatedAt", "direction": "DESC", "limit": 10},
        headers={"Authorization": f"Bearer {key}"},
    )
    pages = (reply or {}).get("data") or []
    return {
        "items_title": "Recently updated",
        "items": [
            {
                "title": str(page.get("title") or "Untitled"),
                "subtitle": f"By {(page.get('updatedBy') or {}).get('name')}"
                if (page.get("updatedBy") or {}).get("name")
                else "",
                "meta": ago(page["updatedAt"]) if page.get("updatedAt") else "",
                "url": ctx.web(str(page["url"])) if str(page.get("url") or "").startswith("/") else "",
            }
            for page in pages
        ],
        "empty": "No pages yet. Write the first one in Outline.",
    }
