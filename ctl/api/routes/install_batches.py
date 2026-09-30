"""Multi-application install batches owned by one operator."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import (
    IdentityData,
    Member,
    MemberMutation,
    Owner,
    VerifiedAccount,
    job_identity,
)
from ctl.app_sizes import AppSizeStore, summarize
from ctl.download_manager import progress_key
from ctl.image_downloads import ImageDownloadStore
from ctl.install_batches import DEFAULT_PARALLEL_DOWNLOADS, InstallBatchStore
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
    """Enrich a batch with its live child job, a redacted event tail and download progress."""
    if not batch:
        return {"batch": None, "current_job": None}
    items = batch.get("items", [])
    downloads = ImageDownloadStore.runtime()
    keys = {str(item.get("job_id") or "") or progress_key(str(batch["id"]), int(item["ordinal"])) for item in items}
    progress = downloads.for_jobs(sorted(keys)) if downloads else {}
    for item in items:
        # Before setup the background download reports progress; during setup, the job does.
        item["download"] = progress.get(str(item.get("job_id") or "")) or progress.get(
            progress_key(str(batch["id"]), int(item["ordinal"]))
        )
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
    """Install apps in the order given; ``parallel_downloads`` apps download at once."""
    payload = await runtime.json_body(request)
    service_ids = payload.get("service_ids", [])
    if not isinstance(service_ids, list) or not all(isinstance(item, str) for item in service_ids):
        raise ApiError(409, "service_ids must be a list of curated application IDs")
    parallel = payload.get("parallel_downloads", DEFAULT_PARALLEL_DOWNLOADS)
    if not isinstance(parallel, int) or isinstance(parallel, bool):
        raise ApiError(409, "parallel_downloads must be a whole number")
    return await run_in_threadpool(_create_batch, service_ids, parallel, request, operator)


def _create_batch(service_ids: list[str], parallel: int, request: Request, operator: IdentityData) -> dict[str, Any]:
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
            parallel_downloads=parallel,
        )
    except (ValueError, RegistryError) as exc:
        raise ApiError(409, str(exc)) from exc
    return {"ok": True, "batch": result}


def _free_bytes() -> int:
    """Free space where Docker keeps images (the web process only reads disk stats)."""
    for path in (Path("/var/lib/docker"), Path("/var/lib/containerd"), Path("/")):
        try:
            return shutil.disk_usage(path).free
        except OSError:
            continue
    return 0


@router.get("/app-sizes")
def app_sizes(operator: Member, ids: str = "") -> dict[str, Any]:
    """Per-app sizes, plus totals for a selection with shared layers counted once."""
    store = AppSizeStore.runtime()
    sizes = store.all() if store else {}
    selection = [item for item in ids.split(",") if item]
    return {
        "ok": True,
        "apps": {
            service_id: {
                **summarize(sizes, [service_id]),
                "updated_at": entry["updated_at"],
                "complete": not entry["error"],
            }
            for service_id, entry in sizes.items()
        },
        "selection": summarize(sizes, selection),
        "measured": all(item in sizes for item in selection),
        "free_bytes": _free_bytes(),
    }


@router.get("/service-install-batches")
def latest_install_batch(owner: Owner) -> dict[str, Any]:
    batches = InstallBatchStore.runtime()
    return {"ok": True, **_batch_view(batches.latest(str(owner["subject_id"])) if batches else None)}


@router.get("/service-install-batches/{batch_id}")
def get_install_batch(batch_id: str, operator: Member) -> dict[str, Any]:
    _, batch = _owned_batch(batch_id, operator)
    return {"ok": True, **_batch_view(batch)}


@router.post("/service-install-batches/{batch_id}/resume")
def resume_install_batch(batch_id: str, operator: MemberMutation) -> dict[str, Any]:
    batches, _ = _owned_batch(batch_id, operator)
    try:
        return {"ok": True, "batch": batches.resume(batch_id, runtime.job_store())}
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc


async def _batch_change(batch_id: str, operator: IdentityData, change) -> dict[str, Any]:
    def apply() -> dict[str, Any]:
        batches, _ = _owned_batch(batch_id, operator)
        try:
            return {"ok": True, **_batch_view(change(batches))}
        except ValueError as exc:
            raise ApiError(409, str(exc)) from exc

    return await run_in_threadpool(apply)


@router.post("/service-install-batches/{batch_id}/downloads/{service_id}/pause")
async def pause_download(batch_id: str, service_id: str, operator: MemberMutation) -> dict[str, Any]:
    return await _batch_change(batch_id, operator, lambda batches: batches.pause_download(batch_id, service_id))


@router.post("/service-install-batches/{batch_id}/downloads/{service_id}/resume")
async def resume_download(batch_id: str, service_id: str, operator: MemberMutation) -> dict[str, Any]:
    return await _batch_change(batch_id, operator, lambda batches: batches.resume_download(batch_id, service_id))


@router.post("/service-install-batches/{batch_id}/order")
async def reorder_batch(batch_id: str, request: Request, operator: MemberMutation) -> dict[str, Any]:
    service_ids = (await runtime.json_body(request)).get("service_ids", [])
    if not isinstance(service_ids, list) or not all(isinstance(item, str) for item in service_ids):
        raise ApiError(409, "service_ids must list the batch's apps in the wanted order")
    return await _batch_change(batch_id, operator, lambda batches: batches.reorder(batch_id, service_ids))


@router.post("/service-install-batches/{batch_id}/parallel-downloads")
async def set_parallel_downloads(batch_id: str, request: Request, operator: MemberMutation) -> dict[str, Any]:
    count = (await runtime.json_body(request)).get("parallel_downloads")
    if not isinstance(count, int) or isinstance(count, bool):
        raise ApiError(409, "parallel_downloads must be a whole number")
    return await _batch_change(batch_id, operator, lambda batches: batches.set_parallel_downloads(batch_id, count))


@router.post("/service-install-batches/{batch_id}/cancel")
def cancel_install_batch(batch_id: str, operator: MemberMutation) -> dict[str, Any]:
    batches, _ = _owned_batch(batch_id, operator)
    jobs_store = JobStore.runtime()
    if not jobs_store or not batches.cancel(batch_id, jobs_store):
        raise ApiError(409, "batch cannot be cancelled")
    return {"ok": True, "batch": batches.get(batch_id)}


@router.post("/service-install-batches/{batch_id}/reset")
def reset_install_batch(batch_id: str, operator: MemberMutation) -> JSONResponse:
    batches, batch = _owned_batch(batch_id, operator)
    jobs_store = JobStore.runtime()
    if not jobs_store or not runtime.control_state():
        raise ApiError(503, "runtime state is unavailable")
    if batch["state"] not in RESETTABLE:
        raise ApiError(409, "batch is not resettable")
    # Cleanup is queued for the durable worker rather than running Docker in
    # this request, and must never race an active operation.
    active = {str(job.get("service_id")) for job in jobs_store.active_jobs()}
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
