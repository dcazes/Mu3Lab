"""Home widgets for ContextForge (manifest ``widgets``; ctl/app_widgets.py runs them).

Connectors (administrators): which apps' chat tools are live in ContextForge now, and how many tool
calls each app's tools have answered and failed, from ContextForge's own figures (its Metrics page's),
as Mu3Lab's ContextForge administrator. A person's own connectors (the calendar's) count under their
app, without their names.
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

CALENDAR = "Calendar"


def _app_name(text: str, separator: str) -> str:
    # Mu3Lab names a connector "<app>: <connector>" and its tools' virtual server "<app> - <connector>";
    # each person's calendar connector is "Calendar for <name>", counted as one Calendar.
    name = text.split(separator, 1)[0].strip() if separator in text else text.strip()
    return CALENDAR if name.startswith(CALENDAR + " ") else name


def _calls(count: int) -> str:
    return f"{count:,} call" if count == 1 else f"{count:,} calls"


def connectors(ctx: WidgetContext) -> dict[str, Any]:
    from ctl import contextforge

    try:
        with contextforge.client(ctx.paths) as api:
            gateways = api.gateways()
            servers = api.request(
                "GET", "/v1/servers", params={"limit": 0, "include_metrics": "true", "include_inactive": "true"}
            )
    except contextforge.ContextForgeError as exc:
        raise WidgetError(str(exc)) from None
    apps: dict[str, dict[str, Any]] = {}
    for gateway in gateways:
        name = _app_name(str(gateway.get("description") or gateway.get("name") or ""), ":")
        entry = apps.setdefault(name, {"live": False, "down": False, "tools": 0, "ok": 0, "failed": 0})
        if gateway.get("enabled"):
            if gateway.get("reachable"):
                entry["live"] = True
            else:
                entry["down"] = True
        entry["tools"] = max(entry["tools"], int(gateway.get("toolCount") or 0))
    for server in servers if isinstance(servers, list) else []:
        entry = apps.get(_app_name(str(server.get("name") or ""), " - "))
        metrics = server.get("metrics") or {}
        if entry is not None:
            entry["ok"] += int(metrics.get("successfulExecutions") or 0)
            entry["failed"] += int(metrics.get("failedExecutions") or 0)
    live = sum(1 for entry in apps.values() if entry["live"])
    calls = sum(entry["ok"] + entry["failed"] for entry in apps.values())
    failed = sum(entry["failed"] for entry in apps.values())

    def item(name: str, entry: dict[str, Any]) -> dict[str, Any]:
        total = entry["ok"] + entry["failed"]
        if entry["live"]:
            state, tone = f"Live · {entry['tools']} tools", "good"
        elif entry["down"]:
            state, tone = "Switched on, but not answering", "bad"
        else:
            state, tone = "Off while its app is stopped", ""
        return {
            "title": name,
            "subtitle": state,
            "meta": f"{_calls(total)} · {entry['failed']} failed" if entry["failed"] else _calls(total),
            "tone": "warn" if entry["failed"] and tone != "bad" else tone,
        }

    order = sorted(apps.items(), key=lambda pair: (not pair[1]["live"], not pair[1]["down"], -pair[1]["ok"]))
    return {
        "stats": [
            {"label": "Live connectors", "value": f"{live} of {len(apps)}", "tone": "good" if live else ""},
            {"label": "Tool calls", "value": f"{calls:,}"},
            {"label": "Failed", "value": f"{failed:,}", "tone": "warn" if failed else "good"},
        ],
        "items_title": "Connectors",
        "items": [item(name, entry) for name, entry in order],
        "empty": "No app connectors yet: they appear as apps with chat tools are installed.",
        "note": "Calls since ContextForge was installed.",
    }
