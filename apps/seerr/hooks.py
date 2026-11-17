"""Seerr is set up, and its people added, through its own API; nobody sees its setup screens.

Install: Seerr's owner becomes Jellyfin's hidden Mu3Lab administrator (``mu3lab-service``),
signed in once with the password Jellyfin's settings hold (``integrates_with``). Seerr then
makes its own Jellyfin key. Mu3Lab keeps Seerr's key in its settings for what follows.

At every start, and every few minutes after, Mu3Lab puts back what it owns:

- sign-in with Jellyfin logins only (no Seerr passwords), and only for people Mu3Lab added;
- Jellyfin's Movies and Shows libraries, and the addresses Seerr links to;
- Radarr for films and Sonarr for shows while they are installed, with their "Mu3Lab" quality
  profile and library folders. Films wait until released, so nothing is fetched from a cinema.

People: every household member is added from their Jellyfin account and may request and report
problems; Mu3Lab administrators' requests are approved at once, and they approve everyone
else's. Their Jellyfin login, which Mu3Lab made, is saved to their vault for Seerr's address
too. A deactivated person can no longer request (their Jellyfin account is closed as well).
"""

from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError
from ctl.engine.project import provider
from ctl.store import person_logins

if TYPE_CHECKING:
    from ctl.manifest.catalog import App

KEY_ENV = "SEERR_API_KEY"
JELLYFIN_PASSWORD_ENV = "MU3LAB_JELLYFIN_ADMIN_PASSWORD"
SERVICE = "mu3lab-service"  # Jellyfin's hidden administrator (apps/jellyfin/hooks.py)
READY_SECONDS = 300
JELLYFIN = {"hostname": "jellyfin", "port": 8096}
SERVER_TYPE_JELLYFIN = 2

# Seerr's permission bits (server/lib/permissions.ts).
MANAGE_REQUESTS, REQUEST, AUTO_APPROVE, REQUEST_ADVANCED, REQUEST_VIEW = 16, 32, 128, 8192, 16384
MANAGE_ISSUES, VIEW_ISSUES, CREATE_ISSUES = 1048576, 2097152, 4194304
HOUSEHOLD = REQUEST | VIEW_ISSUES | CREATE_ISSUES
ADMINISTRATOR = HOUSEHOLD | MANAGE_REQUESTS | AUTO_APPROVE | REQUEST_VIEW | REQUEST_ADVANCED | MANAGE_ISSUES
NONE = 0

# (key the integration provides, Seerr's word for it, name in Seerr, address, library folder, extra settings)
SERVERS: tuple[tuple[str, str, str, dict[str, Any], str, dict[str, Any]], ...] = (
    (
        "MU3LAB_RADARR_API_KEY",
        "radarr",
        "Radarr (Mu3Lab)",
        {"hostname": "radarr", "port": 7878},
        "/media/movies",
        {"minimumAvailability": "released"},
    ),
    (
        "MU3LAB_SONARR_API_KEY",
        "sonarr",
        "Sonarr (Mu3Lab)",
        {"hostname": "sonarr", "port": 8989},
        "/media/shows",
        {"enableSeasonFolders": True},
    ),
)
PROFILE = "Mu3Lab"


class Seerr:
    """Seerr's API on this computer's loopback port, with its key or a sign-in session."""

    def __init__(self, port: int, key: str = "") -> None:
        self.port, self.key = port, key
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, data: Any = None) -> Any:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.key:
            headers["X-Api-Key"] = self.key
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/v1/{path.lstrip('/')}",
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise LoopbackError(f"Seerr refused {method} {path.split('?')[0]} (HTTP {exc.code}).") from None
        except OSError as exc:
            raise LoopbackError(f"Seerr did not answer {path.split('?')[0]}.") from exc
        return json.loads(raw) if raw.strip() else None

    def get(self, path: str) -> Any:
        return self.call("GET", path)

    def post(self, path: str, data: Any = None) -> Any:
        return self.call("POST", path, {} if data is None else data)

    def put(self, path: str, data: Any) -> Any:
        return self.call("PUT", path, data)

    def delete(self, path: str) -> Any:
        return self.call("DELETE", path)


