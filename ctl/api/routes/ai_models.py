"""Local AI models and the household's outside-provider switch (R31)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from ctl import chat_ops, duplex_voice, local_models, model_runners, voice_tools
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import Member, OperatorMutation
from ctl.jobs import JobConflict, JobStore
from ctl.registry import ROOT, load
from ctl.runtime import RuntimePaths

router = APIRouter(prefix="/api/v1/ai", tags=["ai"], route_class=ContractRoute)
NAMESPACE = "ai.models"


def _view(store: JobStore | None) -> dict:
    paths = RuntimePaths()
    active = next(
        (
            job
            for job in (store.jobs_for_service(local_models.SERVICE_ID, limit=5) if store else [])
            if job.get("state") in {"queued", "running"}
        ),
        None,
    )
    return {
        "ok": True,
        **local_models.status(paths),
        "embedding_locked_by": local_models.index_tied(load().catalog, paths),
        "active_job_id": str(active["id"]) if active else "",
    }


DUPLEX_HOLDS_CARD = (
    "The voice assistant's full-duplex model holds the graphics card, so local models are stopped. "
    "Switch the voice assistant back to separate steps in Settings, Voice Assistant first."
)


def _refuse_while_duplex() -> None:
    if duplex_voice.duplex_on():
        raise ApiError(409, DUPLEX_HOLDS_CARD)


@router.get("/models", response_model=models.AiModels, response_model_exclude_none=True)
def ai_models(_member: Member) -> models.AiModels:
    """Everyone may see where AI requests go; only administrators change it."""
    return models.AiModels.model_validate(_view(JobStore.runtime()))


@router.post("/models/try", response_model=models.AiTryResult, response_model_exclude_none=True)
async def try_model(payload: models.AiTryRequest, operator: OperatorMutation) -> models.AiTryResult:
    """One test answer from a downloaded model or the outside providers, before switching to it."""
    _refuse_while_duplex()
    try:
        result = await run_in_threadpool(local_models.try_model, payload.target, payload.prompt)
    except (ValueError, *model_runners.ENGINE_ERRORS) as exc:
        raise ApiError(422, str(exc)) from exc
    return models.AiTryResult.model_validate({"ok": True, **result})


@router.post("/models/{model_id}/remove", response_model=models.OkResponse, response_model_exclude_none=True)
async def remove_model(model_id: str, operator: OperatorMutation) -> models.OkResponse:
    """Delete one downloaded model no setting uses, to free its disk space."""
    _refuse_while_duplex()
    if _view(JobStore.runtime())["active_job_id"]:
        raise ApiError(409, "Wait for the AI settings change to finish.")
    try:
        detail = await run_in_threadpool(local_models.remove, model_id)
    except (ValueError, *model_runners.ENGINE_ERRORS) as exc:
        raise ApiError(422, str(exc)) from exc
    return models.OkResponse.model_validate({"ok": True, "message": detail})


@router.put("/models", response_model=models.JobResponse, response_model_exclude_none=True)
def change_ai_models(payload_model: models.AiModelsRequest, request: Request, operator: OperatorMutation):
    """Queue the change: models download and are checked before anything is switched over."""
    _refuse_while_duplex()
    store = runtime.job_store()
    paths = RuntimePaths()
    try:
        chosen = local_models.validate(payload_model.model_dump())
    except ValueError as exc:
        raise ApiError(422, str(exc)) from exc
    current = local_models.settings(paths)
    # Compare runners as they take effect: none chosen means the one serving now.
    current["runner"] = local_models.runner(paths).id
    chosen["runner"] = chosen["runner"] or current["runner"]
    if not model_runners.installed(chosen["runner"]):
        raise ApiError(422, "Install that model runner from Apps first.")
    if chosen["embedding_model"] != current["embedding_model"]:
        locked = local_models.index_tied(load().catalog, paths)
        if locked:
            raise ApiError(
                409,
                f"{', '.join(locked)} keeps a search index for one embedding model. "
                "Remove it before changing the search model.",
            )
    if all(chosen[key] == current[key] for key in chosen):
        raise ApiError(422, "These are already the current settings.")
    key = runtime.idempotency_key(request)
    subject = runtime.mutation_subject(operator)
    previous = store.by_idempotency_key(
        key or "",
        kind="wiring",
        service_id=local_models.SERVICE_ID,
        action="apply",
        actor=operator["username"],
        actor_subject=subject,
        namespace=NAMESPACE,
        request=chosen,
    )
    if previous:
        return models.JobResponse.model_validate({"ok": True, "duplicate": True, "job": previous})
    try:
        job = store.create(
            kind="wiring",
            service_id=local_models.SERVICE_ID,
            action="apply",
            actor=operator["username"],
            actor_subject=subject,
            namespace=NAMESPACE,
            request=chosen,
            params=local_models.as_params(chosen),
            detail="Operator changed the AI model settings.",
            idempotency_key=key,
        )
    except JobConflict as conflict:
        raise ApiError(409, "The AI model settings are already being changed.") from conflict
    return models.JobResponse.model_validate({"ok": True, "job": job})


@router.put("/voice", response_model=models.JobResponse, response_model_exclude_none=True)
def change_voice(payload_model: models.AiVoiceRequest, request: Request, operator: OperatorMutation):
    """Queue the voice assistant's change: a local model downloads and is checked before voice switches over."""
    _refuse_while_duplex()
    store = runtime.job_store()
    paths = RuntimePaths()
    current = local_models.voice_settings(paths)
    follow = payload_model.matches_chat
    if follow and current["matches_chat"]:
        raise ApiError(422, "The voice assistant already follows chat's setting.")
    try:
        chosen = local_models.voice_validate(
            local_models.voice_from_chat(paths) if follow else payload_model.model_dump(exclude={"matches_chat"})
        )
    except ValueError as exc:
        raise ApiError(422, str(exc)) from exc
    if chosen["privacy"] == "outside_allowed":
        chosen["model"] = ""  # only a local choice names a model
    if not follow and not current["matches_chat"] and all(chosen[key] == current[key] for key in chosen):
        raise ApiError(422, "These are already the current settings.")
    if chosen["privacy"] != "outside_allowed" and not model_runners.installed(local_models.runner(paths).id):
        raise ApiError(422, "Install the model runner from Apps first.")
    if follow:
        chosen["matches_chat"] = "yes"
    key = runtime.idempotency_key(request)
    subject = runtime.mutation_subject(operator)
    previous = store.by_idempotency_key(
        key or "",
        kind="wiring",
        service_id=local_models.SERVICE_ID,
        action=local_models.VOICE_ACTION,
        actor=operator["username"],
        actor_subject=subject,
        namespace=NAMESPACE,
        request=chosen,
    )
    if previous:
        return models.JobResponse.model_validate({"ok": True, "duplicate": True, "job": previous})
    try:
        job = store.create(
            kind="wiring",
            service_id=local_models.SERVICE_ID,
            action=local_models.VOICE_ACTION,
            actor=operator["username"],
            actor_subject=subject,
            namespace=NAMESPACE,
            request=chosen,
            params=chosen,
            detail="Operator changed the voice assistant's model.",
            idempotency_key=key,
        )
    except JobConflict as conflict:
        raise ApiError(409, "The AI model settings are already being changed.") from conflict
    return models.JobResponse.model_validate({"ok": True, "job": job})


