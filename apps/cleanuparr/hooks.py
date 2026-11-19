"""Cleanuparr is set up through its own API; nobody sees its setup screens.

At install Mu3Lab makes Cleanuparr's administrator login (a generated password nobody needs)
and finishes its first-run setup. At every start it puts back what it owns:

- No login of its own for local addresses: only Caddy, behind Authentik's gate, and Mu3Lab's
  setup reach its loopback port. No update checks (updates come with Mu3Lab) and no banner.
- Sonarr and Radarr while they are installed (their keys arrive through ``integrates_with``),
  and qBittorrent on its own network, where qBittorrent skips its login.
- The owner's rules (2026-11-18): every 15 minutes it checks Sonarr's and Radarr's downloads;
  one with no progress for a day (96 checks) is removed, that copy blocked in the app and
  another searched for; a magnet stuck fetching its details for an hour (4 checks) likewise.
  Every half hour it searches for one monitored film or episode that has no file and is out
  (Radarr's own "released" rule), Sonarr and Radarr in turn. Upgrades are not searched for.
  Torrents added by hand, which no app tracks, are left alone.

Everything else (more rules, notifications) is the administrators' to change in Cleanuparr.
Every few minutes it also connects a Sonarr or Radarr installed later, or drops one removed.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError

PASSWORD_ENV = "CLEANUPARR_PASSWORD"
USERNAME = "mu3lab"
READY_SECONDS = 300
# qBittorrent's name and port on its own network, which this app joins (docker-compose.yml).
QBITTORRENT = {"name": "qBittorrent (Mu3Lab)", "typeName": "qBittorrent", "host": "http://qbittorrent:8479"}
# kind (Cleanuparr's word), key from integrates_with, instance name, address on mu3lab_media, major version.
APPS = (
    ("sonarr", "MU3LAB_SONARR_API_KEY", "Sonarr (Mu3Lab)", "http://sonarr:8989", 4),
    ("radarr", "MU3LAB_RADARR_API_KEY", "Radarr (Mu3Lab)", "http://radarr:7878", 5),
)
CHECK_MINUTES = 15
STALL_RULE = {
    "name": "Mu3Lab: no progress for a day",
    "enabled": True,
    "maxStrikes": 24 * 60 // CHECK_MINUTES,
    "privacyType": "Both",
    "contentType": "All",
    "minCompletionPercentage": 0,
    "maxCompletionPercentage": 100,
    "deletePrivateTorrentsFromClient": False,
    "changeCategory": False,
    "resetStrikesOnProgress": True,
    "minimumProgress": None,
}
GENERAL = {
    "auth": {"disableAuthForLocalAddresses": True, "trustForwardedHeaders": False, "trustedNetworks": []},
    "statusCheckEnabled": False,
    "displaySupportBanner": False,
}
QUEUE_CLEANER = {
    "enabled": True,
    "useAdvancedScheduling": True,
    "cronExpression": f"0 0/{CHECK_MINUTES} * * * ?",
    "downloadingMetadataMaxStrikes": 60 // CHECK_MINUTES,
}
SEEKER = {
    "searchEnabled": True,
    "searchInterval": 30,
    "proactiveSearchEnabled": True,
    "useRoundRobin": True,
}
SEEKER_INSTANCE = {
    "enabled": True,
    "monitoredOnly": True,
    "useCutoff": False,
    "useCustomFormatScore": False,
    "radarrReleaseGate": "ArrAvailability",
}


def _reason(exc: urllib.error.HTTPError) -> str:
    """Cleanuparr's own explanation, from its problem reply; never a credential."""
    try:
        body = json.loads(exc.read(64 * 1024) or b"{}")
    except ValueError:
        return ""
    detail = body.get("detail") or body.get("title") or body.get("error") or ""
    errors = body.get("errors")
    if isinstance(errors, dict):
        detail = "; ".join(str(item) for values in errors.values() for item in (values or [])) or detail
    return f": {str(detail)[:200]}" if detail else ""


