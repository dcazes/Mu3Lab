"""Set up Sonarr, Radarr and Prowlarr through their own APIs (the "Servarr" family).

The three apps share one API style: JSON over HTTP with an ``X-Api-Key`` header, settings
in ``/config/<section>``, and "providers" (download clients, indexers, applications) made
of an implementation name and a list of ``{name, value}`` fields. Each app's ``hooks.py``
says what Mu3Lab wants (folders, names, the quality profile, which apps to connect) and
calls these helpers, which change only what differs, so running them again at every start
or every few minutes changes nothing.

Nothing here names an app: the hooks pass in addresses, keys and the quality table
(``apps/<id>/quality.json``).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ctl.engine.loopback import LoopbackError

# Sonarr and Radarr call these "Executables" and "Potentially Dangerous": a download holding
# such a file is marked failed, blocked and searched for again (indexer setting failDownloads).
FAIL_DANGEROUS = [0, 1]
# A custom format scored this low is never downloaded (the profile's minimum score is 0).
UNWANTED_SCORE = -10000
GROUP_ID_BASE = 1000
# How the apps show a stored secret; sending it back keeps the stored value.
MASK = "********"


class Servarr:
    """One app's API on this computer's loopback port."""

    def __init__(self, name: str, port: int, key: str, version: str = "v3") -> None:
        self.name, self.port, self.key, self.version = name, port, key, version

    def call(self, method: str, path: str, data: Any = None) -> Any:
        url = f"http://127.0.0.1:{self.port}/api/{self.version}/{path.lstrip('/')}"
        request = urllib.request.Request(
            url,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"X-Api-Key": self.key, "Accept": "application/json", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = _reason(exc.read())
            where = path.split("?")[0]
            raise LoopbackError(f"{self.name} refused {method} {where} (HTTP {exc.code}){detail}.") from None
        except OSError as exc:
            raise LoopbackError(f"{self.name} did not answer {path.split('?')[0]}.") from exc
        return json.loads(raw) if raw.strip() else None

    def get(self, path: str) -> Any:
        return self.call("GET", path)

    def post(self, path: str, data: Any) -> Any:
        return self.call("POST", path, data)

    def put(self, path: str, data: Any) -> Any:
        return self.call("PUT", path, data)

    def delete(self, path: str) -> Any:
        return self.call("DELETE", path)

    def wait(self, seconds: int = 300) -> None:
        """Until the API answers with the key (it starts some time after the web page)."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                self.get("system/status")
                return
            except LoopbackError:
                if time.monotonic() > deadline:
                    raise
            time.sleep(3)


def _reason(raw: bytes) -> str:
    """The app's own words for a refusal (its validation messages), kept short."""
    try:
        body = json.loads(raw)
    except ValueError:
        return ""
    items = body if isinstance(body, list) else [body]
    messages = [str(item.get("errorMessage") or item.get("message") or "") for item in items if isinstance(item, dict)]
    text = "; ".join(message for message in messages if message)[:300]
    return f": {text}" if text else ""


# --- Providers (download clients, indexers, applications, indexer proxies) --------------


def field_values(item: dict[str, Any]) -> dict[str, Any]:
    return {str(field.get("name")): field.get("value") for field in item.get("fields") or []}


