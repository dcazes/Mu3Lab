"""Jellyfin is set up, and its people are created, through its own API.

Nobody sees Jellyfin's setup screens. Mu3Lab completes the wizard with its own
hidden administrator (``mu3lab-service``, a generated password), then:

- adds the Movies and Shows libraries (and Music, read-only, when Navidrome is
  installed);
- keeps Jellyfin to the home network: remote access off, and the only "local"
  networks are the home network and Docker's (Mu3Lab's own checks and the chat
  connector arrive from Docker's addresses);
- converts video on the NVIDIA card in the NVIDIA build.

Every household member gets a Jellyfin account with a generated password saved
to their vault; Mu3Lab administrators are Jellyfin administrators. A
deactivated person's account is disabled and their TVs and phones signed out,
keeping their watch history for when they come back.

Chat signs in to Jellyfin as each person (a "Mu3Lab chat" device), so their
library access and parental limits apply; no administrator key is shared.
"""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

from ctl.docker_config import POOL
from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError
from ctl.runtime import RuntimePaths
from ctl.store import onboarding as onboarding_state
from ctl.store import person_logins

if TYPE_CHECKING:
    from ctl.manifest.catalog import App

SERVICE = "mu3lab-service"
PASSWORD_ENV = "JELLYFIN_ADMIN_PASSWORD"
READY_SECONDS = 300
CONTROL_DEVICE = "mu3lab-control"
# Docker's default ranges besides Mu3Lab's own pool: older networks may still use them.
DOCKER_NETWORKS = (POOL["base"], "172.16.0.0/12")
LIBRARIES = (
    ("Movies", "movies", "/media/movies"),
    ("Shows", "tvshows", "/media/shows"),
)
MUSIC = ("Music", "music", "/media/music")
# What the RTX-class cards decode; Jellyfin skips any the card lacks.
NVDEC_CODECS = ["h264", "hevc", "mpeg2video", "vc1", "vp8", "vp9", "av1"]


def _client(device: str, token: str = "") -> str:
    value = f'MediaBrowser Client="Mu3Lab", Device="Mu3Lab", DeviceId="{device}", Version="1.0"'
    return value + (f', Token="{token}"' if token else "")


def _call(
    port: int,
    path: str,
    *,
    method: str = "GET",
    data: Any = None,
    token: str = "",
    device: str = CONTROL_DEVICE,
) -> Any:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": _client(device, token),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise LoopbackError(f"Jellyfin refused {method} {path.split('?')[0]} (HTTP {exc.code}).") from None
    except OSError as exc:
        raise LoopbackError(f"Jellyfin did not answer {path.split('?')[0]}.") from exc
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return raw.decode(errors="replace")


def _sign_in(port: int, username: str, password: str, device: str = CONTROL_DEVICE) -> dict[str, Any]:
    body = _call(
        port, "/Users/AuthenticateByName", method="POST", data={"Username": username, "Pw": password}, device=device
    )
    if not isinstance(body, dict) or not body.get("AccessToken"):
        raise LoopbackError(f"Jellyfin did not sign {username} in.")
    return body


def _admin(port: int, env: dict[str, str]) -> str:
    return str(_sign_in(port, SERVICE, env[PASSWORD_ENV])["AccessToken"])


def _wait(port: int, ctx: HookContext) -> None:
    deadline = time.monotonic() + READY_SECONDS
    while True:
        try:
            if _call(port, "/health") == "Healthy":
                return
        except LoopbackError:
            pass
        if time.monotonic() > deadline:
            ctx.fail("account_bootstrap", "account_provisioning_failed", "Jellyfin did not start.")
        time.sleep(3)


def _local_networks(env: dict[str, str]) -> list[str]:
    home = env.get("MU3LAB_HOME_NETWORK", "")
    return [network for network in (home, *DOCKER_NETWORKS) if network]


def _configure(port: int, token: str, env: dict[str, str]) -> None:
    """Settings Mu3Lab owns, put back at every start; the rest stays the household's to change."""
    network = _call(port, "/System/Configuration/network", token=token)
    network |= {
        "EnableRemoteAccess": False,
        "LocalNetworkSubnets": _local_networks(env),
        "AutoDiscovery": False,
        "EnableUPnP": False,
        "EnableHttps": False,
        "RequireHttps": False,
        "KnownProxies": [],
        "RemoteIPFilter": [],
        "IsRemoteIPFilterBlacklist": False,
    }
    _call(port, "/System/Configuration/network", method="POST", data=network, token=token)

    encoding = _call(port, "/System/Configuration/encoding", token=token)
    if env.get("JELLYFIN_VIDEO_ACCEL") == "nvenc":
        encoding |= {
            "HardwareAccelerationType": "nvenc",
            "EnableHardwareEncoding": True,
            "HardwareDecodingCodecs": NVDEC_CODECS,
            "EnableDecodingColorDepth10Hevc": True,
            "EnableDecodingColorDepth10Vp9": True,
            "EnableTonemapping": True,
        }
    else:
        encoding |= {"HardwareAccelerationType": "none", "EnableTonemapping": False}
    _call(port, "/System/Configuration/encoding", method="POST", data=encoding, token=token)

    wanted = [*LIBRARIES, *([MUSIC] if env.get("MU3LAB_MUSIC_SHARED") == "1" else [])]
    existing = _call(port, "/Library/VirtualFolders", token=token) or []
    have = {str(folder.get("Name") or "").casefold() for folder in existing if isinstance(folder, dict)}
    for name, kind, path in wanted:
        if name.casefold() in have:
            continue
        query = urllib.parse.urlencode({"name": name, "collectionType": kind, "refreshLibrary": "true"})
        options = {"LibraryOptions": {"PathInfos": [{"Path": path}], "SaveLocalMetadata": False}}
        _call(port, f"/Library/VirtualFolders?{query}", method="POST", data=options, token=token)


