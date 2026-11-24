"""Home widgets for LiteLLM (manifest ``widgets``; ctl/app_widgets.py runs them).

AI usage (administrators): today's and this week's requests, tokens and cost through Mu3Lab's AI
gateway, and the models used most, from LiteLLM's own usage figures (its Usage page's), with its
administrator key.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

MASTER_KEY_ENV = "LITELLM_MASTER_KEY"


def _cost(value: float) -> str:
    return f"${value:,.2f}" if value >= 0.01 or value == 0 else "under $0.01"


def usage(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.env().get(MASTER_KEY_ENV, "")
    if not key:
        raise WidgetError("LiteLLM's administrator key is missing; reinstall LiteLLM from its page in Apps.")
    today = date.today()
    reply = ctx.get(
        "/user/daily/activity",
        params={"start_date": (today - timedelta(days=6)).isoformat(), "end_date": today.isoformat(), "page_size": 50},
        headers={"Authorization": f"Bearer {key}"},
    )
    days = (reply or {}).get("results") or []
    week = (reply or {}).get("metadata") or {}
    now = next((day.get("metrics") or {} for day in days if str(day.get("date")) == today.isoformat()), {})
    models: dict[str, int] = defaultdict(int)
    for day in days:
        for name, entry in ((day.get("breakdown") or {}).get("models") or {}).items():
            models[name] += int(((entry or {}).get("metrics") or {}).get("api_requests") or 0)
    total = sum(models.values())
    return {
        "stats": [
            {"label": "Requests today", "value": f"{int(now.get('api_requests') or 0):,}"},
            {"label": "Cost today", "value": _cost(float(now.get("spend") or 0))},
            {"label": "This week", "value": f"{int(week.get('total_api_requests') or 0):,}"},
            {
                "label": "Failed this week",
                "value": str(int(week.get("total_failed_requests") or 0)),
                "tone": "warn" if week.get("total_failed_requests") else "good",
            },
        ],
        "items_title": "Models used this week",
        "items": [
            {"title": name, "meta": f"{count:,}", "progress": count / total if total else None}
            for name, count in sorted(models.items(), key=lambda pair: pair[1], reverse=True)[:8]
        ],
        "empty": "No requests this week.",
    }