def with_fields(item: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """``item`` with the named fields set; fields the app adds but Mu3Lab does not own keep their value."""
    fields = [dict(field) for field in item.get("fields") or []]
    for field in fields:
        if field.get("name") in values:
            field["value"] = values[field["name"]]
    known = {field.get("name") for field in fields}
    fields += [{"name": name, "value": value} for name, value in values.items() if name not in known]
    return {**item, "fields": fields}


def ensure_provider(
    api: Servarr,
    kind: str,
    name: str,
    implementation: str,
    values: dict[str, Any],
    extra: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Create or update the provider ``name`` of type ``kind`` (e.g. ``downloadclient``).

    New ones start from the app's own template for ``implementation``, so every field the app
    expects is present. A secret the app shows masked counts as unchanged, since it cannot be
    compared; ``force`` writes every value anyway (the caller knows the secret changed).
    """
    extra = extra or {}
    existing = next((item for item in api.get(kind) or [] if item.get("name") == name), None)
    if existing is None:
        template = next(
            (item for item in api.get(f"{kind}/schema") or [] if item.get("implementation") == implementation), None
        )
        if template is None:
            raise LoopbackError(f"{api.name} has no {implementation} {kind}.")
        body = with_fields({**template, "name": name, **extra}, values)
        # Saved even while the other end is away (qBittorrent stopped, Sonarr still starting).
        return api.post(f"{kind}?forceSave=true", body)
    current = field_values(existing)
    changed = (
        force
        or any(current.get(key) not in (value, MASK) for key, value in values.items())
        or any(existing.get(key) != value for key, value in extra.items())
    )
    if not changed:
        return existing
    body = with_fields({**existing, **extra}, values)
    return api.put(f"{kind}/{existing['id']}?forceSave=true", body)


def remove_provider(api: Servarr, kind: str, name: str) -> bool:
    found = [item for item in api.get(kind) or [] if item.get("name") == name]
    for item in found:
        api.delete(f"{kind}/{item['id']}")
    return bool(found)


def ensure_tag(api: Servarr, label: str) -> int:
    for tag in api.get("tag") or []:
        if str(tag.get("label") or "").casefold() == label.casefold():
            return int(tag["id"])
    return int(api.post("tag", {"label": label})["id"])


# --- Settings ---------------------------------------------------------------------------


def set_config(api: Servarr, section: str, values: dict[str, Any]) -> bool:
    """Merge ``values`` into ``/config/<section>``; True when anything changed."""
    current = api.get(f"config/{section}")
    if all(current.get(key) == value for key, value in values.items()):
        return False
    api.put(f"config/{section}/{current['id']}", {**current, **values})
    return True


def ensure_root_folder(api: Servarr, path: str) -> None:
    if not any(str(item.get("path") or "").rstrip("/") == path.rstrip("/") for item in api.get("rootfolder") or []):
        api.post("rootfolder", {"path": path})


def fail_dangerous_downloads(api: Servarr) -> list[str]:
    """Every indexer fails a download holding a program, so another release is tried; names it changed.

    Prowlarr adds indexers without this setting, and keeps it once it is set.
    """
    changed = []
    for indexer in api.get("indexer") or []:
        values = field_values(indexer)
        if "failDownloads" not in values or sorted(values.get("failDownloads") or []) == FAIL_DANGEROUS:
            continue
        api.put(f"indexer/{indexer['id']}?forceSave=true", with_fields(indexer, {"failDownloads": FAIL_DANGEROUS}))
        changed.append(str(indexer.get("name")))
    return changed


# --- Quality ----------------------------------------------------------------------------


def load_quality(path: Path) -> dict[str, Any]:
    """An app's quality table: sizes per quality, unwanted custom formats and the profile's tiers."""
    return json.loads(path.read_text(encoding="utf-8"))


def apply_sizes(api: Servarr, sizes: dict[str, list[float]]) -> bool:
    """Minimum, preferred and maximum size (MB per minute of running time) for each named quality."""
    definitions = api.get("qualitydefinition") or []
    changed = []
    for definition in definitions:
        wanted = sizes.get(str((definition.get("quality") or {}).get("name")))
        if not wanted:
            continue
        low, preferred, high = wanted
        update = {"minSize": low, "preferredSize": preferred, "maxSize": high}
        if any(definition.get(key) != value for key, value in update.items()):
            definition.update(update)
            changed.append(definition)
    if changed:
        api.put("qualitydefinition/update", definitions)
    return bool(changed)


def custom_format(spec: dict[str, Any]) -> dict[str, Any]:
    """A TRaSH Guides custom format (fields as a mapping) in the apps' own shape (a field list)."""
    return {
        "name": spec["name"],
        "includeCustomFormatWhenRenaming": bool(spec.get("includeCustomFormatWhenRenaming")),
        "specifications": [
            {
                "name": item["name"],
                "implementation": item["implementation"],
                "negate": bool(item.get("negate")),
                "required": bool(item.get("required")),
                "fields": [{"name": key, "value": value} for key, value in (item.get("fields") or {}).items()],
            }
            for item in spec.get("specifications") or []
        ],
    }


def _specs(item: dict[str, Any]) -> list[tuple[Any, ...]]:
    """A custom format's rules, comparable whatever extra details the app adds."""
    return sorted(
        (
            spec.get("name"),
            spec.get("implementation"),
            bool(spec.get("negate")),
            bool(spec.get("required")),
            json.dumps(field_values(spec), sort_keys=True),
        )
        for spec in item.get("specifications") or []
    )


def ensure_custom_formats(api: Servarr, specs: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Create or update each custom format by name; returns ``{name: id}``."""
    existing = {str(item.get("name")): item for item in api.get("customformat") or []}
    ids = {}
    for spec in specs:
        wanted = custom_format(spec)
        current = existing.get(wanted["name"])
        if current is None:
            ids[wanted["name"]] = int(api.post("customformat", wanted)["id"])
            continue
        if _specs(current) != _specs(wanted) or bool(current.get("includeCustomFormatWhenRenaming")) != bool(
            wanted["includeCustomFormatWhenRenaming"]
        ):
            api.put(f"customformat/{current['id']}", {**wanted, "id": current["id"]})
        ids[wanted["name"]] = int(current["id"])
    return ids


def _qualities(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every quality in a profile's item list, groups opened up, in the app's order."""
    found = []
    for item in items:
        if item.get("quality"):
            found.append(item["quality"])
        found += _qualities(item.get("items") or [])
    return found


def profile_items(schema_items: list[dict[str, Any]], tiers: list[dict[str, Any]]) -> tuple[list[dict], int]:
    """A profile's items from the app's template and Mu3Lab's tiers, best first; returns (items, cutoff id).

    Each tier is ``{"name": "1080p", "qualities": [...], "cutoff": bool}`` and becomes one group,
    so any of its qualities counts the same. The apps list items worst first, so the groups go
    last in reverse; every quality in no tier is listed, not allowed, before them. A name the
    app does not have is skipped (a release can rename a quality).
    """
    by_name = {str(quality["name"]): quality for quality in _qualities(schema_items)}
    placed = {name for tier in tiers for name in tier["qualities"]}
    items: list[dict[str, Any]] = [
        {"quality": quality, "items": [], "allowed": False} for name, quality in by_name.items() if name not in placed
    ]
    cutoff = 0
    for position, tier in reversed(list(enumerate(tiers))):
        members = [by_name[name] for name in tier["qualities"] if name in by_name]
        if not members:
            continue
        group_id = GROUP_ID_BASE + 100 + position
        items.append(
            {
                "id": group_id,
                "name": tier["name"],
                "allowed": True,
                "items": [{"quality": quality, "items": [], "allowed": True} for quality in members],
            }
        )
        if tier.get("cutoff"):
            cutoff = group_id
    if not cutoff:
        raise ValueError("the quality tiers name no cutoff the app has")
    return items, cutoff


def _shape(items: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """What a profile's item list means: each allowed group's name and qualities, in order."""
    return [
        (
            item.get("name") or "",
            bool(item.get("allowed")),
            tuple(quality["id"] for quality in _qualities(item.get("items") or []) or [item.get("quality") or {}]),
        )
        for item in items
    ]


def ensure_profile(
    api: Servarr, name: str, tiers: list[dict[str, Any]], unwanted: dict[str, int], extra: dict[str, Any] | None = None
) -> int:
    """Create or update the quality profile ``name``; returns its id.

    Upgrades stop at the cutoff tier. Unwanted custom formats score far below the minimum, so a
    release matching one is never downloaded; every other custom format scores nothing.
    """
    schema = api.get("qualityprofile/schema")
    items, cutoff = profile_items(schema["items"], tiers)
    formats = [
        {
            "format": item["format"],
            "name": item.get("name"),
            "score": UNWANTED_SCORE if item["format"] in unwanted.values() else 0,
        }
        for item in schema.get("formatItems") or []
    ]
    wanted = {
        "name": name,
        "upgradeAllowed": True,
        "cutoff": cutoff,
        "items": items,
        "minFormatScore": 0,
        "cutoffFormatScore": 0,
        "minUpgradeFormatScore": 1,
        "formatItems": formats,
        **(extra or {}),
    }
    current = next((item for item in api.get("qualityprofile") or [] if item.get("name") == name), None)
    if current is None:
        return int(api.post("qualityprofile", {**schema, **wanted})["id"])
    scores = {item["format"]: item["score"] for item in current.get("formatItems") or []}
    same = (
        _shape(current["items"]) == _shape(items)
        and current.get("cutoff") == cutoff
        and current.get("upgradeAllowed") is True
        and current.get("minFormatScore") == 0
        and scores == {item["format"]: item["score"] for item in formats}
        and all(current.get(key) == value for key, value in (extra or {}).items())
    )
    if not same:
        api.put(f"qualityprofile/{current['id']}", {**current, **wanted, "id": current["id"]})
    return int(current["id"])


# --- Sonarr and Radarr ------------------------------------------------------------------

DOWNLOAD_CLIENT = "qBittorrent (Mu3Lab)"
SUBTITLES = "srt,ass,ssa,sub,idx,vtt"
# Never fill the disk: an import waits while less than this is free (MB).
FREE_SPACE_MB = 10240
# The longest the apps allow between checks of each site's newest releases (the owner's choice:
# slow is fine; a request from Seerr is searched for at once).
FEED_CHECK_MINUTES = 120


def configure_library(
    api: Servarr,
    *,
    root: str,
    client: tuple[str, int],
    category_field: str,
    category: str,
    naming: dict[str, Any],
    quality: Path,
) -> list[str]:
    """Everything Mu3Lab owns in Sonarr or Radarr, put back at every start; returns what must wait.

    - The library folder (Shows or Movies) and qBittorrent (``client``: its name and port on
      its own network) as the download client, in the app's own category. qBittorrent stops each torrent the moment it finishes (no sharing); the app
      then moves the file into the library and removes the torrent.
    - Names Jellyfin reads, with subtitles kept beside the video.
    - Size limits, the unwanted-release rules and the "Mu3Lab" quality profile (``quality``).
    - Slow feed checks, and a download holding a program fails so another release is tried.

    The app will not add qBittorrent while qBittorrent is stopped; that one waits for the
    app's upkeep (``ensure_download_client``), and the rest is done now.
    """
    table = load_quality(quality)
    waiting = []
    ensure_root_folder(api, root)
    try:
        ensure_download_client(api, client, category_field, category)
    except LoopbackError as exc:
        waiting.append(f"qBittorrent is added once it is running ({exc})")
    set_config(api, "naming", naming)
    set_config(
        api,
        "mediamanagement",
        {
            "importExtraFiles": True,
            "extraFileExtensions": SUBTITLES,
            "deleteEmptyFolders": True,
            "minimumFreeSpaceWhenImporting": FREE_SPACE_MB,
            "skipFreeSpaceCheckWhenImporting": False,
        },
    )
    set_config(api, "indexer", {"rssSyncInterval": FEED_CHECK_MINUTES})
    apply_sizes(api, table["sizes"])
    unwanted = ensure_custom_formats(api, table["unwanted"])
    ensure_profile(api, table["profile"], table["tiers"], unwanted)
    fail_dangerous_downloads(api)
    return waiting


def ensure_download_client(api: Servarr, client: tuple[str, int], category_field: str, category: str) -> bool:
    """qBittorrent, on its own network, as the app's download client; True when it was added or changed.

    Finished torrents are removed by the app once qBittorrent has stopped them (no sharing).
    """
    before = next((item for item in api.get("downloadclient") or [] if item.get("name") == DOWNLOAD_CLIENT), None)
    after = ensure_provider(
        api,
        "downloadclient",
        DOWNLOAD_CLIENT,
        "QBittorrent",
        {"host": client[0], "port": client[1], "useSsl": False, category_field: category, "initialState": 0},
        {"enable": True, "priority": 1, "removeCompletedDownloads": True, "removeFailedDownloads": True},
    )
    return before != after
