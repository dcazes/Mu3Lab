"""Each person's own key for using Mu3Lab's speech from their devices and other programs."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import voice_keys
from ctl.api import models
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import Owner, OwnerMutation
from ctl.integrations.litellm import LiteLLMError
from ctl.manifest.catalog import cached
from ctl.runtime import RuntimePaths
from ctl.service_state import tailnet_dns_name

router = APIRouter(prefix="/api/v1/voice-key", tags=["voice"], route_class=ContractRoute)
# The key is shown exactly once; no browser or proxy may keep a copy.
_NO_STORE = {"Cache-Control": "no-store"}


def _require_speech() -> None:
    if not voice_keys.speech_installed(cached(), RuntimePaths()):
        raise ApiError(409, "Install Speech first; voice keys use it.", code="speech_not_installed")


@router.get("", response_model=models.VoiceKeyStatus, response_model_exclude_none=True)
async def voice_key_status(person: Owner) -> models.VoiceKeyStatus:
    catalog, paths = cached(), RuntimePaths()
    available = voice_keys.speech_installed(catalog, paths)
    saved = {"exists": False, "created_at": ""}
    if available:
        try:
            saved = await run_in_threadpool(voice_keys.status, str(person["subject_id"]))
        except LiteLLMError as exc:
            raise ApiError(503, str(exc), code="voice_key_unavailable") from None
    return models.VoiceKeyStatus.model_validate(
        {
            "ok": True,
            "available": available,
            **saved,
            "base_url": voice_keys.base_url(tailnet_dns_name(), catalog),
            "models": voice_keys.MODELS,
        }
    )


@router.post("", response_model=models.VoiceKeyCreated, response_model_exclude_none=True)
async def create_voice_key(person: OwnerMutation) -> JSONResponse:
    _require_speech()
    try:
        key = await run_in_threadpool(voice_keys.create, str(person["subject_id"]))
    except LiteLLMError as exc:
        raise ApiError(503, str(exc), code="voice_key_unavailable", headers=_NO_STORE) from None
    body = models.VoiceKeyCreated.model_validate(
        {
            "ok": True,
            "key": key,
            "base_url": voice_keys.base_url(tailnet_dns_name(), cached()),
            "models": voice_keys.MODELS,
        }
    )
    return JSONResponse(body.model_dump(mode="json"), headers=_NO_STORE)


@router.delete("", response_model=models.VoiceKeyStatus, response_model_exclude_none=True)
async def revoke_voice_key(person: OwnerMutation) -> models.VoiceKeyStatus:
    try:
        await run_in_threadpool(voice_keys.revoke, str(person["subject_id"]))
    except LiteLLMError as exc:
        raise ApiError(503, str(exc), code="voice_key_unavailable") from None
    return models.VoiceKeyStatus.model_validate(
        {
            "ok": True,
            "available": voice_keys.speech_installed(cached(), RuntimePaths()),
            "exists": False,
            "base_url": voice_keys.base_url(tailnet_dns_name(), cached()),
            "models": voice_keys.MODELS,
        }
    )
