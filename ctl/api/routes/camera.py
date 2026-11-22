"""The front-door camera: its settings, a test picture, and the live view relayed from go2rtc.

Administrators only, every route: the settings hold the camera's login, and the live view is
the household's front door. The browser never talks to go2rtc; these routes relay it.
"""

from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from ctl import camera
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, Operator, OperatorMutation, resolve_identity

router = APIRouter(prefix="/api/v1/home", tags=["home"], route_class=ContractRoute)
# The relay is a plain route: a WebSocket cannot go through the JSON contract wrapper.
live = APIRouter(prefix="/api/v1/home", tags=["home"])

QUALITIES = ("sd", "hd")


def _audit(admin: IdentityData, event: str, detail: str) -> None:
    runtime.job_store().record_audit(actor=str(admin["username"]), event=event, detail=detail)


@router.get("/devices/camera", response_model=models.CameraSettings)
def read_camera(_admin: Operator) -> models.CameraSettings:
    return models.CameraSettings.model_validate(camera.public())


@router.put("/devices/camera", response_model=models.CameraSettings)
def save_camera(payload: models.CameraSettingsRequest, admin: OperatorMutation) -> models.CameraSettings:
    try:
        settings = camera.save(payload.model_dump())
    except camera.CameraError as exc:
        raise ApiError(400, str(exc), code="camera_invalid") from None
    camera.reset_streams()
    _audit(admin, "camera.saved", f"Saved the {settings['name']} camera's settings.")
    return models.CameraSettings.model_validate(settings)


@router.delete("/devices/camera", response_model=models.CameraSettings)
def remove_camera(admin: OperatorMutation) -> models.CameraSettings:
    camera.forget()
    _audit(admin, "camera.removed", "Removed the camera from Mu3Lab.")
    return models.CameraSettings.model_validate(camera.public())


@live.get("/camera/snapshot.jpg", include_in_schema=False)
async def camera_snapshot(_admin: Operator, quality: str = "sd") -> Response:
    try:
        picture = await run_in_threadpool(
            camera.snapshot, runtime.job_store(), quality if quality in QUALITIES else "sd"
        )
    except camera.CameraError as exc:
        raise ApiError(503, str(exc), code="camera_unavailable") from None
    return Response(picture, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@live.get("/camera/{path:path}", include_in_schema=False)
async def camera_hls(path: str, request: Request, _admin: Operator) -> Response:
    """HLS for phones that cannot use the WebSocket view: only go2rtc's HLS paths pass."""
    if path not in camera.HLS_PATHS:
        raise ApiError(404, "not found")
    params = dict(request.query_params)
    if "src" in params and params["src"] not in camera.STREAMS.values():
        raise ApiError(404, "not found")
    camera.touch()
    try:
        if path == "api/stream.m3u8":
            await run_in_threadpool(camera.wake, runtime.job_store())
            await run_in_threadpool(camera.ensure_streams)
        async with httpx.AsyncClient(timeout=20) as client:
            upstream = await client.get(f"{camera.go2rtc_url()}/{path}", params=params)
    except camera.CameraError as exc:
        raise ApiError(503, str(exc), code="camera_unavailable") from None
    except httpx.HTTPError:
        raise ApiError(
            503, "The camera's stream helper (go2rtc) is not running. Try again.", code="camera_unavailable"
        ) from None
    return Response(
        upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers={"Cache-Control": "no-store"},
    )


def _allowed(websocket: WebSocket) -> bool:
    """An administrator, from this dashboard's own page (no other site may open the stream)."""
    identity = resolve_identity(websocket)  # type: ignore[arg-type]  # reads headers only
    origin = urlsplit(websocket.headers.get("origin", ""))
    same_site = origin.scheme == "https" and origin.netloc.lower() == websocket.headers.get("host", "").lower()
    return bool(identity.get("is_admin")) and same_site


@live.websocket("/camera/live")
async def camera_live(websocket: WebSocket, quality: str = "sd") -> None:
    """Relay go2rtc's Media Source stream: the page's codec list goes up, video fragments come down."""
    if not _allowed(websocket):
        await websocket.close(code=4403)
        return
    with camera.watching():
        await _relay(websocket, quality)


async def _relay(websocket: WebSocket, quality: str) -> None:
    await websocket.accept()
    try:
        # The first viewer starts go2rtc; the page says so while it waits.
        await websocket.send_json({"type": "status", "value": "starting"})
        await run_in_threadpool(camera.wake, runtime.job_store())
        await run_in_threadpool(camera.ensure_streams)
        # go2rtc would only hang up when the camera refuses; say why instead.
        reason = await run_in_threadpool(camera.problem)
        if reason:
            raise camera.CameraError(reason)
    except camera.CameraError as exc:
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "value": str(exc)})
            await websocket.close()
        return
    except (OSError, WebSocketDisconnect):
        return
    from websockets.asyncio.client import connect

    stream = camera.stream_name(quality)
    target = camera.go2rtc_url().replace("http://", "ws://") + f"/api/ws?src={stream}"
    try:
        async with connect(target, max_size=None, open_timeout=10) as upstream:

            async def up() -> None:
                while True:
                    message = await websocket.receive_text()
                    await upstream.send(message)

            async def down() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except (OSError, WebSocketDisconnect):
        pass
    except Exception:  # go2rtc refused or dropped: tell the page instead of hanging up silently
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "value": "The live view stopped. Try again."})
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()