class Cleanuparr:
    """Cleanuparr's API on this computer's loopback port, signed in as Mu3Lab's administrator."""

    def __init__(self, port: int) -> None:
        self.port, self.token = port, ""

    def call(self, method: str, path: str, data: Any = None) -> Any:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/{path.lstrip('/')}",
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise LoopbackError(f"Cleanuparr refused {method} {path} (HTTP {exc.code}){_reason(exc)}.") from None
        except OSError as exc:
            raise LoopbackError(f"Cleanuparr did not answer {path}.") from exc
        return json.loads(raw) if raw.strip() else None

    def get(self, path: str) -> Any:
        return self.call("GET", path)

    def post(self, path: str, data: Any = None) -> Any:
        return self.call("POST", path, {} if data is None else data)

    def put(self, path: str, data: Any) -> Any:
        return self.call("PUT", path, data)

    def delete(self, path: str) -> Any:
        return self.call("DELETE", path)

    def wait(self) -> dict[str, Any]:
        deadline = time.monotonic() + READY_SECONDS
        while True:
            try:
                return self.get("auth/status")
            except LoopbackError:
                if time.monotonic() > deadline:
                    raise
            time.sleep(3)

    def sign_in(self, password: str) -> None:
        """Finish first-run setup with Mu3Lab's login if nobody has, then sign in with it."""
        if not self.wait().get("setupCompleted"):
            try:
                self.post("auth/setup/account", {"username": USERNAME, "password": password})
            except LoopbackError as exc:
                if "HTTP 409" not in str(exc):  # made by an earlier, interrupted setup
                    raise
            self.post("auth/setup/complete")
        reply = self.post("auth/login", {"username": USERNAME, "password": password})
        token = ((reply or {}).get("tokens") or {}).get("accessToken")
        if not token:
            raise LoopbackError("Cleanuparr did not accept Mu3Lab's administrator login.")
        self.token = str(token)


def _merged(current: dict[str, Any], wanted: dict[str, Any]) -> dict[str, Any]:
    """``current`` with ``wanted`` laid over it, nested settings merged rather than replaced."""
    merged = dict(current)
    for key, value in wanted.items():
        merged[key] = _merged(current.get(key) or {}, value) if isinstance(value, dict) else value
    return merged


def _ensure_settings(api: Cleanuparr, path: str, wanted: dict[str, Any]) -> bool:
    current = api.get(path) or {}
    merged = _merged(current, wanted)
    if merged == current:
        return False
    api.put(path, merged)
    return True


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _differs(current: Any, wanted: Any) -> bool:
    """Whether a stored value differs from Mu3Lab's; Cleanuparr adds a "/" to the addresses it stores."""
    if isinstance(current, str) and isinstance(wanted, str) and wanted.startswith("http"):
        return current.rstrip("/") != wanted.rstrip("/")
    return current != wanted


