"""Set up Sonarr, Radarr and Prowlarr through their own APIs (the "Servarr" family).

The three apps share one API style: JSON over HTTP with an ``X-Api-Key`` header, settings
in ``/config/<section>``, and "providers" (download clients, indexers, applications) made
of an implementation name and a list of ``{name, value}`` fields. Each app's ``hooks.py``
says what Mu3Lab wants (folders, names, which apps to connect) and
calls these helpers, which change only what differs, so running them again at every start
or every few minutes changes nothing.

Nothing here names an app: the hooks pass in addresses and keys. Quality rules are
Recyclarr's (``apps/<id>/recyclarr.yml``), run by the hooks.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ctl.control_state import ControlState
from ctl.engine.compose import Compose
from ctl.engine.loopback import LoopbackError
from ctl.runtime import RuntimePaths

# Sonarr and Radarr call these "Executables" and "Potentially Dangerous": a download holding
# such a file is marked failed, blocked and searched for again (indexer setting failDownloads).
FAIL_DANGEROUS = [0, 1]
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
        # Saved even while the other end is away (Sonarr still starting). A download client is the
        # exception: Sonarr and Radarr still test it and refuse one they cannot reach.
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
) -> list[str]:
    """Everything Mu3Lab owns in Sonarr or Radarr, put back at every start; returns what must wait.

    - The library folder (Shows or Movies) and qBittorrent (``client``: its name and port on
      its own network) as the download client, in the app's own category. qBittorrent stops each torrent the moment it finishes (no sharing); the app
      then moves the file into the library and removes the torrent.
    - Names Jellyfin reads, with subtitles kept beside the video.
    - Slow feed checks, and a download holding a program fails so another release is tried.

    The app will not add qBittorrent while qBittorrent is stopped; that one waits for the
    app's upkeep (``ensure_download_client``), and the rest is done now.
    """
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
    fail_dangerous_downloads(api)
    return waiting


def apply_quality_rules(compose: Compose, name: str, log: Any) -> None:
    """Run Recyclarr, the service beside the app, once: sizes, unwanted formats and the "Mu3Lab" profile.

    Its rules are the app's ``recyclarr.yml``. It also runs weekly on its own schedule; this run
    makes a new install or a changed rule take effect at once. Raises ``ValueError`` with its error.
    """
    rc, output = compose.exec("recyclarr", ["recyclarr", "sync"], log, timeout=900)
    errors = [line.strip() for line in output.splitlines() if "[ERR]" in line or "[FTL]" in line]
    if rc or errors:
        lines = errors or [line for line in output.splitlines() if line.strip()] or ["it stopped without a message"]
        reason = lines[-1]
        raise ValueError(f"Recyclarr could not apply {name}'s quality rules: {reason}")


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


def keep_download_client(
    api: Servarr,
    client: tuple[str, int],
    category_field: str,
    category: str,
    *,
    client_app: str,
    paths: RuntimePaths,
    log: Any,
) -> bool:
    """The upkeep pass of ``ensure_download_client``; True when qBittorrent was added or changed.

    While qBittorrent (the app ``client_app``) is stopped the app refuses it, and that waits quietly
    for the next pass. A refusal while it runs is a real fault (nothing would download), so it goes
    to the log.
    """
    try:
        return ensure_download_client(api, client, category_field, category)
    except LoopbackError as exc:
        state = ControlState.runtime(paths)
        installation = state.installation(client_app) if state else None
        if installation and installation["state"] in {"running", "degraded"}:
            log(f"{api.name} cannot use qBittorrent, so nothing downloads: {exc}")
        return False
