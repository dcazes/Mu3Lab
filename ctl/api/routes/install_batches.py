"""Multi-application install batches owned by one operator."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
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

router = APIRouter(prefix="/api/v1", tags=["install-batches"], route_class=ContractRoute)

RESETTABLE = frozenset({"paused", "cancelled", "completed_with_failures", "reset_failed"})


def _batch_store() -> InstallBatchStore:
    batches = InstallBatchStore.runtime()
    if batches is None:
        raise ApiError(503, "runtime state is not initialized")
    return batches


def _owned_batch(batch_id: str, identity: IdentityData) -> tuple[InstallBatchStore, OwnedBatch]:
    batches = InstallBatchStore.runtime()
    batch = batches.get(batch_id) if batches else None
    if not batches or not batch or batch["owner_uid"] != identity.get("subject_id"):
        raise ApiError(404, "batch not found")
    return batches, OwnedBatch.model_validate(batch)


class OwnedBatch(models.InstallBatch):
    owner_uid: str


def _batch_view(value: object) -> models.InstallBatchResponse:
    """Enrich a batch with its child job and download progress."""
    if value is None:
        return models.InstallBatchResponse(ok=True, batch=None)
    batch = models.InstallBatch.model_validate(value)
    downloads = ImageDownloadStore.runtime()
    keys = {item.job_id or progress_key(batch.id, int(item.ordinal)) for item in batch.items}
    progress = downloads.for_jobs(sorted(keys)) if downloads else {}
    for item in batch.items:
        raw = progress.get(item.job_id) or progress.get(progress_key(batch.id, int(item.ordinal)))
        item.download = models.ImageDownload.model_validate(raw) if raw else None
    current = next((item for item in batch.items if item.ordinal == batch.current_ordinal and item.job_id), None)
    current = current or next(
        (item for item in batch.items if item.state in {"queued", "running"} and item.job_id), None
    )
    current_job = None
    jobs_store = JobStore.runtime()
    if current and jobs_store:
        job = jobs_store.get(current.job_id)
        if job:
            current_job = models.InstallBatchJob.model_validate(
                {
                    "id": current.job_id,
                    "state": job.get("state") or "queued",
                    "step_id": job.get("step_id") or "",
                    "detail": job.get("detail") or "",
                    "events": jobs_store.events(current.job_id, limit=20),
                }
            )
    return models.InstallBatchResponse(ok=True, batch=batch, current_job=current_job)


@router.post("/services/install-batch", response_model=models.InstallBatchResponse, response_model_exclude_none=True)
async def create_install_batch(
    payload_model: models.InstallBatchRequest, request: Request, operator: VerifiedAccount
) -> models.InstallBatchResponse:
    """Install apps in the order given; ``parallel_downloads`` apps download at once."""
    payload = payload_model.model_dump()
    service_ids = payload.get("service_ids", [])
    parallel = payload.get("parallel_downloads", DEFAULT_PARALLEL_DOWNLOADS)
    return models.InstallBatchResponse.model_validate(
        await run_in_threadpool(_create_batch, service_ids, parallel, request, operator)
    )


def _create_batch(
    service_ids: list[str], parallel: int, request: Request, operator: IdentityData
) -> models.InstallBatchResponse:
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
    return models.InstallBatchResponse.model_validate({"ok": True, "batch": result})


def _free_bytes() -> int:
    """Free space where Docker keeps images (the web process only reads disk stats)."""
    for path in (Path("/var/lib/docker"), Path("/var/lib/containerd"), Path("/")):
        try:
            return shutil.disk_usage(path).free
        except OSError:
            continue
    return 0


@router.get("/app-sizes", response_model=models.AppSizesResponse, response_model_exclude_none=True)
def app_sizes(operator: Member, ids: str = "") -> models.AppSizesResponse:
    """Per-app sizes, plus totals for a selection with shared layers counted once."""
    store = AppSizeStore.runtime()
    sizes = store.all() if store else {}
    selection = [item for item in ids.split(",") if item]
    return models.AppSizesResponse.model_validate(
        {
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
    )


@router.get("/service-install-batches", response_model=models.InstallBatchResponse, response_model_exclude_none=True)
def latest_install_batch(owner: Owner) -> models.InstallBatchResponse:
    batches = InstallBatchStore.runtime()
    return models.InstallBatchResponse.model_validate(
        _batch_view(batches.latest(str(owner["subject_id"])) if batches else None)
    )


@router.get(
    "/service-install-batches/{batch_id}", response_model=models.InstallBatchResponse, response_model_exclude_none=True
)
def get_install_batch(batch_id: str, operator: Member) -> models.InstallBatchResponse:
    _, batch = _owned_batch(batch_id, operator)
    return models.InstallBatchResponse.model_validate(_batch_view(batch))


@router.post(
    "/service-install-batches/{batch_id}/resume",
    response_model=models.InstallBatchResponse,
    response_model_exclude_none=True,
)
def resume_install_batch(batch_id: str, operator: MemberMutation) -> models.InstallBatchResponse:
    batches, _ = _owned_batch(batch_id, operator)
    try:
        return models.InstallBatchResponse.model_validate(
            {"ok": True, "batch": batches.resume(batch_id, runtime.job_store())}
        )
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc


async def _batch_change(batch_id: str, operator: IdentityData, change) -> models.InstallBatchResponse:
    def apply() -> models.InstallBatchResponse:
        batches, _ = _owned_batch(batch_id, operator)
        try:
            return _batch_view(change(batches))
        except ValueError as exc:
            raise ApiError(409, str(exc)) from exc

    return await run_in_threadpool(apply)


@router.post(
    "/service-install-batches/{batch_id}/downloads/{service_id}/pause",
    response_model=models.InstallBatchResponse,
    response_model_exclude_none=True,
)
async def pause_download(batch_id: str, service_id: str, operator: MemberMutation) -> models.InstallBatchResponse:
    return models.InstallBatchResponse.model_validate(
        await _batch_change(batch_id, operator, lambda batches: batches.pause_download(batch_id, service_id))
    )


@router.post(
    "/service-install-batches/{batch_id}/downloads/{service_id}/resume",
    response_model=models.InstallBatchResponse,
    response_model_exclude_none=True,
)
async def resume_download(batch_id: str, service_id: str, operator: MemberMutation) -> models.InstallBatchResponse:
    return models.InstallBatchResponse.model_validate(
        await _batch_change(batch_id, operator, lambda batches: batches.resume_download(batch_id, service_id))
    )


@router.post(
    "/service-install-batches/{batch_id}/order",
    response_model=models.InstallBatchResponse,
    response_model_exclude_none=True,
)
async def reorder_batch(
    payload_model: models.BatchOrderRequest, batch_id: str, request: Request, operator: MemberMutation
) -> models.InstallBatchResponse:
    service_ids = (payload_model.model_dump()).get("service_ids", [])
    return models.InstallBatchResponse.model_validate(
        await _batch_change(batch_id, operator, lambda batches: batches.reorder(batch_id, service_ids))
    )


@router.post(
    "/service-install-batches/{batch_id}/parallel-downloads",
    response_model=models.InstallBatchResponse,
    response_model_exclude_none=True,
)
async def set_parallel_downloads(
    payload_model: models.ParallelDownloadsRequest, batch_id: str, request: Request, operator: MemberMutation
) -> models.InstallBatchResponse:
    count = (payload_model.model_dump()).get("parallel_downloads")
    return models.InstallBatchResponse.model_validate(
        await _batch_change(batch_id, operator, lambda batches: batches.set_parallel_downloads(batch_id, count))
    )


@router.post(
    "/service-install-batches/{batch_id}/cancel",
    response_model=models.InstallBatchResponse,
    response_model_exclude_none=True,
)
def cancel_install_batch(batch_id: str, operator: MemberMutation) -> models.InstallBatchResponse:
    batches, _ = _owned_batch(batch_id, operator)
    jobs_store = JobStore.runtime()
    if not jobs_store or not batches.cancel(batch_id, jobs_store):
        raise ApiError(409, "batch cannot be cancelled")
    return models.InstallBatchResponse.model_validate({"ok": True, "batch": batches.get(batch_id)})


@router.post(
    "/service-install-batches/{batch_id}/reset",
    response_model=models.InstallBatchResponse,
    response_model_exclude_none=True,
)
def reset_install_batch(batch_id: str, operator: MemberMutation) -> models.InstallBatchResponse | JSONResponse:
    batches, batch = _owned_batch(batch_id, operator)
    jobs_store = JobStore.runtime()
    if not jobs_store or not runtime.control_state():
        raise ApiError(503, "runtime state is unavailable")
    if batch.state not in RESETTABLE:
        raise ApiError(409, "batch is not resettable")
    # Cleanup is queued for the durable worker rather than running Docker in
    # this request, and must never race an active operation.
    active = {str(job.get("service_id")) for job in jobs_store.active_jobs()}
    for item in batch.items:
        if item.state in {"failed", "cancelled"} and item.service_id in active:
            raise ApiError(409, f"{item.service_id} still has an active operation")
    try:
        reset_batch = batches.begin_reset(batch_id, jobs_store)
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    return JSONResponse(
        models.InstallBatchResponse.model_validate(
            {
                "ok": True,
                "reset": True,
                "batch": reset_batch,
                "message": "Cleanup has been queued. Persistent data will be preserved.",
            }
        ).model_dump(mode="json", exclude_none=True),
        status_code=202,
    )