@router.put("/voice/engine", response_model=models.JobResponse, response_model_exclude_none=True)
def change_voice_engine(payload_model: models.AiVoiceEngineRequest, request: Request, operator: OperatorMutation):
    """Queue the switch between separate steps and the full-duplex model; the job stops and starts GPU apps."""
    store = runtime.job_store()
    wanted = payload_model.engine
    if wanted == duplex_voice.engine():
        raise ApiError(422, "The voice assistant already talks this way.")
    if wanted == duplex_voice.DUPLEX:
        reason = duplex_voice.unavailable_reason(ROOT)
        if reason:
            raise ApiError(422, reason)
    chosen = {"engine": wanted}
    key = runtime.idempotency_key(request)
    subject = runtime.mutation_subject(operator)
    previous = store.by_idempotency_key(
        key or "",
        kind="wiring",
        service_id=local_models.SERVICE_ID,
        action=duplex_voice.ACTION,
        actor=operator["username"],
        actor_subject=subject,
        namespace=NAMESPACE,
        request=chosen,
    )
    if previous:
        return models.JobResponse.model_validate({"ok": True, "duplicate": True, "job": previous})
    try:
        job = store.create(
            kind="wiring",
            service_id=local_models.SERVICE_ID,
            action=duplex_voice.ACTION,
            actor=operator["username"],
            actor_subject=subject,
            namespace=NAMESPACE,
            request=chosen,
            params=chosen,
            detail="Operator changed how the voice assistant talks.",
            idempotency_key=key,
        )
    except JobConflict as conflict:
        raise ApiError(409, "The AI model settings are already being changed.") from conflict
    return models.JobResponse.model_validate({"ok": True, "job": job})


def _voice_apps() -> models.VoiceApps:
    return models.VoiceApps.model_validate(
        {"ok": True, "installed": chat_ops.installed(voice_tools.app().id), "apps": voice_tools.available()}
    )


@router.get("/voice/apps", response_model=models.VoiceApps, response_model_exclude_none=True)
async def voice_apps(_member: Member) -> models.VoiceApps:
    """The app assistants the voice assistant can hand people to."""
    return await run_in_threadpool(_voice_apps)


@router.put("/voice/apps/{app_id}", response_model=models.VoiceApps, response_model_exclude_none=True)
async def change_voice_app(
    app_id: str, payload: models.VoiceAppRequest, operator: OperatorMutation
) -> models.VoiceApps:
    """Switch one app's assistant on or off for voice; the agent sees it from the next call."""
    if app_id not in {item["id"] for item in await run_in_threadpool(voice_tools.available)}:
        raise ApiError(404, "That app has no assistant for the voice assistant to use.")
    voice_tools.set_switch(app_id, payload.on)
    await run_in_threadpool(voice_tools.sync, lambda _line: None)
    return await run_in_threadpool(_voice_apps)
