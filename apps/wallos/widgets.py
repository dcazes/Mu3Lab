"""Home widgets for Wallos (manifest ``widgets``; ctl/app_widgets.py runs them).

Subscriptions: the viewer's own subscriptions: this month's cost and the next renewals, through
Wallos's API with the key they paste into the widget's settings (Wallos keeps each person's
subscriptions private, and only shows a person their own key).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

SOON = 7


def _call(ctx: WidgetContext, endpoint: str, **params: Any) -> dict[str, Any]:
    reply = ctx.get(f"/api/{endpoint}", params={"api_key": ctx.key(), **params}) or {}
    if not reply.get("success", False):
        raise WidgetError(f"Wallos refused the key ({reply.get('title') or reply.get('message') or 'check it'}).")
    return reply


def _when(stamp: str) -> date | None:
    try:
        return datetime.strptime(stamp[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def subscriptions(ctx: WidgetContext) -> dict[str, Any]:
    today = date.today()
    found = _call(ctx, "subscriptions/get_subscriptions.php", state=0, sort="next_payment").get("subscriptions") or []
    monthly = _call(ctx, "subscriptions/get_monthly_cost.php", month=today.month, year=today.year)
    items = []
    soon = 0
    for item in found:
        if item.get("inactive"):
            continue
        due = _when(str(item.get("next_payment") or ""))
        days = (due - today).days if due else None
        if days is not None and 0 <= days <= SOON:
            soon += 1
        items.append(
            {
                "title": str(item.get("name") or "A subscription"),
                "subtitle": f"{item.get('price', '')} {item.get('currency_code') or ''}".strip(),
                "meta": "Today" if days == 0 else "Tomorrow" if days == 1 else f"{due:%d %b}" if due else "",
                "tone": "warn" if days is not None and 0 <= days <= SOON else "",
            }
        )
    cost = monthly.get("localized_monthly_cost") or monthly.get("monthly_cost") or ""
    return {
        "stats": [
            {"label": "This month", "value": str(cost)},
            {"label": "Renewing this week", "value": str(soon), "tone": "warn" if soon else ""},
            {"label": "Subscriptions", "value": str(len(items))},
        ],
        "items_title": "Next renewals",
        "items": items[:10],
        "empty": "No subscriptions yet. Add one in Wallos.",
    }
