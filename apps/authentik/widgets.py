"""Home widgets for Authentik (manifest ``widgets``; ctl/app_widgets.py runs them).

Sign-ins counts the last 24 hours for administrators, and lists recent failed sign-ins with the
name that was tried, so a mistyped password and someone guessing look different.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError, ago
from ctl.integrations.authentik import Authentik, AuthentikError

# Authentik's event list ignores time filters: read the newest events and count the last day.
PAGE = 200


def _recent(api: Authentik, action: str, since: datetime) -> list[dict[str, Any]]:
    found = api.request("GET", "/events/events/", params={"action": action, "ordering": "-created", "page_size": PAGE})
    return [event for event in found.get("results") or [] if datetime.fromisoformat(event["created"]) >= since]


def sign_ins(ctx: WidgetContext) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=1)
    try:
        api = Authentik.runtime()
        people = api.request("GET", "/core/users/", params={"page_size": 1, "is_active": "true", "type": "internal"})
        logins = _recent(api, "login", since)
        failures = _recent(api, "login_failed", since)
    except AuthentikError as exc:
        raise WidgetError(str(exc)) from None
    items = []
    for event in failures[:10]:
        context = event.get("context") or {}
        tried = str(context.get("username") or (event.get("user") or {}).get("username") or "an unknown name")
        items.append(
            {
                "title": f"Failed sign-in as {tried}",
                "subtitle": f"From {event.get('client_ip') or 'an unknown address'}",
                "meta": ago(event["created"]),
                "tone": "warn",
            }
        )
    return {
        "stats": [
            {"label": "People", "value": str((people.get("pagination") or {}).get("count", 0))},
            {"label": "Sign-ins today", "value": str(len(logins))},
            {"label": "Failed", "value": str(len(failures)), "tone": "warn" if failures else "good"},
        ],
        "items_title": "Failed sign-ins",
        "items": items,
        "empty": "No failed sign-ins in the last 24 hours.",
    }
