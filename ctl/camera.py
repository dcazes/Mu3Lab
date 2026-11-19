"""The front-door camera's live view, for administrators, through go2rtc.

go2rtc (the app with the ``camera_stream`` capability) turns the camera's RTSP stream into
fragments a browser plays. It listens only on this server; the dashboard reaches it through
Mu3Lab (``/api/v1/home/camera/...``), which admits administrators only, so the view works
anywhere on the tailnet without opening the camera to the network.

The camera's address and login are kept in Mu3Lab's secret store, not in go2rtc's files:
whenever someone opens the view, Mu3Lab hands go2rtc the stream addresses (again after a
go2rtc restart). go2rtc connects to the camera only while someone watches and lets go
after; nothing is recorded.

Two streams: the camera's light sub-stream for the widget, and its main stream for HD on tap.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from ctl.manifest.catalog import App
from ctl.platform_apps import by_capability
from ctl.store.secrets import SecretStore

CAPABILITY = "camera_stream"
SCOPE = "home_devices"
NAME = "camera"
STREAMS = {"sd": "front_door", "hd": "front_door_hd"}
# Reolink's usual RTSP paths; some models name them differently (the settings say how to change them).
DEFAULTS: dict[str, Any] = {"port": 554, "sd_path": "h264Preview_01_sub", "hd_path": "h264Preview_01_main", "name": "Front door"}
HOST = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")
PATH = re.compile(r"^[A-Za-z0-9_./\-]{1,120}$")


class CameraError(RuntimeError):
    """The camera or go2rtc could not be reached or set up; the message is for the person."""


def _app() -> App:
    try:
        return by_capability(CAPABILITY)
    except ValueError:
        raise CameraError("The camera app is not in this version of Mu3Lab.") from None


def go2rtc_url() -> str:
    return f"http://127.0.0.1:{_app().manifest.service.local_port}"


def saved(store: SecretStore | None = None) -> dict[str, Any] | None:
    value = (store or SecretStore()).get(SCOPE, NAME)
    return value if isinstance(value, dict) and value.get("host") else None


def public(store: SecretStore | None = None) -> dict[str, Any]:
    """The settings without the password, for the Settings page."""
    value = saved(store)
    if not value:
        return {"ok": True, "configured": False, **DEFAULTS, "host": "", "username": "", "has_password": False}
    return {
        "ok": True,
        "configured": True,
        "name": value.get("name") or DEFAULTS["name"],
        "host": value["host"],
        "port": value.get("port", DEFAULTS["port"]),
        "username": value.get("username", ""),
        "has_password": bool(value.get("password")),
        "sd_path": value.get("sd_path", DEFAULTS["sd_path"]),
        "hd_path": value.get("hd_path", DEFAULTS["hd_path"]),
    }


def save(values: dict[str, Any], store: SecretStore | None = None) -> dict[str, Any]:
    """Keep the camera's settings; a blank password keeps the one already saved."""
    store = store or SecretStore()
    host = str(values.get("host") or "").strip()
    if not HOST.match(host):
        raise CameraError("Enter the camera's address on the home network, like 192.168.1.40.")
    try:
        port = int(str(values.get("port") or DEFAULTS["port"]))
    except (TypeError, ValueError):
        raise CameraError("The RTSP port is a number, usually 554.") from None
    if not 1 <= port <= 65535:
        raise CameraError("The RTSP port is a number, usually 554.")
    paths = {key: str(values.get(key) or DEFAULTS[key]).strip().lstrip("/") for key in ("sd_path", "hd_path")}
    if not all(PATH.match(value) for value in paths.values()):
        raise CameraError("Stream paths may use letters, numbers, _ - . and /.")
    current = saved(store) or {}
    password = str(values.get("password") or "") or str(current.get("password") or "")
    record = {
        "name": str(values.get("name") or DEFAULTS["name"]).strip()[:40] or DEFAULTS["name"],
        "host": host,
        "port": port,
        "username": str(values.get("username") or "").strip()[:80],
        "password": password[:200],
        **paths,
        "saved_at": time.time(),
    }
    store.put(SCOPE, NAME, record)
    return public(store)


def forget(store: SecretStore | None = None) -> None:
    (store or SecretStore()).delete(SCOPE, NAME)
    reset_streams()


def _source(record: dict[str, Any], path_key: str) -> str:
    login = ""
    if record.get("username"):
        login = quote(str(record["username"]), safe="")
        if record.get("password"):
            login += ":" + quote(str(record["password"]), safe="")
        login += "@"
    return f"rtsp://{login}{record['host']}:{record.get('port', 554)}/{record[path_key]}"


def ensure_streams(store: SecretStore | None = None) -> None:
    """Make sure go2rtc knows both streams; it forgets them when it restarts."""
    record = saved(store)
    if not record:
        raise CameraError("No camera is set up yet. An administrator adds it in Settings > Home devices.")
    base = go2rtc_url()
    try:
        known = httpx.get(f"{base}/api/streams", timeout=5).json() or {}
        for quality, stream in STREAMS.items():
            if stream not in known:
                source = _source(record, f"{quality}_path")
                response = httpx.put(f"{base}/api/streams", params={"name": stream, "src": source}, timeout=5)
                response.raise_for_status()
    except (httpx.HTTPError, ValueError):
        raise CameraError("The camera app (go2rtc) is not running. Start it in Apps.") from None


def reset_streams() -> None:
    """Drop go2rtc's copies of the streams, so the next view uses the saved settings (or none)."""
    try:
        for stream in STREAMS.values():
            httpx.delete(f"{go2rtc_url()}/api/streams", params={"src": stream}, timeout=5)
    except (httpx.HTTPError, CameraError):
        pass


def snapshot(quality: str = "sd") -> bytes:
    """One picture from the camera, to check the settings work."""
    ensure_streams()
    try:
        response = httpx.get(
            f"{go2rtc_url()}/api/frame.jpeg", params={"src": STREAMS.get(quality, STREAMS["sd"])}, timeout=20
        )
    except httpx.HTTPError:
        raise CameraError("The camera did not send a picture. Check its address and login.") from None
    if not response.is_success or not response.content:
        raise CameraError(
            "The camera did not send a picture. Check its address, login, and that RTSP is switched on in the "
            "camera's own app (Reolink: Settings > Network > Advanced > Server settings)."
        )
    return response.content


def stream_name(quality: str) -> str:
    return STREAMS.get(quality, STREAMS["sd"])


# HLS (for phones without Media Source Extensions): only these go2rtc paths are passed through.
HLS_PATHS = frozenset(
    {"api/stream.m3u8", "api/hls/playlist.m3u8", "api/hls/segment.ts", "api/hls/segment.m4s", "api/hls/init.mp4"}
)
