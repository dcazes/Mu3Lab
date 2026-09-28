"""Multi-application install batches owned by one operator."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, Operator, OperatorMutation, Owner, VerifiedAccount, job_identity
from ctl.install_batches import InstallBatchStore
from ctl.jobs import JobStore
from ctl.registry import RegistryError
from ctl.registry import load as load_registry

router = APIRouter(prefix="/api/v1", tags=["install-batches"])

RESETTABLE = frozenset({"paused", "cancelled", "completed_with_failures", "reset_failed"})


def _batch_store() -> InstallBatchStore:
    batches = InstallBatchStore.runtime()
    if batches is None:
        raise ApiError(503, "runtime state is not initialized")
    return batches


def _owned_batch(batch_id: str, identity: IdentityData) -> tuple[InstallBatchStore, dict[str, Any]]:
    batches = InstallBatchStore.runtime()
    batch = batches.get(batch_id) if batches else None
    if not batches or not batch or batch["owner_uid"] != identity.get("subject_id"):
        raise ApiError(404, "batch not found")
    return batches, batch


def _batch_view(batch: dict[str, Any] | None) -> dict[str, Any]:
    """Enrich a batch with its live child job and a redacted event tail."""
    if not batch:
        return {"batch": None, "current_job": None}
    items = batch.get("items", [])
    current_item = next(
        (
            item
            for item in items
            if int(item.get("ordinal", -1)) == int(batch.get("current_ordinal", -1)) and item.get("job_id")
        ),
        None,
    )
    current_item = current_item or next(
        (item for item in items if item.get("state") in {"queued", "running"} and item.get("job_id")), None
    )
    current_job = None
    jobs_store = JobStore.runtime()
    if current_item and jobs_store:
        job_id = str(current_item["job_id"])
        job = jobs_store.get(job_id)
        if job:
            current_job = {
                "id": job_id,
                "state": str(job.get("state") or "queued"),
                "step_id": str(job.get("step_id") or ""),
                "detail": str(job.get("detail") or ""),
                "events": jobs_store.events(job_id, limit=20),
            }
    return {"batch": batch, "current_job": current_job}


@router.post("/services/install-batch")
async def create_install_batch(request: Request, operator: VerifiedAccount) -> dict[str, Any]:
    service_ids = (await runtime.json_body(request)).get("service_ids", [])
    if not isinstance(service_ids, list) or not all(isinstance(item, str) for item in service_ids):
        raise ApiError(409, "service_ids must be a list of curated application IDs")
    batches, jobs_store, control = _batch_store(), runtime.job_store(), runtime.control_state()
    try:
        result = batches.create(
            load_registry(),
            service_ids,
            actor=str(operator["username"]),
            owner_uid=str(operator["subject_id"]),
            identity=job_identity(operator),
            idempotency_key=request.headers.get("idempotency-key", ""),
            jobs=jobs_store,
            control=control,
        )
    except (ValueError, RegistryError) as exc:
        raise ApiError(409, str(exc)) from exc
    return {"ok": True, "batch": result}


@router.get("/service-install-batches")
def latest_install_batch(owner: Owner) -> dict[str, Any]:
    batches = InstallBatchStore.runtime()
    return {"ok": True, **_batch_view(batches.latest(str(owner["subject_id"])) if batches else None)}


@router.get("/service-install-batches/{batch_id}")
def get_install_batch(batch_id: str, operator: Operator) -> dict[str, Any]:
    _, batch = _owned_batch(batch_id, operator)
    return {"ok": True, **_batch_view(batch)}


@router.post("/service-install-batches/{batch_id}/resume")
def resume_install_batch(batch_id: str, operator: OperatorMutation) -> dict[str, Any]:
    batches, _ = _owned_batch(batch_id, operator)
    try:
        return {"ok": True, "batch": batches.resume(batch_id, runtime.job_store())}
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc


@router.post("/service-install-batches/{batch_id}/cancel")
def cancel_install_batch(batch_id: str, operator: OperatorMutation) -> dict[str, Any]:
    batches, _ = _owned_batch(batch_id, operator)
    jobs_store = JobStore.runtime()
    if not jobs_store or not batches.cancel(batch_id, jobs_store):
        raise ApiError(409, "batch cannot be cancelled")
    return {"ok": True, "batch": batches.get(batch_id)}


@router.post("/service-install-batches/{batch_id}/reset")
def reset_install_batch(batch_id: str, operator: OperatorMutation) -> JSONResponse:
    batches, batch = _owned_batch(batch_id, operator)
    jobs_store = JobStore.runtime()
    if not jobs_store or not runtime.control_state():
        raise ApiError(503, "runtime state is unavailable")
    if batch["state"] not in RESETTABLE:
        raise ApiError(409, "batch is not resettable")
    # Cleanup is queued for the durable worker rather than running Docker in
    # this request, and must never race an active operation.
    active = {
        str(job.get("service_id"))
        for job in jobs_store.jobs(limit=100)
        if job.get("state") in {"queued", "running", "waiting_for_confirmation"}
    }
    for item in batch["items"]:
        if item["state"] in {"failed", "cancelled"} and str(item["service_id"]) in active:
            raise ApiError(409, f"{item['service_id']} still has an active operation")
    try:
        reset_batch = batches.begin_reset(batch_id, jobs_store)
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    return JSONResponse(
        {
            "ok": True,
            "reset": True,
            "batch": reset_batch,
            "message": "Cleanup has been queued. Persistent data will be preserved.",
        },
        status_code=202,
    )
