"""Home widgets for FreeLLMAPI (manifest ``widgets``; ctl/app_widgets.py runs them).

Free AI usage (administrators): the calls Mu3Lab's free outside providers answered today and this
week, how many failed, and the models called most this week, from FreeLLMAPI's own analytics (its
dashboard's), signed in as its administrator.
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

# FreeLLMAPI's dashboard sign-in, kept between refreshes; signed in again when it lapses.
_token: dict[str, str] = {}


def _sign_in(ctx: WidgetContext) -> str:
    values = ctx.env()
    email, password = values.get("FREELLMAPI_ADMIN_EMAIL", ""), values.get("FREELLMAPI_ADMIN_PASSWORD", "")
    if not email or not password:
        raise WidgetError("FreeLLMAPI's administrator sign-in is missing; reinstall it from its page in Apps.")
    reply = ctx.request("POST", "/api/auth/login", json={"email": email, "password": password})
    token = str((reply or {}).get("token") or "")
    if not token:
        raise WidgetError("FreeLLMAPI did not accept Mu3Lab's sign-in.")
    _token["admin"] = token
    return token


def _get(ctx: WidgetContext, path: str, **params: Any) -> Any:
    token = _token.get("admin") or _sign_in(ctx)
    try:
        return ctx.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
    except WidgetError:
        # A restarted FreeLLMAPI forgets sign-ins: sign in again once.
        return ctx.get(path, params=params, headers={"Authorization": f"Bearer {_sign_in(ctx)}"})


def usage(ctx: WidgetContext) -> dict[str, Any]:
    day = _get(ctx, "/api/analytics/summary", range="24h") or {}
    week = _get(ctx, "/api/analytics/summary", range="7d") or {}
    models = _get(ctx, "/api/analytics/by-model", range="7d") or []
    # Several endpoints can serve one model: one row per model, their calls added up.
    calls: dict[str, int] = {}
    for row in models if isinstance(models, list) else []:
        name = str(row.get("displayName") or row.get("modelId") or "")
        if name:
            calls[name] = calls.get(name, 0) + int(row.get("requests") or 0)
    total = sum(calls.values())
    rate = week.get("successRate")
    failed = round(int(week.get("totalRequests") or 0) * (100 - float(rate)) / 100) if rate is not None else 0
    return {
        "stats": [
            {"label": "Calls today", "value": f"{int(day.get('totalRequests') or 0):,}"},
            {"label": "This week", "value": f"{int(week.get('totalRequests') or 0):,}"},
            {
                "label": "Failed this week",
                "value": f"{failed:,}",
                "tone": "warn" if failed else "good",
            },
        ],
        "items_title": "Models called this week",
        "items": [
            {"title": name, "meta": f"{count:,}", "progress": count / total if total else None}
            for name, count in sorted(calls.items(), key=lambda pair: pair[1], reverse=True)[:8]
        ],
        "empty": "No calls this week.",
    }