def _policy(port: int, token: str, user: dict[str, Any], **changes: Any) -> None:
    policy = dict(user.get("Policy") or {})
    wanted = {"EnableRemoteAccess": False, **changes}
    if all(policy.get(key) == value for key, value in wanted.items()):
        return
    _call(port, f"/Users/{user['Id']}/Policy", method="POST", data=policy | wanted, token=token)


def _set_password(port: int, token: str, user_id: str, password: str) -> None:
    query = urllib.parse.urlencode({"userId": user_id})
    _call(
        port,
        f"/Users/Password?{query}",
        method="POST",
        data={"NewPw": password, "ResetPassword": False},
        token=token,
    )


def _sign_out(port: int, token: str, user_id: str) -> None:
    """Sign out every TV, phone and browser of one person (and their chat sign-in)."""
    found = _call(port, "/Devices?" + urllib.parse.urlencode({"userId": user_id}), token=token) or {}
    for device in found.get("Items") or []:
        if isinstance(device, dict) and device.get("Id"):
            _call(port, "/Devices?" + urllib.parse.urlencode({"id": device["Id"]}), method="DELETE", token=token)


def _chat_token(port: int, app: App, username: str, paths: RuntimePaths) -> dict[str, str]:
    login = person_logins.get(app.id, username, paths)
    if not login:
        raise RuntimeError(f"{username} has no Jellyfin account yet; Mu3Lab creates it shortly.")
    body = _sign_in(port, str(login["login"]), str(login["password"]), device=f"mu3lab-chat-{username}")
    return {"token": str(body["AccessToken"])}


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        port = ctx.app.manifest.service.local_port
        env = ctx.env()
        _wait(port, ctx)
        try:
            info = _call(port, "/System/Info/Public") or {}
            if not info.get("StartupWizardCompleted"):
                country = env.get("MU3LAB_COUNTRY") or "US"
                _call(
                    port,
                    "/Startup/Configuration",
                    method="POST",
                    data={"UICulture": "en-US", "MetadataCountryCode": country, "PreferredMetadataLanguage": "en"},
                )
                _call(port, "/Startup/User")  # Jellyfin prepares its first user here
                _call(port, "/Startup/User", method="POST", data={"Name": SERVICE, "Password": env[PASSWORD_ENV]})
                _call(port, "/Startup/RemoteAccess", method="POST", data={"EnableRemoteAccess": False})
                _call(port, "/Startup/Complete", method="POST")
                done = "Jellyfin is set up"
            else:
                done = "Jellyfin's existing setup was kept"
            token = _admin(port, env)
            service = next(
                (user for user in _call(port, "/Users", token=token) or [] if str(user.get("Name")) == SERVICE), None
            )
            if service:
                _policy(port, token, service)  # home network only, like everyone else
            settings = _call(port, "/System/Configuration", token=token)
            settings |= {"ServerName": "Mu3Lab", "QuickConnectAvailable": True}
            _call(port, "/System/Configuration", method="POST", data=settings, token=token)
            _configure(port, token, env)
        except LoopbackError as exc:
            ctx.fail("account_bootstrap", "account_provisioning_failed", f"Jellyfin could not be set up: {exc}")
        return f"{done}; Mu3Lab creates each person's account next."

    def after_healthy(self, ctx: HookContext) -> None:
        """After a restart (a new home address, Navidrome arriving): put Mu3Lab's settings back."""
        port = ctx.app.manifest.service.local_port
        env = ctx.env()
        _wait(port, ctx)
        try:
            _configure(port, _admin(port, env), env)
        except LoopbackError as exc:
            ctx.fail("configure_application", "application_configuration_failed", str(exc))

    def sync_people(self, ctx: HookContext, people: list[dict[str, Any]]) -> None:
        port = ctx.app.manifest.service.local_port
        paths = ctx.facts.paths if ctx.facts else None
        token = _admin(port, ctx.env())
        users = {str(user["Name"]).casefold(): user for user in _call(port, "/Users", token=token) or []}
        for person in people:
            username = person["username"]
            if username.casefold() == SERVICE:
                continue
            user = users.get(username.casefold())
            login = person_logins.get(ctx.app.id, username, paths)
            if not person["member"]:
                if user and not (user.get("Policy") or {}).get("IsDisabled"):
                    _policy(port, token, user, IsDisabled=True)
                    _sign_out(port, token, str(user["Id"]))
                person_logins.forget(ctx.app.id, username, paths)
                continue
            password = login["password"] if login else secrets.token_urlsafe(24)
            if user is None:
                user = _call(
                    port, "/Users/New", method="POST", data={"Name": username, "Password": password}, token=token
                )
            elif login is None:
                # Back after leaving, or made by hand: a fresh password, saved to their vault.
                _set_password(port, token, str(user["Id"]), password)
            _policy(port, token, user, IsAdministrator=person["admin"], IsDisabled=False)
            if login is None:
                person_logins.put(ctx.app.id, username, username, password, paths)

    def connector_credentials(self, app: App, connector_id: str, paths: RuntimePaths) -> dict[str, str]:
        """The installing owner's own chat sign-in (everyone else gets theirs per person)."""
        owner = (onboarding_state.read(app.id, paths).get("owner") or {}).get("username")
        if not owner:
            raise RuntimeError("Jellyfin's owner is not known yet; reinstall Jellyfin to record it.")
        return _chat_token(app.manifest.service.local_port, app, str(owner), paths)

    def person_connector_credentials(
        self, app: App, connector_id: str, person: dict[str, str], paths: RuntimePaths
    ) -> dict[str, str]:
        return _chat_token(app.manifest.service.local_port, app, person["username"], paths)
