"""The front-door camera's live view, for administrators, through go2rtc.

go2rtc (the app with the ``camera_stream`` capability) turns the camera's RTSP stream into
fragments a browser plays. It listens only on this server; the dashboard reaches it through
Mu3Lab (``/api/v1/home/camera/...``), which admits administrators only, so the view works
anywhere on the tailnet without opening the camera to the network.

The camera's address and login are kept in Mu3Lab's secret store, not in go2rtc's files:
whenever someone opens the view, Mu3Lab hands go2rtc the stream addresses (again after a
go2rtc restart). go2rtc connects to the camera only while someone watches and lets go
after; nothing is recorded.

go2rtc itself runs only while it is needed: the first viewer (or a test picture) starts it
through the same start job the Apps page uses, and it is stopped once nobody has used it for
``IDLE_SECONDS``. It is a helper for the Camera widget (``serves_widget``), so it is not listed
in Apps or Discover; setting up the camera installs it.

Two streams: the camera's light sub-stream for the widget, and its main stream for HD on tap.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote

import httpx

from ctl.jobs import JobStore
from ctl.manifest.catalog import App
from ctl.platform_apps import by_capability
from ctl.store.secrets import SecretStore

CAPABILITY = "camera_stream"
SCOPE = "home_devices"
NAME = "camera"
STREAMS = {"sd": "front_door", "hd": "front_door_hd"}
# Reolink's usual RTSP paths; some models name them differently (the settings say how to change them).
DEFAULTS: dict[str, Any] = {
    "port": 554,
    "sd_path": "h264Preview_01_sub",
    "hd_path": "h264Preview_01_main",
    "name": "Front door",
}
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


# --- go2rtc only while it is needed -----------------------------------------------------

ACTOR = "camera"
IDLE_SECONDS = 120
START_SECONDS = 90
_lock = threading.Lock()
_viewers = 0
_last_used = 0.0
_timer: threading.Timer | None = None


def _answers() -> bool:
    try:
        return httpx.get(f"{go2rtc_url()}/api", timeout=2).is_success
    except httpx.HTTPError:
        return False


def _queue(jobs: JobStore, action: str, detail: str) -> dict[str, Any]:
    app = _app()
    # One start (or stop) per minute however many people press Watch at once.
    return jobs.create(
        kind="lifecycle",
        service_id=app.id,
        action=action,
        actor=ACTOR,
        namespace="camera.on_demand",
        detail=detail,
        idempotency_key=f"{action}:{app.id}:{int(time.time() // 60)}",
    )


def wake(jobs: JobStore) -> None:
    """Start go2rtc when it is stopped, and wait until it answers."""
    touch()
    if _answers():
        return
    from ctl.control_state import ControlState

    state = ControlState.runtime()
    installation = state.app_installation(_app().id) if state else None
    if not installation or installation.get("state") in {None, "not_installed"}:
        raise CameraError("The camera's stream helper is not installed yet. Set the camera up again to install it.")
    job = _queue(jobs, "start", "Someone opened the camera, so its stream helper starts.")
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        if _answers():
            return
        state = (jobs.get(job["id"]) or {}).get("state")
        if state in {"failed", "cancelled"}:
            raise CameraError("The camera's stream helper (go2rtc) did not start. Check Activity for why.")
        time.sleep(1)
    raise CameraError("The camera's stream helper (go2rtc) is taking too long to start. Try again in a minute.")


def touch() -> None:
    """The camera was just used: put off stopping go2rtc."""
    global _last_used
    with _lock:
        _last_used = time.monotonic()
        _arm()


@contextmanager
def watching() -> Iterator[None]:
    """Someone is watching: go2rtc keeps running until they stop, then for ``IDLE_SECONDS``."""
    global _viewers, _last_used
    with _lock:
        _viewers += 1
        _arm()
    try:
        yield
    finally:
        with _lock:
            _viewers -= 1
            _last_used = time.monotonic()
            _arm()


def _arm() -> None:
    """(Re)start the countdown to stopping go2rtc; with _lock held."""
    global _timer
    if _timer is not None:
        _timer.cancel()
        _timer = None
    if _viewers == 0:
        _timer = threading.Timer(IDLE_SECONDS, _idle)
        _timer.daemon = True
        _timer.start()


_jobs: JobStore | None = None


def use_jobs(jobs: JobStore) -> None:
    """The job store the idle stop queues its job in (the control plane's)."""
    global _jobs
    _jobs = jobs


def _idle() -> None:
    with _lock:
        if _viewers or time.monotonic() - _last_used < IDLE_SECONDS - 1 or _jobs is None:
            return
    if not _answers():
        return
    try:
        _queue(_jobs, "stop", "Nobody is watching the camera, so its stream helper stops.")
    except (CameraError, ValueError):
        pass


def after_restart(jobs: JobStore) -> None:
    """The control plane started: a go2rtc left running stops once it has been idle a while."""
    use_jobs(jobs)
    touch()


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
    except (httpx.HTTPError, ValueError):
        raise CameraError("The camera's stream helper (go2rtc) is not running. Try again.") from None
    try:
        for quality, stream in STREAMS.items():
            if stream not in known:
                # go2rtc adds the stream, then answers 400 because it cannot save it to its settings
                # file, which is read-only on purpose (the camera login stays out of it): check below.
                httpx.put(
                    f"{base}/api/streams", params={"name": stream, "src": _source(record, f"{quality}_path")}, timeout=5
                )
        known = httpx.get(f"{base}/api/streams", timeout=5).json() or {}
    except (httpx.HTTPError, ValueError):
        raise CameraError("The camera's stream helper (go2rtc) stopped answering. Try again.") from None
    if not all(stream in known for stream in STREAMS.values()):
        raise CameraError("go2rtc did not accept the camera's stream addresses. Check them in Settings > Home devices.")


def reset_streams() -> None:
    """Drop go2rtc's copies of the streams, so the next view uses the saved settings (or none)."""
    try:
        for stream in STREAMS.values():
            httpx.delete(f"{go2rtc_url()}/api/streams", params={"src": stream}, timeout=5)
    except (httpx.HTTPError, CameraError):
        pass


def problem(store: SecretStore | None = None) -> str:
    """Why the camera cannot send video, found from this server; empty when its video port answers."""
    record = saved(store)
    if not record:
        return "No camera is set up yet. An administrator adds it in Settings > Home devices."
    host, port = record["host"], int(record.get("port", DEFAULTS["port"]))
    try:
        socket.create_connection((host, port), timeout=4).close()
    except ConnectionRefusedError:
        return (
            f"The camera at {host} refused the video connection on port {port}: RTSP is switched off. In the "
            "Reolink app, open the camera's Settings > Network > Advanced > Server settings (Port settings on some "
            "models) and switch on RTSP. Battery-powered Reolink cameras have no RTSP of their own."
        )
    except OSError:
        return f"The camera at {host} did not answer. Check its address and that it is online."
    return ""


def snapshot(jobs: JobStore, quality: str = "sd") -> bytes:
    """One picture from the camera, to check the settings work."""
    wake(jobs)
    ensure_streams()
    try:
        response = httpx.get(
            f"{go2rtc_url()}/api/frame.jpeg", params={"src": STREAMS.get(quality, STREAMS["sd"])}, timeout=20
        )
    except httpx.HTTPError:
        response = None
    if response is None or not response.is_success or not response.content:
        text = response.text.lower() if response is not None else ""
        reason = problem() or (
            "The camera refused the username or password." if "401" in text or "unauthorized" in text else ""
        )
        raise CameraError(reason or "The camera did not send a picture. Check its address, login and stream paths.")
    return response.content


def stream_name(quality: str) -> str:
    return STREAMS.get(quality, STREAMS["sd"])


# HLS (for phones without Media Source Extensions): only these go2rtc paths are passed through.
HLS_PATHS = frozenset(
    {"api/stream.m3u8", "api/hls/playlist.m3u8", "api/hls/segment.ts", "api/hls/segment.m4s", "api/hls/init.mp4"}
)
