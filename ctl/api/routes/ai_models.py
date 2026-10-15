"""Local AI models and the household's outside-provider switch (R31)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ctl import local_models, model_runners
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import Member, OperatorMutation
from ctl.jobs import JobConflict, JobStore
from ctl.registry import load
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


@router.get("/models", response_model=models.AiModels, response_model_exclude_none=True)
def ai_models(_member: Member) -> models.AiModels:
    """Everyone may see where AI requests go; only administrators change it."""
    return models.AiModels.model_validate(_view(JobStore.runtime()))


@router.put("/models", response_model=models.JobResponse, response_model_exclude_none=True)
def change_ai_models(payload_model: models.AiModelsRequest, request: Request, operator: OperatorMutation):
    """Queue the change: models download and are checked before anything is switched over."""
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
            params=chosen,
            detail="Operator changed the AI model settings.",
            idempotency_key=key,
        )
    except JobConflict as conflict:
        raise ApiError(409, "The AI model settings are already being changed.") from conflict
    return models.JobResponse.model_validate({"ok": True, "job": job})
