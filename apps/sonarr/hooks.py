"""Sonarr is set up through its own API; nobody sees its setup screens.

At every start Mu3Lab puts back what it owns (``ctl.servarr.configure_library``): Shows as
the library folder, qBittorrent as the download client in the ``sonarr`` category, Jellyfin's
folder names ("Show Name/Season 01"), size limits and unwanted-release rules, and the
"Mu3Lab" quality profile: 1080p, else 4K, 720p or DVD, upgrading only up to 1080p
(``quality.json``). Everything else (which shows to follow, extra settings) is the
administrators' to change in Sonarr.

Every few minutes it also adds qBittorrent if it was stopped at setup, and marks any site
Prowlarr added since as failing a download that holds a program, so Sonarr skips it and
tries another release.
"""

from __future__ import annotations

from pathlib import Path

from ctl import servarr
from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError

KEY_ENV = "SONARR_API_KEY"
CATEGORY = "sonarr"
SHOWS = "/media/shows"
QUALITY = Path(__file__).with_name("quality.json")
# qBittorrent's name and port on its own network, which this app joins (docker-compose.yml).
QBITTORRENT = ("qbittorrent", 8479)
NAMING = {
    "renameEpisodes": True,
    "replaceIllegalCharacters": True,
    "standardEpisodeFormat": "{Series Title} - S{season:00}E{episode:00} - {Episode Title} {Quality Full}",
    "dailyEpisodeFormat": "{Series Title} - {Air-Date} - {Episode Title} {Quality Full}",
    "animeEpisodeFormat": "{Series Title} - S{season:00}E{episode:00} - {Episode Title} {Quality Full}",
    # The layout finished.py and Jellyfin use: "Show Name/Season 02".
    "seriesFolderFormat": "{Series Title}",
    "seasonFolderFormat": "Season {season:00}",
}


def _api(ctx: HookContext) -> servarr.Servarr:
    return servarr.Servarr("Sonarr", ctx.app.manifest.service.local_port, ctx.env()[KEY_ENV])


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        ctx.stage("configure_application", "Connect Sonarr to qBittorrent and set its quality.")
        api = _api(ctx)
        try:
            api.wait()
            waiting = servarr.configure_library(
                api,
                client=QBITTORRENT,
                root=SHOWS,
                category_field="tvCategory",
                category=CATEGORY,
                naming=NAMING,
                quality=QUALITY,
            )
        except (LoopbackError, ValueError) as exc:
            ctx.fail("configure_application", "application_configuration_failed", f"Sonarr could not be set up: {exc}")
        for note in waiting:
            ctx.log(f"Sonarr: {note}")

    def periodic(self, ctx: HookContext) -> str:
        api = _api(ctx)
        notes = []
        try:
            if servarr.ensure_download_client(api, QBITTORRENT, "tvCategory", CATEGORY):
                notes.append("now downloads with qBittorrent")
        except LoopbackError:
            pass  # qBittorrent is stopped; tried again next time
        changed = servarr.fail_dangerous_downloads(api)
        if changed:
            notes.append(f"skips downloads holding programs from {', '.join(changed)}")
        return f"Sonarr {' and '.join(notes)}." if notes else ""
