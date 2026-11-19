"""Radarr is set up through its own API; nobody sees its setup screens.

At every start Mu3Lab puts back what it owns (``ctl.servarr.configure_library``): Movies as
the library folder, qBittorrent as the download client in the ``radarr`` category, Jellyfin's
names ("Film Name (2010)/Film Name (2010).mkv"). Then Recyclarr, a service beside Radarr,
applies the quality rules in ``recyclarr.yml``: size limits, unwanted-release rules and the
"Mu3Lab" profile (4K, else 1080p, 720p or DVD; the first good copy is kept, no upgrades).
Cam recordings, telesyncs and screeners are never downloaded. Everything else (which films
to add, extra settings) is the administrators' to change in Radarr.

Every few minutes it also adds qBittorrent if it was stopped at setup, and marks any site
Prowlarr added since as failing a download that holds a program, so Radarr skips it and
tries another release.
"""

from __future__ import annotations

from ctl import servarr
from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError
from ctl.runtime import RuntimePaths

KEY_ENV = "RADARR_API_KEY"
CATEGORY = "radarr"
MOVIES = "/media/movies"
# qBittorrent's name and port on its own network, which this app joins (docker-compose.yml).
QBITTORRENT = ("qbittorrent", 8479)
NAMING = {
    "renameMovies": True,
    "replaceIllegalCharacters": True,
    "standardMovieFormat": "{Movie Title} ({Release Year}) {Quality Full}",
    "movieFolderFormat": "{Movie Title} ({Release Year})",
}


def _api(ctx: HookContext) -> servarr.Servarr:
    return servarr.Servarr("Radarr", ctx.app.manifest.service.local_port, ctx.env()[KEY_ENV])


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        ctx.stage("configure_application", "Connect Radarr to qBittorrent and set its quality.")
        api = _api(ctx)
        try:
            api.wait()
            waiting = servarr.configure_library(
                api,
                client=QBITTORRENT,
                root=MOVIES,
                category_field="movieCategory",
                category=CATEGORY,
                naming=NAMING,
            )
            servarr.apply_quality_rules(ctx.compose, "Radarr", ctx.log)
        except (LoopbackError, ValueError) as exc:
            ctx.fail("configure_application", "application_configuration_failed", f"Radarr could not be set up: {exc}")
        for note in waiting:
            ctx.log(f"Radarr: {note}")

    def periodic(self, ctx: HookContext) -> str:
        api = _api(ctx)
        notes = []
        paths = ctx.facts.paths if ctx.facts else RuntimePaths()
        if servarr.keep_download_client(
            api, QBITTORRENT, "movieCategory", CATEGORY, client_app="qbittorrent", paths=paths, log=ctx.log
        ):
            notes.append("now downloads with qBittorrent")
        changed = servarr.fail_dangerous_downloads(api)
        if changed:
            notes.append(f"skips downloads holding programs from {', '.join(changed)}")
        return f"Radarr {' and '.join(notes)}." if notes else ""
