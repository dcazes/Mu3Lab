"""Home widgets for qBittorrent (manifest ``widgets``; ctl/app_widgets.py runs them).

Downloads (administrators): speeds, what is downloading with its progress and time left, and how many
are seeding, through qbittorrent-api (the client Home Assistant uses). qBittorrent lets this server
in without a password; everyone else signs in through Authentik.
"""

from __future__ import annotations

from typing import Any

import qbittorrentapi

from ctl.app_widgets import WidgetContext, WidgetError

STALLED = ("stalledDL", "metaDL", "queuedDL", "pausedDL", "stoppedDL")


def _speed(value: float) -> str:
    for size, unit in ((1024**3, "GB/s"), (1024**2, "MB/s"), (1024, "KB/s")):
        if value >= size:
            return f"{value / size:.1f} {unit}"
    return f"{int(value)} B/s"


def _left(seconds: int) -> str:
    if seconds <= 0 or seconds >= 8640000:  # qBittorrent's "never"
        return ""
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min left"
    return f"{max(1, seconds // 60)} min left"


def downloads(ctx: WidgetContext) -> dict[str, Any]:
    client = qbittorrentapi.Client(
        host=f"http://127.0.0.1:{ctx.app.manifest.service.local_port}",
        # qBittorrent answers only to names it knows (start.py: HostHeaderValidation); this server is one.
        EXTRA_HEADERS={"Host": "localhost"},
        REQUESTS_ARGS={"timeout": 10},
        DISABLE_LOGGING_DEBUG_OUTPUT=True,
    )
    try:
        transfer = client.transfer_info()
        downloading = client.torrents_info(status_filter="downloading", sort="progress", reverse=True)
        seeding = client.torrents_info(status_filter="seeding")
    except (qbittorrentapi.APIConnectionError, qbittorrentapi.LoginFailed):
        raise WidgetError("qBittorrent did not answer.") from None
    except qbittorrentapi.APIError:
        raise WidgetError("qBittorrent did not give its downloads.") from None
    items = []
    for torrent in downloading:
        stalled = torrent.state in STALLED
        items.append(
            {
                "title": torrent.name,
                "subtitle": f"{_speed(torrent.dlspeed)}" if torrent.dlspeed else ("Stalled" if stalled else "Starting"),
                "progress": float(torrent.progress),
                "meta": _left(int(torrent.eta)) or f"{torrent.progress:.0%}",
                "tone": "warn" if stalled else "",
            }
        )
    return {
        "stats": [
            {"label": "Down", "value": _speed(transfer.dl_info_speed)},
            {"label": "Up", "value": _speed(transfer.up_info_speed)},
            {"label": "Downloading", "value": str(len(downloading))},
            {"label": "Seeding", "value": str(len(seeding))},
        ],
        "items_title": "Downloading",
        "items": items,
        "empty": "Nothing is downloading.",
    }
