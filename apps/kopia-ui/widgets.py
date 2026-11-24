"""Home widgets for Kopia backups (manifest ``widgets``; ctl/app_widgets.py runs them).

Backups shows what Mu3Lab itself records about every backup, so it works while Kopia is stopped:
the newest backup of Mu3Lab and of each app, whether it was checked, and the next scheduled run.
"""

from __future__ import annotations

from typing import Any

from ctl import backup_service
from ctl import backups as saved
from ctl.app_widgets import WidgetContext, ago, until
from ctl.manifest.catalog import cached

LOOK = {
    "missing": ("bad", "Not backed up"),
    "stale": ("warn", "Over a week old"),
    "unchecked": ("warn", "Not checked yet"),
    "checked": ("good", "Checked"),
}
ORDER = ["missing", "stale", "unchecked", "checked"]


def backups(ctx: WidgetContext) -> dict[str, Any]:
    status = backup_service.status(ctx.paths)
    platform = saved.protection(saved.PLATFORM_SOURCE, ctx.paths)
    catalog = cached()
    # Exactly what Kopia backs up (the Backups page's list): each app with data, and the media libraries.
    rows = []
    for source in backup_service.sources(ctx.app.folder.parents[1], ctx.paths):
        if source == saved.PLATFORM_SOURCE:
            continue
        name = "Media libraries" if source == saved.MEDIA_SOURCE else catalog.get(source).manifest.name
        rows.append((name, saved.protection(source, ctx.paths)))
    rows.sort(key=lambda row: (ORDER.index(row[1]["state"]) if row[1]["state"] in ORDER else 0, row[0]))
    good = sum(1 for _name, found in rows if found["state"] == "checked")
    failed = len(status["problems"])
    newest = str(platform.get("completed_at") or "")
    items = []
    for name, found in rows:
        tone, label = LOOK.get(found["state"], ("", found["state"]))
        when = str(found.get("completed_at") or "")
        items.append(
            {
                "title": name,
                "subtitle": f"Saved {ago(when)}" if when else "No backup yet",
                "meta": label,
                "tone": tone,
            }
        )
    note = ""
    if not status["service_installed"]:
        note = "Kopia backups isn't installed, so nothing is backed up on a schedule."
    elif not status["service_running"]:
        note = "The backup service is stopped, so no new backups are being made."
    return {
        "stats": [
            {
                "label": "Last backup",
                "value": ago(newest) if newest else "Never",
                "tone": "bad" if not newest else "warn" if platform["state"] == "stale" else "",
            },
            {
                "label": "Next",
                "value": until(status["next_run_at"]) if status["next_run_at"] else "Not scheduled",
                "tone": "" if status["next_run_at"] else "warn",
            },
            {"label": "Checked", "value": f"{good} of {len(rows)}", "tone": "good" if good == len(rows) else "warn"},
            {"label": "Failed", "value": str(failed), "tone": "bad" if failed else "good"},
        ],
        "items_title": "Apps",
        "items": items,
        "empty": "No apps to back up yet.",
        "note": note,
    }