def _wait(api: Seerr) -> None:
    deadline = time.monotonic() + READY_SECONDS
    while True:
        try:
            api.get("status")
            return
        except LoopbackError:
            if time.monotonic() > deadline:
                raise
        time.sleep(3)


def owner_key(port: int, env: dict[str, str]) -> str:
    """Sign Seerr's owner in with Jellyfin (making the owner on a new Seerr) and return Seerr's key."""
    password = env.get(JELLYFIN_PASSWORD_ENV, "")
    if not password:
        raise LoopbackError("Jellyfin's Mu3Lab administrator is not known yet; Jellyfin must be installed first.")
    session = Seerr(port)
    login = {"username": SERVICE, "password": password}
    if not session.get("settings/public").get("initialized"):
        # Seerr's first sign-in names its Jellyfin server and makes the owner.
        login |= {**JELLYFIN, "useSsl": False, "urlBase": "", "email": f"{SERVICE}@mu3lab.invalid"}
        login["serverType"] = SERVER_TYPE_JELLYFIN
    session.post("auth/jellyfin", login)
    key = str(session.get("settings/main").get("apiKey") or "")
    if not key:
        raise LoopbackError("Seerr did not give its key.")
    return key


def _addresses(ctx: HookContext) -> tuple[str, str]:
    """Seerr's own home address and Jellyfin's, for the links Seerr shows."""
    env = ctx.env()
    home = env.get("MU3LAB_HOME_IP", "")
    seerr_port = ctx.app.manifest.service.home_port
    jellyfin = provider("videos", ctx.facts) if ctx.facts else None
    jellyfin_port = jellyfin.manifest.service.home_port if jellyfin else None
    if not home:
        return "", ""
    return (
        f"http://{home}:{seerr_port}" if seerr_port else "",
        f"http://{home}:{jellyfin_port}" if jellyfin_port else "",
    )


def configure(api: Seerr, env: dict[str, str], own_url: str = "", jellyfin_url: str = "") -> list[str]:
    """Put back what Mu3Lab owns in Seerr; returns what changed, in words."""
    changed: list[str] = []
    main = api.get("settings/main")
    wanted = {
        "localLogin": False,
        "mediaServerLogin": True,
        "newPlexLogin": False,
        "defaultPermissions": HOUSEHOLD,
        **({"applicationUrl": own_url} if own_url else {}),
    }
    if any(main.get(key) != value for key, value in wanted.items()):
        api.post("settings/main", wanted)
    jellyfin = api.get("settings/jellyfin")
    if jellyfin_url and jellyfin.get("externalHostname") != jellyfin_url:
        api.post("settings/jellyfin", {"externalHostname": jellyfin_url})  # only what changes
    libraries = api.post("settings/jellyfin/library/sync") or []
    for library in libraries:
        if not library.get("enabled"):
            api.put(f"settings/jellyfin/library/{library['id']}", {"enabled": True})
    for key_env, kind, name, address, folder, extra in SERVERS:
        key = env.get(key_env, "")
        existing = [server for server in api.get(f"settings/{kind}") or [] if server.get("name") == name]
        if not key:
            for server in existing:
                api.delete(f"settings/{kind}/{server['id']}")
                changed.append(f"{kind} disconnected")
            continue
        found = api.post(f"settings/{kind}/test", {**address, "apiKey": key, "useSsl": False, "baseUrl": ""})
        profile = next((item for item in found.get("profiles") or [] if item.get("name") == PROFILE), None)
        if profile is None:
            raise LoopbackError(f"{kind} has no {PROFILE} quality profile yet; it is made when {kind} starts.")
        settings = {
            "name": name,
            **address,
            "apiKey": key,
            "useSsl": False,
            "baseUrl": "",
            "activeProfileId": profile["id"],
            "activeProfileName": PROFILE,
            "activeDirectory": folder,
            "is4k": False,
            "isDefault": True,
            "syncEnabled": True,
            "preventSearch": False,
            **extra,
        }
        if kind == "sonarr":
            languages = found.get("languageProfiles") or []
            settings |= {
                "activeAnimeProfileId": profile["id"],
                "activeAnimeProfileName": PROFILE,
                "activeAnimeDirectory": folder,
                **({"activeLanguageProfileId": languages[0]["id"]} if languages else {}),
            }
        if not existing:
            api.post(f"settings/{kind}", settings)
            changed.append(f"{kind} connected")
        elif any(existing[0].get(field) != value for field, value in settings.items()):
            api.put(f"settings/{kind}/{existing[0]['id']}", {**existing[0], **settings})
    if not api.get("settings/public").get("initialized"):
        api.post("settings/initialize")
    return changed


