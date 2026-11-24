"""Home widgets for Navidrome (manifest ``widgets``; ctl/app_widgets.py runs them).

Music: how many songs the household library holds, when it was last scanned, and the newest albums,
through Navidrome's Subsonic API as Mu3Lab's own account (the chat connector's).
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError, ago

CONNECTOR = "navidrome-adapter"
USERNAME = "mu3lab-service"
PASSWORD_ENV = "NAVIDROME_PASSWORD"


def _call(ctx: WidgetContext, method: str, **params: Any) -> dict[str, Any]:
    password = ctx.credentials(CONNECTOR).get(PASSWORD_ENV, "")
    if not password:
        raise WidgetError("Mu3Lab's Navidrome sign-in is missing; reinstall Navidrome from its page in Apps.")
    salt = secrets.token_hex(8)
    reply = ctx.get(
        f"/rest/{method}",
        params={
            "u": USERNAME,
            # The Subsonic API's own sign-in: the MD5 of the password and a fresh salt.
            "t": hashlib.md5((password + salt).encode()).hexdigest(),
            "s": salt,
            "v": "1.16.1",
            "c": "mu3lab",
            "f": "json",
            **params,
        },
    )
    body = (reply or {}).get("subsonic-response") or {}
    if body.get("status") != "ok":
        raise WidgetError("Navidrome refused Mu3Lab's sign-in.")
    return body


def music(ctx: WidgetContext) -> dict[str, Any]:
    scan = _call(ctx, "getScanStatus").get("scanStatus") or {}
    albums = (_call(ctx, "getAlbumList2", type="newest", size=8).get("albumList2") or {}).get("album") or []
    last = str(scan.get("lastScan") or "")
    return {
        "stats": [
            {"label": "Songs", "value": str(scan.get("count", 0))},
            {"label": "Folders", "value": str(scan.get("folderCount", 0))},
            {"label": "Scanned", "value": "Now" if scan.get("scanning") else ago(last) if last else "Not yet"},
        ],
        "items_title": "Newest albums",
        "items": [
            {
                "title": str(album.get("name") or "An album"),
                "subtitle": " · ".join(str(part) for part in (album.get("artist"), album.get("year")) if part),
                "meta": ago(album["created"]) if album.get("created") else "",
            }
            for album in albums
        ],
        "empty": "No music yet. Add some to the Music folder.",
    }
