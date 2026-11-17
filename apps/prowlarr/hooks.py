"""Prowlarr is connected to Sonarr, Radarr and FlareSolverr through its own API.

At every start, and every few minutes after (so Sonarr or Radarr arriving or leaving is
followed without a restart), Mu3Lab puts back what it owns:

- FlareSolverr as an indexer proxy for sites tagged ``flaresolverr``. Prowlarr only sends a
  site through it when the site carries that tag; it says so when a site fails Cloudflare's
  check, and the administrator adds the tag to that site.
- Sonarr and Radarr as applications while they are installed (their keys arrive through
  ``integrates_with``), with full sync: every site added here appears in both, and one
  removed here disappears from both. One that is uninstalled is taken off the list.

The sites themselves are the administrators' choice; Mu3Lab adds none.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from ctl import servarr
from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError

KEY_ENV = "PROWLARR_API_KEY"
PROWLARR_URL = "http://prowlarr:9696"
PROXY = "FlareSolverr (Mu3Lab)"
PROXY_TAG = "flaresolverr"
FLARESOLVERR_URL = "http://flaresolverr:8191/"
# (key the integration provides, name in Prowlarr, implementation, address on mu3lab_media, categories)
APPS: tuple[tuple[str, str, str, str, dict[str, Any]], ...] = (
    (
        "MU3LAB_SONARR_API_KEY",
        "Sonarr (Mu3Lab)",
        "Sonarr",
        "http://sonarr:8989",
        {"syncCategories": [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5090], "animeSyncCategories": [5070]},
    ),
    (
        "MU3LAB_RADARR_API_KEY",
        "Radarr (Mu3Lab)",
        "Radarr",
        "http://radarr:7878",
        {"syncCategories": [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080, 2090]},
    ),
)


def _api(ctx: HookContext) -> servarr.Servarr:
    return servarr.Servarr("Prowlarr", ctx.app.manifest.service.local_port, ctx.env()[KEY_ENV], version="v1")


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def configure(
    api: servarr.Servarr, env: dict[str, str], remember: Callable[[dict[str, str]], Any] = lambda values: None
) -> list[str]:
    """Put back Mu3Lab's connections; returns what changed, in words.

    Prowlarr never shows a key it holds, so a digest of the key it was last given is kept in
    its settings (``remember``): a reinstalled Sonarr or Radarr with a new key is sent it again.
    """
    changed = []
    tag = servarr.ensure_tag(api, PROXY_TAG)
    servarr.ensure_provider(
        api, "indexerproxy", PROXY, "FlareSolverr", {"host": FLARESOLVERR_URL, "requestTimeout": 60}, {"tags": [tag]}
    )
    connected = {str(item.get("name")) for item in api.get("applications") or []}
    for key_env, name, implementation, url, categories in APPS:
        key = env.get(key_env, "")
        sent = key_env + "_SENT"
        if not key:
            if servarr.remove_provider(api, "applications", name):
                changed.append(f"{implementation} disconnected")
            continue
        servarr.ensure_provider(
            api,
            "applications",
            name,
            implementation,
            {"prowlarrUrl": PROWLARR_URL, "baseUrl": url, "apiKey": key, **categories},
            {"syncLevel": "fullSync"},
            force=env.get(sent) != _digest(key),
        )
        if env.get(sent) != _digest(key):
            remember({sent: _digest(key)})
        if name not in connected:
            changed.append(f"{implementation} connected")
    return changed


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        ctx.stage("configure_application", "Connect Prowlarr to FlareSolverr, Sonarr and Radarr.")
        api = _api(ctx)
        try:
            api.wait()
            configure(api, ctx.env(), ctx.set_env)
        except LoopbackError as exc:
            ctx.fail(
                "configure_application", "application_configuration_failed", f"Prowlarr could not be set up: {exc}"
            )

    def periodic(self, ctx: HookContext) -> str:
        changed = configure(_api(ctx), ctx.env(), ctx.set_env)
        return f"Prowlarr: {', '.join(changed)}." if changed else ""
