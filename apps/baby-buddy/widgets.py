"""Home widgets for Baby Buddy (manifest ``widgets``; ctl/app_widgets.py runs them).

Baby: for each child, how long since the last feeding, sleep and nappy change, with what it was,
through Baby Buddy's API with the token Mu3Lab made for the owner (the chat connector's).
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError, ago

CONNECTOR = "babybuddy-official"
TOKEN_ENV = "BABYBUDDY_TOKEN"


def _latest(ctx: WidgetContext, path: str, child: int, headers: dict[str, str]) -> dict[str, Any]:
    found = (ctx.get(path, params={"child": child, "limit": 1}, headers=headers) or {}).get("results") or []
    return found[0] if found else {}


def _minutes(value: Any) -> str:
    """Baby Buddy's "HH:MM:SS" durations, in plain words."""
    parts = str(value or "").split(":")
    if len(parts) != 3 or not all(part.split(".")[0].isdigit() for part in parts):
        return ""
    hours, minutes = int(parts[0]), int(parts[1])
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def baby(ctx: WidgetContext) -> dict[str, Any]:
    token = ctx.credentials(CONNECTOR).get(TOKEN_ENV, "")
    if not token:
        ctx.require_connection(CONNECTOR)
        raise WidgetError("Mu3Lab's Baby Buddy token is missing; reinstall Baby Buddy from its page in Apps.")
    headers = {"Authorization": f"Token {token}"}
    children = (ctx.get("/api/children/", headers=headers) or {}).get("results") or []
    items = []
    stats = []
    for child in children:
        name = str(child.get("first_name") or "Baby")
        feeding = _latest(ctx, "/api/feedings/", child["id"], headers)
        sleep = _latest(ctx, "/api/sleep/", child["id"], headers)
        change = _latest(ctx, "/api/changes/", child["id"], headers)
        prefix = f"{name}: " if len(children) > 1 else ""
        if feeding:
            what = " · ".join(
                str(part).replace("_", " ") for part in (feeding.get("type"), feeding.get("method")) if part
            )
            items.append({"title": f"{prefix}Fed {ago(feeding['end'])}", "subtitle": what.capitalize(), "tone": ""})
        if sleep:
            items.append(
                {
                    "title": f"{prefix}Woke {ago(sleep['end'])}",
                    "subtitle": f"Slept {_minutes(sleep.get('duration'))}" if sleep.get("duration") else "",
                }
            )
        if change:
            kind = " and ".join(
                word for word, flag in (("wet", change.get("wet")), ("solid", change.get("solid"))) if flag
            )
            items.append({"title": f"{prefix}Changed {ago(change['time'])}", "subtitle": kind.capitalize()})
        if len(children) == 1:
            stats = [
                {"label": "Last feeding", "value": ago(feeding["end"]) if feeding else "None yet"},
                {"label": "Last sleep", "value": ago(sleep["end"]) if sleep else "None yet"},
                {"label": "Last change", "value": ago(change["time"]) if change else "None yet"},
            ]
    return {
        "stats": stats,
        "items": items,
        "empty": "Nothing logged yet. Log a feeding, sleep or change in Baby Buddy.",
    }
