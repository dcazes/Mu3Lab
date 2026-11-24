"""Home widgets for Cleanuparr (manifest ``widgets``; ctl/app_widgets.py runs them).

Stuck downloads (administrators): the downloads Cleanuparr is counting strikes against, closest to
removal first (with how near they are and roughly when), then the ones it removed this week and why,
from Cleanuparr's own strikes and events (its Strikes and Events pages'). Cleanuparr skips its login
for this server's own addresses (apps/cleanuparr/hooks.py), so the widget needs no key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ctl.app_widgets import WidgetContext, ago

# Cleanuparr checks downloads every 15 minutes (apps/cleanuparr/hooks.py): one strike a check.
CHECK_MINUTES = 15
REMOVED_DAYS = 7
WORDS = {
    "Stalled": "No progress",
    "DownloadingMetadata": "Stuck fetching details",
    "SlowSpeed": "Too slow",
    "SlowTime": "Too slow",
    "FailedImport": "Could not be imported",
    "DeadTorrent": "No one sharing it",
    "Errored": "Download error",
}
REASONS = {
    "Stalled": "no progress",
    "DownloadingMetadata": "stuck fetching details",
    "SlowSpeed": "too slow",
    "SlowTime": "too slow",
    "FailedImport": "could not be imported",
    "AllFilesSkipped": "every file skipped",
    "AllFilesSkippedByQBit": "every file skipped",
    "AllFilesBlocked": "blocked files",
    "AtLeastOneFileBlocked": "a blocked file",
    "DownloadFailed": "the download failed",
    "Errored": "a download error",
}


def _api(ctx: WidgetContext, path: str, **params: Any) -> Any:
    return ctx.get("/api/" + path, params=params or None, headers={"Accept": "application/json"})


def _smallest(rules: Any) -> int:
    limits = [int(rule.get("maxStrikes") or 0) for rule in rules or [] if rule.get("enabled")]
    return min((limit for limit in limits if limit > 0), default=0)


def _limits(ctx: WidgetContext) -> dict[str, int]:
    """How many strikes remove a download, for each kind of strike (0: that check is off)."""
    config = _api(ctx, "configuration/queue_cleaner") or {}
    slow = _smallest(_api(ctx, "queue-rules/slow"))
    return {
        "Stalled": _smallest(_api(ctx, "queue-rules/stall")),
        "DownloadingMetadata": int(config.get("downloadingMetadataMaxStrikes") or 0),
        "SlowSpeed": slow,
        "SlowTime": slow,
        "FailedImport": int((config.get("failedImport") or {}).get("maxStrikes") or 0),
        "Errored": int((config.get("errored") or {}).get("maxStrikes") or 0),
    }


def _left(strikes: int, limit: int) -> str:
    """Roughly how long until the download is removed, if it stays stuck."""
    minutes = max(limit - strikes, 0) * CHECK_MINUTES
    if not minutes:
        return "removed next check"
    return f"{minutes} min left" if minutes < 60 else f"about {round(minutes / 60)} h left"


def downloads(ctx: WidgetContext) -> dict[str, Any]:
    limits = _limits(ctx)
    struck = (_api(ctx, "strikes", page=1, pageSize=50) or {}).get("items") or []
    watched = []
    for entry in struck:
        if entry.get("isRemoved"):
            continue
        by_type = entry.get("strikesByType") or {}
        kind, count = max(by_type.items(), key=lambda pair: pair[1], default=("Stalled", 0))
        limit = limits.get(kind) or 0
        share = min(count / limit, 1.0) if limit else 0.0
        watched.append((share, entry, kind, int(count), limit))
    watched.sort(key=lambda row: row[0], reverse=True)
    since = (datetime.now(UTC) - timedelta(days=REMOVED_DAYS)).isoformat()
    removed = (_api(ctx, "events", page=1, pageSize=20, eventType="QueueItemDeleted", fromDate=since) or {}).get(
        "items"
    ) or []
    removed = [event for event in removed if not event.get("isDryRun")]
    items = [
        {
            "title": str(entry.get("title") or "A download"),
            "subtitle": f"{WORDS.get(kind, kind)} · {count} of {limit} strikes"
            if limit
            else f"{WORDS.get(kind, kind)} · {count} strikes",
            "meta": _left(count, limit) if limit else "",
            "progress": share if limit else None,
            "tone": "bad" if share >= 0.9 else "warn" if share >= 0.5 else "",
        }
        for share, entry, kind, count, limit in watched
    ]
    items += [
        {
            "title": str(event.get("itemTitle") or "A download"),
            "subtitle": f"Removed: {REASONS.get(str(event.get('deleteReason')), 'stuck')}",
            "meta": f"removed {ago(str(event['timestamp']))}" if event.get("timestamp") else "removed",
        }
        for event in removed
    ]
    return {
        "stats": [
            {"label": "Being watched", "value": str(len(watched)), "tone": "warn" if watched else "good"},
            {"label": "Removed this week", "value": str(len(removed))},
        ],
        "items_title": "Stuck downloads",
        "items": items,
        "empty": "Nothing is stuck: every download is moving.",
    }
