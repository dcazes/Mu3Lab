"""Home widgets for Outline (manifest ``widgets``; ctl/app_widgets.py runs them).

Wiki: the household wiki's most recently updated pages and who changed them, through Outline's API
with the viewer's own key (so it lists only the pages they can open).

Notes: one document the viewer chooses, shown and edited right on Home, saved to Outline as them
(their key), so Outline's own access, history and search all still apply.
"""

from __future__ import annotations

import re
from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError, ago

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


# ---------- Notes: one Outline document, read and written as the viewer ----------

DOCUMENT_ID = re.compile(r"^[A-Za-z0-9-]{1,100}$")
NOTES_TITLE = "Home notes"
MAX_NOTE = 100_000


def _call(ctx: WidgetContext, method: str, body: dict[str, Any]) -> Any:
    key = ctx.own_credentials(CONNECTOR).get(KEY_ENV, "")
    reply = ctx.request("POST", f"/api/{method}", json=body, headers={"Authorization": f"Bearer {key}"})
    return (reply or {}).get("data")


def notes_settings(given: dict[str, Any]) -> dict[str, Any]:
    document = str(given.get("document_id") or "")
    return {"document_id": document} if DOCUMENT_ID.match(document) else {}


def _recent(ctx: WidgetContext) -> list[dict[str, str]]:
    pages = _call(ctx, "documents.list", {"sort": "updatedAt", "direction": "DESC", "limit": 25}) or []
    return [{"id": str(page["id"]), "title": str(page.get("title") or "Untitled")} for page in pages if page.get("id")]


def notes(ctx: WidgetContext) -> dict[str, Any]:
    """The chosen document's text (Markdown), and the viewer's recent documents to choose from."""
    chosen = str(ctx.settings.get("document_id") or "")
    document = None
    if chosen:
        try:
            page = _call(ctx, "documents.info", {"id": chosen}) or {}
        except WidgetError:
            page = {}
        if page.get("id"):
            document = {
                "id": str(page["id"]),
                "title": str(page.get("title") or "Untitled"),
                "text": str(page.get("text") or "")[:MAX_NOTE],
                "url": ctx.web(str(page["url"])) if str(page.get("url") or "").startswith("/") else "",
                "updated_at": str(page.get("updatedAt") or ""),
            }
    return {
        "view": {"document": document, "documents": _recent(ctx), "missing": bool(chosen and document is None)},
        "empty": "Choose a document in this widget's settings, or make a new one.",
    }


def notes_action(ctx: WidgetContext, action: str) -> dict[str, Any] | None:
    """``save``: the note's new text, unless someone changed it in Outline since it was loaded.
    ``create``: a new "Home notes" document in the first collection the viewer can write to."""
    if action == "create":
        collections = _call(ctx, "collections.list", {"limit": 25}) or []
        writable = [item for item in collections if item.get("id") and item.get("permission") != "read"]
        if not writable:
            raise WidgetError("You have no Outline collection to write in yet. Make one in Outline first.")
        page = _call(
            ctx,
            "documents.create",
            {"title": NOTES_TITLE, "text": "", "collectionId": writable[0]["id"], "publish": True},
        )
        if not (page or {}).get("id"):
            raise WidgetError("Outline did not make the document.")
        return {"document_id": str(page["id"])}
    if action != "save":
        raise WidgetError("Outline was asked for something it does not do here.")
    document = str(ctx.payload.get("id") or "")
    if not DOCUMENT_ID.match(document) or document != ctx.settings.get("document_id"):
        raise WidgetError("This widget's document changed. Reload the page.")
    text = str(ctx.payload.get("text") or "")
    if len(text) > MAX_NOTE:
        raise WidgetError("That note is too long for the Home widget; edit it in Outline.")
    base = str(ctx.payload.get("updated_at") or "")
    current = _call(ctx, "documents.info", {"id": document}) or {}
    if base and str(current.get("updatedAt") or "") != base:
        raise WidgetError("This note changed in Outline since it was opened here. Reload to see it, then type again.")
    page = _call(ctx, "documents.update", {"id": document, "text": text}) or {}
    return {"updated_at": str(page.get("updatedAt") or "")}
