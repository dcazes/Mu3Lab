"""Home widgets for Nextcloud (manifest ``widgets``; ctl/app_widgets.py runs them).

Storage (administrators): free space, files, shares and how many people used Nextcloud today, from
Nextcloud's own server information, with the owner's (an administrator's) app password.
"""

from __future__ import annotations

from typing import Any

from ctl.app_widgets import WidgetContext, WidgetError

CONNECTOR = "nextcloud-context-agent"
GB = 1024**3


def storage(ctx: WidgetContext) -> dict[str, Any]:
    login = ctx.credentials(CONNECTOR)
    if not login.get("NEXTCLOUD_USERNAME") or not login.get("NEXTCLOUD_APP_PASSWORD"):
        raise WidgetError("Mu3Lab's Nextcloud sign-in is missing; reinstall Nextcloud from its page in Apps.")
    reply = ctx.get(
        "/ocs/v2.php/apps/serverinfo/api/v1/info",
        params={"format": "json", "skipApps": "true", "skipUpdate": "true"},
        headers={"OCS-APIRequest": "true"},
        auth=(login["NEXTCLOUD_USERNAME"], login["NEXTCLOUD_APP_PASSWORD"]),
    )
    data = ((reply or {}).get("ocs") or {}).get("data") or {}
    cloud = data.get("nextcloud") or {}
    stored = cloud.get("storage") or {}
    free = float((cloud.get("system") or {}).get("freespace") or 0)
    active = data.get("activeUsers") or {}
    return {
        "stats": [
            {"label": "Free space", "value": f"{free / GB:,.0f} GB", "tone": "warn" if 0 < free < 20 * GB else ""},
            {"label": "Files", "value": f"{int(stored.get('num_files') or 0):,}"},
            {"label": "Shares", "value": str((cloud.get("shares") or {}).get("num_shares", 0))},
            {"label": "Active today", "value": str(active.get("last24hours", 0))},
        ],
        "note": "" if free else "Nextcloud did not say how much space is free.",
    }
