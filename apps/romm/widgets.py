"""Home widgets for RomM (manifest ``widgets``; ctl/app_widgets.py runs them).

Games: how many games and consoles the household library holds, with saves and the consoles with the
most games, read with the read-only token Mu3Lab made for the owner (the chat connector's).
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

CONNECTOR = "romm-adapter"
TOKEN_ENV = "ROMM_API_TOKEN"
GB = 1024**3


def games(ctx: WidgetContext) -> dict[str, Any]:
    token = ctx.credentials(CONNECTOR).get(TOKEN_ENV, "")
    if not token:
        raise WidgetError("Mu3Lab's RomM token is missing; reinstall RomM from its page in Apps.")
    headers = {"Authorization": f"Bearer {token}"}
    figures = ctx.get("/api/stats", headers=headers) or {}
    consoles = ctx.get("/api/platforms", headers=headers) or []
    consoles = sorted(consoles, key=lambda console: int(console.get("rom_count") or 0), reverse=True)
    size = float(figures.get("TOTAL_FILESIZE_BYTES") or figures.get("FILESIZE") or 0)
    return {
        "stats": [
            {"label": "Games", "value": f"{int(figures.get('ROMS') or 0):,}"},
            {"label": "Consoles", "value": str(figures.get("PLATFORMS", 0))},
            {"label": "Saves", "value": str(int(figures.get("SAVES") or 0) + int(figures.get("STATES") or 0))},
            *([{"label": "Size", "value": f"{size / GB:,.1f} GB"}] if size else []),
        ],
        "items_title": "Consoles",
        "items": [
            {
                "title": str(console.get("display_name") or console.get("name") or "A console"),
                "meta": f"{int(console.get('rom_count') or 0)} games",
                "url": ctx.web(f"/platform/{console['id']}") if console.get("id") else "",
            }
            for console in consoles[:8]
            if console.get("rom_count")
        ],
        "empty": "No games yet. Add some to the Games folder.",
    }