def _jellyfin_app(ctx: HookContext) -> App:
    found = provider("videos", ctx.facts) if ctx.facts else None
    if found is None:
        raise RuntimeError("Jellyfin is not installed; Seerr signs people in with their Jellyfin login.")
    return found


def copy_logins(ctx: HookContext, people: list[dict[str, Any]]) -> int:
    """Each member's Jellyfin login, saved again for Seerr's address; returns how many changed.

    Raises while a member has no Jellyfin login yet, so the household sync runs again.
    """
    paths = ctx.facts.paths if ctx.facts else None
    jellyfin = _jellyfin_app(ctx)
    changed = 0
    for person in people:
        username = person["username"]
        if not person["member"]:
            person_logins.forget(ctx.app.id, username, paths)
            continue
        login = person_logins.get(jellyfin.id, username, paths)
        if not login:
            raise RuntimeError(f"{username} has no Jellyfin login yet; Mu3Lab creates it shortly.")
        mine = person_logins.get(ctx.app.id, username, paths)
        if not mine or (mine["login"], mine["password"]) != (login["login"], login["password"]):
            person_logins.put(ctx.app.id, username, str(login["login"]), str(login["password"]), paths)
            changed += 1
    return changed


def _api(ctx: HookContext) -> Seerr:
    key = ctx.env().get(KEY_ENV, "")
    if not key:
        raise LoopbackError("Seerr's key is not known yet; it is kept when Seerr is set up.")
    return Seerr(ctx.app.manifest.service.local_port, key)


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        port = ctx.app.manifest.service.local_port
        try:
            _wait(Seerr(port))
            ctx.set_env({KEY_ENV: owner_key(port, ctx.env())})
        except LoopbackError as exc:
            ctx.fail("account_bootstrap", "account_provisioning_failed", f"Seerr could not be set up: {exc}")
        return "Seerr is set up with Jellyfin; Mu3Lab adds each person next."

    def after_healthy(self, ctx: HookContext) -> None:
        ctx.stage("configure_application", "Connect Seerr to Jellyfin, Radarr and Sonarr.")
        try:
            api = _api(ctx)
            _wait(api)
            configure(api, ctx.env(), *_addresses(ctx))
        except LoopbackError as exc:
            ctx.fail("configure_application", "application_configuration_failed", f"Seerr could not be set up: {exc}")

    def periodic(self, ctx: HookContext) -> str:
        changed = configure(_api(ctx), ctx.env(), *_addresses(ctx))
        return f"Seerr: {', '.join(changed)}." if changed else ""

    def sync_people(self, ctx: HookContext, people: list[dict[str, Any]]) -> None:
        api = _api(ctx)
        copy_logins(ctx, people)
        accounts = {
            str(user["username"]).casefold(): str(user["id"]) for user in api.get("settings/jellyfin/users") or []
        }
        users = {
            str(user.get("jellyfinUsername") or "").casefold(): user
            for user in (api.get("user?take=1000") or {}).get("results") or []
        }
        for person in people:
            name = person["username"].casefold()
            if name == SERVICE:
                continue
            user = users.get(name)
            if not person["member"]:
                if user and user.get("permissions") != NONE:
                    api.post(f"user/{user['id']}/settings/permissions", {"permissions": NONE})
                continue
            if user is None:
                if name not in accounts:
                    raise RuntimeError(f"{person['username']} has no Jellyfin account yet; Mu3Lab creates it shortly.")
                made = api.post("user/import-from-jellyfin", {"jellyfinUserIds": [accounts[name]]}) or []
                user = made[0] if made else None
                if user is None:
                    raise RuntimeError(f"Seerr did not add {person['username']}.")
            wanted = ADMINISTRATOR if person["admin"] else HOUSEHOLD
            if user.get("permissions") != wanted:
                api.post(f"user/{user['id']}/settings/permissions", {"permissions": wanted})
