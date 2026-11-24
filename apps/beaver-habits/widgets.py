"""Home widgets for Beaver Habit Tracker (manifest ``widgets``; ctl/app_widgets.py runs them).

Habits: the viewer's own habits for today, ticked off right in the widget, with each one's current
streak, through Beaver's API with their own token (the one Mu3Lab made for their chat connector).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from ctl.app_widgets import WidgetContext

CONNECTOR = "beaver-adapter"
TOKEN_ENV = "BEAVER_API_TOKEN"
FORMAT = "%Y-%m-%d"
# How far back a streak is counted.
LOOKBACK = 120


def _headers(ctx: WidgetContext) -> dict[str, str]:
    return {"Authorization": f"Bearer {ctx.own_credentials(CONNECTOR).get(TOKEN_ENV, '')}"}


def _streak(done: set[str], today: date) -> int:
    """Days in a row up to today (or up to yesterday, while today is not ticked yet)."""
    day = today if today.strftime(FORMAT) in done else today - timedelta(days=1)
    count = 0
    while day.strftime(FORMAT) in done:
        count += 1
        day -= timedelta(days=1)
    return count


def habits(ctx: WidgetContext) -> dict[str, Any]:
    headers = _headers(ctx)
    today = date.today()
    found = ctx.get("/api/v1/habits", headers=headers) or []
    items = []
    streaks = []
    done_today = 0
    for habit in found:
        completions = ctx.get(
            f"/api/v1/habits/{quote(str(habit['id']), safe='')}/completions",
            params={
                "date_fmt": FORMAT,
                "date_start": (today - timedelta(days=LOOKBACK)).strftime(FORMAT),
                "date_end": today.strftime(FORMAT),
                "sort": "desc",
                "limit": LOOKBACK + 1,
            },
            headers=headers,
        )
        dates = {str(day) for day in completions or []}
        done = today.strftime(FORMAT) in dates
        done_today += done
        streak = _streak(dates, today)
        streaks.append(streak)
        items.append(
            {
                "title": str(habit.get("name") or "A habit"),
                "subtitle": f"{streak}-day streak" if streak > 1 else "",
                "check": done,
                # Ticking sets today to the opposite of what it is now.
                "action": f"{habit['id']}:{'0' if done else '1'}",
            }
        )
    return {
        "stats": [
            {
                "label": "Done today",
                "value": f"{done_today} of {len(items)}",
                "tone": "good" if items and done_today == len(items) else "",
            },
            {"label": "Longest streak", "value": f"{max(streaks, default=0)} days"},
        ],
        "items": items,
        "empty": "No habits yet. Add one in Beaver Habits.",
    }


def habits_action(ctx: WidgetContext, action: str) -> None:
    habit, _, value = action.rpartition(":")
    ctx.request(
        "POST",
        f"/api/v1/habits/{quote(habit, safe='')}/completions",
        json={"date_fmt": FORMAT, "date": date.today().strftime(FORMAT), "done": value == "1"},
        headers=_headers(ctx),
    )
