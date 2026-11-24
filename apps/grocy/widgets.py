"""Home widgets for Grocy (manifest ``widgets``; ctl/app_widgets.py runs them).

* Pantry: what has expired, is past its date or due soon, what has run out, and the shopping list.
* Chores: what is due today or overdue, ticked off right in the widget (Grocy records it as done).

Read with the API key Mu3Lab made in Grocy (the chat connector's); Grocy is the household's.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

CONNECTOR = "grocy-community"
KEY_ENV = "GROCY_API_KEY"
DUE_SOON_DAYS = 5


def _headers(ctx: WidgetContext) -> dict[str, str]:
    key = ctx.credentials(CONNECTOR).get(KEY_ENV, "")
    if not key:
        raise WidgetError("Mu3Lab's Grocy key is missing; reinstall Grocy from its page in Apps.")
    return {"GROCY-API-KEY": key}


def _when(stamp: str) -> str:
    try:
        day = datetime.strptime(stamp[:10], "%Y-%m-%d").date()
    except ValueError:
        return ""
    delta = (day - date.today()).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta < 0:
        return f"{-delta} day{'' if delta == -1 else 's'} ago"
    return f"{day:%a %d %b}"


def pantry(ctx: WidgetContext) -> dict[str, Any]:
    headers = _headers(ctx)
    volatile = ctx.get("/api/stock/volatile", params={"due_soon_days": DUE_SOON_DAYS}, headers=headers) or {}
    shopping = ctx.get("/api/objects/shopping_list", headers=headers) or []
    expired = volatile.get("expired_products") or []
    overdue = volatile.get("overdue_products") or []
    due = volatile.get("due_products") or []
    missing = volatile.get("missing_products") or []
    items = []
    for group, label, tone in ((expired, "Expired", "bad"), (overdue, "Past its date", "bad"), (due, "Due", "warn")):
        for entry in group:
            product = entry.get("product") or {}
            items.append(
                {
                    "title": str(product.get("name") or entry.get("name") or "A product"),
                    "subtitle": label,
                    "meta": _when(str(entry.get("best_before_date") or "")),
                    "tone": tone,
                }
            )
    for entry in missing:
        items.append(
            {"title": str(entry.get("name") or "A product"), "subtitle": "Run out", "meta": "", "tone": "warn"}
        )
    to_buy = sum(1 for entry in shopping if not entry.get("done"))
    return {
        "stats": [
            {
                "label": "Expired",
                "value": str(len(expired) + len(overdue)),
                "tone": "bad" if expired or overdue else "good",
            },
            {"label": "Due soon", "value": str(len(due)), "tone": "warn" if due else ""},
            {"label": "Run out", "value": str(len(missing)), "tone": "warn" if missing else ""},
            {"label": "To buy", "value": str(to_buy)},
        ],
        "items_title": "Needs attention",
        "items": items,
        "empty": f"Nothing expires in the next {DUE_SOON_DAYS} days.",
    }


def chores(ctx: WidgetContext) -> dict[str, Any]:
    headers = _headers(ctx)
    found = ctx.get("/api/chores", headers=headers) or []
    today = date.today().isoformat()
    due = [
        chore
        for chore in found
        if chore.get("next_estimated_execution_time") and str(chore["next_estimated_execution_time"])[:10] <= today
    ]
    due.sort(key=lambda chore: str(chore.get("next_estimated_execution_time")))
    overdue = sum(1 for chore in due if str(chore["next_estimated_execution_time"])[:10] < today)
    return {
        "stats": [
            {"label": "Due today", "value": str(len(due) - overdue)},
            {"label": "Overdue", "value": str(overdue), "tone": "bad" if overdue else "good"},
        ],
        "items": [
            {
                "title": str(chore.get("chore_name") or "A chore"),
                "subtitle": (chore.get("next_execution_assigned_user") or {}).get("display_name") or "",
                "meta": _when(str(chore["next_estimated_execution_time"])),
                "tone": "bad" if str(chore["next_estimated_execution_time"])[:10] < today else "",
                "check": False,
                "action": str(chore.get("chore_id")),
            }
            for chore in due
        ],
        "empty": "No chores due today.",
    }


def chores_action(ctx: WidgetContext, action: str) -> None:
    if not action.isdigit():
        raise WidgetError("That chore is not in Grocy.")
    ctx.request("POST", f"/api/chores/{action}/execute", json={}, headers=_headers(ctx))