def connect(
    api: Cleanuparr, env: dict[str, str], remember: Callable[[dict[str, str]], Any] = lambda values: None
) -> tuple[list[str], dict[str, str]]:
    """Sonarr, Radarr and qBittorrent; returns what changed, in words, and each app's instance id.

    Cleanuparr never shows a key it holds, so a digest of the key it was last given is kept in
    its settings (``remember``): a reinstalled Sonarr or Radarr with a new key is sent it again.
    """
    changed: list[str] = []
    ids: dict[str, str] = {}
    for kind, key_env, name, url, version in APPS:
        key = env.get(key_env, "")
        instances = (api.get(f"configuration/{kind}") or {}).get("instances") or []
        found = next((item for item in instances if item.get("name") == name), None)
        if not key:
            if found:
                api.delete(f"configuration/{kind}/instances/{found['id']}")
                changed.append(f"{name.split()[0]} disconnected")
            continue
        wanted = {"enabled": True, "name": name, "url": url, "apiKey": key, "version": version}
        sent = key_env + "_SENT"
        if found is None:
            found = api.post(f"configuration/{kind}/instances", wanted)
            changed.append(f"{name.split()[0]} connected")
        elif env.get(sent) != _digest(key) or any(
            _differs(found.get(field), value) for field, value in wanted.items() if field != "apiKey"
        ):
            api.put(f"configuration/{kind}/instances/{found['id']}", wanted)
        if env.get(sent) != _digest(key):
            remember({sent: _digest(key)})
        ids[kind] = str(found["id"])
    clients = (api.get("configuration/download_client") or {}).get("clients") or []
    client = next((item for item in clients if item.get("name") == QBITTORRENT["name"]), None)
    wanted_client = {**QBITTORRENT, "enabled": True}
    if client is None:
        api.post("configuration/download_client", wanted_client)
        changed.append("qBittorrent connected")
    elif any(_differs(client.get(field), value) for field, value in wanted_client.items()):
        api.put(f"configuration/download_client/{client['id']}", {**client, **wanted_client})
    return changed, ids


def _ensure_seeker(api: Cleanuparr, ids: dict[str, str], *, rules: bool) -> None:
    """Seeker searches each connected app; ``rules`` also puts back Mu3Lab's search settings."""
    current = api.get("configuration/seeker") or {}
    instances = list(current.get("instances") or [])
    wanted_ids = set(ids.values())
    changed = False
    for instance in instances:
        if str(instance.get("arrInstanceId")) in wanted_ids and (
            rules and any(instance.get(field) != value for field, value in SEEKER_INSTANCE.items())
        ):
            instance.update(SEEKER_INSTANCE)
            changed = True
    for arr_id in wanted_ids - {str(item.get("arrInstanceId")) for item in instances}:
        instances.append({"arrInstanceId": arr_id, **SEEKER_INSTANCE})
        changed = True
    merged = _merged(current, SEEKER) if rules else dict(current)
    if changed or merged != current:
        api.put("configuration/seeker", {**merged, "instances": instances})


def _ensure_stall_rule(api: Cleanuparr) -> None:
    rules = api.get("queue-rules/stall") or []
    found = next((rule for rule in rules if rule.get("name") == STALL_RULE["name"]), None)
    if found is None:
        api.post("queue-rules/stall", STALL_RULE)
    elif any(found.get(field) != value for field, value in STALL_RULE.items()):
        api.put(f"queue-rules/stall/{found['id']}", {**found, **STALL_RULE})


def configure(
    api: Cleanuparr, env: dict[str, str], remember: Callable[[dict[str, str]], Any] = lambda values: None
) -> list[str]:
    """Everything Mu3Lab owns, put back at every start; returns what changed, in words."""
    _ensure_settings(api, "configuration/general", GENERAL)
    changed, ids = connect(api, env, remember)
    _ensure_stall_rule(api)
    _ensure_settings(api, "configuration/queue_cleaner", QUEUE_CLEANER)
    _ensure_seeker(api, ids, rules=True)
    return changed


def _api(ctx: HookContext) -> Cleanuparr:
    api = Cleanuparr(ctx.app.manifest.service.local_port)
    api.sign_in(ctx.env()[PASSWORD_ENV])
    return api


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        ctx.stage("configure_application", "Connect Cleanuparr to Sonarr, Radarr and qBittorrent and set its rules.")
        try:
            for note in configure(_api(ctx), ctx.env(), ctx.set_env):
                ctx.log(f"Cleanuparr: {note}")
        except LoopbackError as exc:
            ctx.fail(
                "configure_application", "application_configuration_failed", f"Cleanuparr could not be set up: {exc}"
            )

    def periodic(self, ctx: HookContext) -> str:
        api = _api(ctx)
        changed, ids = connect(api, ctx.env(), ctx.set_env)
        _ensure_seeker(api, ids, rules=False)
        return f"Cleanuparr: {', '.join(changed)}." if changed else ""
