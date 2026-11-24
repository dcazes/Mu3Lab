"""Home widgets for n8n (manifest ``widgets``; ctl/app_widgets.py runs them).

Automations (administrators): how many workflows are switched on, how many runs failed in the last day,
and which, through n8n's public API with an API key an administrator pastes into the widget's
settings (n8n makes API keys only in its own settings).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ctl.app_widgets import WidgetContext, ago


def _since(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def automations(ctx: WidgetContext) -> dict[str, Any]:
    headers = {"X-N8N-API-KEY": ctx.key()}
    flows = (ctx.get("/api/v1/workflows", params={"limit": 250}, headers=headers) or {}).get("data") or []
    names = {str(flow.get("id")): str(flow.get("name") or "A workflow") for flow in flows}
    runs = (ctx.get("/api/v1/executions", params={"limit": 250}, headers=headers) or {}).get("data") or []
    day = datetime.now(UTC) - timedelta(days=1)
    recent = [run for run in runs if (_since(str(run.get("startedAt") or "")) or day) > day]
    failed = [run for run in recent if run.get("status") in ("error", "crashed")]
    return {
        "stats": [
            {"label": "Switched on", "value": f"{sum(1 for flow in flows if flow.get('active'))} of {len(flows)}"},
            {"label": "Runs today", "value": str(len(recent))},
            {"label": "Failed", "value": str(len(failed)), "tone": "bad" if failed else "good"},
        ],
        "items_title": "Failed in the last day",
        "items": [
            {
                "title": names.get(str(run.get("workflowId")), "A workflow"),
                "subtitle": "Crashed" if run.get("status") == "crashed" else "Stopped with an error",
                "meta": ago(str(run.get("startedAt") or "")),
                "tone": "bad",
                "url": ctx.web(f"/workflow/{run.get('workflowId')}/executions/{run.get('id')}"),
            }
            for run in failed[:10]
        ],
        "empty": "No failed runs in the last day.",
    }
