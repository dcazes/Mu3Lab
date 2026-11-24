"""Home widgets for Prowlarr (manifest ``widgets``; ctl/app_widgets.py runs them).

Indexers (administrators): how many sites Prowlarr searches, how many searches and grabs they served,
and which are failing now, read with Mu3Lab's client for the Servarr apps (``ctl.servarr``).
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError, until
from ctl.engine.loopback import LoopbackError
from ctl.servarr import Servarr

KEY_ENV = "PROWLARR_API_KEY"


def indexers(ctx: WidgetContext) -> dict[str, Any]:
    key = ctx.env().get(KEY_ENV, "")
    if not key:
        raise WidgetError("Prowlarr's API key is missing; reinstall Prowlarr from its page in Apps.")
    api = Servarr(ctx.name, ctx.app.manifest.service.local_port, key, version="v1")
    try:
        sites = api.get("indexer") or []
        stats = (api.get("indexerstats") or {}).get("indexers") or []
        failing = {item.get("indexerId"): item for item in api.get("indexerstatus") or []}
    except LoopbackError as exc:
        raise WidgetError(str(exc)) from None
    names = {site.get("id"): str(site.get("name") or "An indexer") for site in sites}
    by_id = {item.get("indexerId"): item for item in stats}
    items = []
    for site_id, name in names.items():
        figures = by_id.get(site_id) or {}
        problem = failing.get(site_id)
        items.append(
            {
                "title": name,
                "subtitle": f"{figures.get('numberOfQueries', 0)} searches · {figures.get('numberOfGrabs', 0)} grabs",
                "meta": f"Failing, retries {until(problem['disabledTill'])}"
                if problem and problem.get("disabledTill")
                else "Failing"
                if problem
                else "Working",
                "tone": "bad" if problem else "good",
            }
        )
    items.sort(key=lambda item: (item["tone"] != "bad", item["title"]))
    return {
        "stats": [
            {"label": "Indexers", "value": str(len(sites))},
            {"label": "Searches", "value": str(sum(int(item.get("numberOfQueries") or 0) for item in stats))},
            {"label": "Grabs", "value": str(sum(int(item.get("numberOfGrabs") or 0) for item in stats))},
            {"label": "Failing", "value": str(len(failing)), "tone": "bad" if failing else "good"},
        ],
        "items": items,
        "empty": "No indexers yet. Add some in Prowlarr.",
    }
