"""Durable jobs, the audit log, and the guided core-suite setup."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import JsonValue

from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import Member, Operator, OperatorMutation
from ctl.core_setup import start as start_core_setup
from ctl.core_setup import start_verify
from ctl.jobs import JobStore
from ctl.provisioning import ProvisioningStore
from ctl.registry import RegistryError
from ctl.registry import load as load_registry
from ctl.store import records

ROOT = Path(__file__).resolve().parents[3]
ACTIVE = frozenset({"queued", "running"})

router = APIRouter(prefix="/api/v1", tags=["jobs"], route_class=ContractRoute)


@router.get("/jobs", response_model=models.JobsResponse, response_model_exclude_none=True)
def list_jobs(_member: Member) -> models.JobsResponse:
    store = JobStore.runtime()
    if store is None:
        return models.JobsResponse.model_validate({"ok": True, "available": False, "jobs": []})
    return models.JobsResponse.model_validate({"ok": True, "available": True, "jobs": store.jobs()})


@router.get("/audit", response_model=models.AuditResponse, response_model_exclude_none=True)
def audit(_admin: Operator) -> models.AuditResponse:
    """Append-only, secret-redacted audit metadata."""
    store = JobStore.runtime()
    if store is None:
        return models.AuditResponse.model_validate({"ok": True, "available": False, "events": []})
    return models.AuditResponse.model_validate({"ok": True, "available": True, "events": store.audit()})


def _recent_job(store: JobStore, job_id: str) -> dict[str, JsonValue]:
    # By primary key: a job stays reachable however many newer jobs exist.
    job = store.get(job_id)
    if not job:
        raise ApiError(404, "job not found")
    return job


@router.get("/jobs/{job_id}", response_model=models.JobDetailResponse, response_model_exclude_none=True)
def job_detail(job_id: str, _operator: Member) -> models.JobDetailResponse:
    store = runtime.job_store()
    return models.JobDetailResponse.model_validate(
        {"ok": True, "job": _recent_job(store, job_id), "events": store.events(job_id)}
    )


@router.get("/jobs/{job_id}/events", response_model=models.JobEventsResponse, response_model_exclude_none=True)
def job_events(job_id: str, _operator: Member) -> models.JobEventsResponse:
    store = runtime.job_store()
    _recent_job(store, job_id)
    return models.JobEventsResponse.model_validate({"ok": True, "events": store.events(job_id)})


@router.post("/jobs/{job_id}/retry", response_model=models.JobResponse, response_model_exclude_none=True)
def retry_job(job_id: str, request: Request, operator: OperatorMutation) -> models.JobResponse:
    store = runtime.job_store()
    original = store.get(job_id)
    # Setup and sign-in jobs act for a person. A retry acts for whoever retries
    # it; the app's recorded owner is kept by the onboarding store regardless.
    needs_identity = bool(
        original
        and original.get("action") in {"install", "retry_setup"}
        and operator.get("subject_id")
        and operator.get("email")
    )
    try:
        job = store.retry(
            job_id,
            actor=operator["username"],
            actor_subject=runtime.mutation_subject(operator),
            idempotency_key=runtime.idempotency_key(request),
            prepare=runtime.identity_for_job(operator) if needs_identity else None,
        )
    except KeyError as exc:
        raise ApiError(404, "job not found") from exc
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    return models.JobResponse.model_validate({"ok": True, "job": job})


@router.post("/jobs/{job_id}/cancel", response_model=models.JobResponse, response_model_exclude_none=True)
def cancel_job(job_id: str, operator: OperatorMutation) -> models.JobResponse:
    store = runtime.job_store()
    try:
        state = store.cancel(job_id, actor=operator["username"])
    except KeyError as exc:
        raise ApiError(404, "job not found") from exc
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    return models.JobResponse.model_validate({"ok": True, "state": state})


def _active_core_job(store: JobStore) -> dict[str, JsonValue] | None:
    return next(
        (job for job in store.active_jobs() if job["service_id"] == "core-suite" and job["state"] in ACTIVE), None
    )


@router.get("/setup/core", response_model=models.CoreSetupResponse, response_model_exclude_none=True)
def core_setup(_member: Member) -> models.CoreSetupResponse:
    """Describe the mandatory suite without claiming it is runnable early."""
    try:
        load_registry()
    except RegistryError as exc:
        return models.CoreSetupResponse.model_validate(
            {"ok": False, "ready_to_run": False, "error": str(exc), "services": []}
        )
    execution = records.get("status", "core") or {
        "ready": False,
        "services": [service.id for service in load_registry().services if service.stage == "core"],
        "missing": [],
        "capacity": {"ok": False, "reasons": ["Waiting for the worker to check this server."]},
    }
    store = JobStore.runtime()
    current_job = next(iter(store.jobs_for_service("core-suite", limit=1)), None) if store else None
    provisioning_store = ProvisioningStore.runtime()
    provisioned = {"available": True, **provisioning_store.summary(initialize=False)} if provisioning_store else None
    waiting = (provisioned or {}).get("waiting")
    waiting_for_provider = bool(
        current_job
        and current_job.get("state") == "waiting_for_confirmation"
        and waiting
        and waiting.get("phase_id") == "configuration"
    )
    return models.CoreSetupResponse.model_validate(
        {
            "ok": True,
            "ready_to_run": execution["ready"] and not waiting_for_provider,
            "services": execution["services"],
            "missing_manifests": execution["missing"],
            "capacity": execution["capacity"],
            "provisioning": provisioned,
            "current_job": current_job,
            "next_action": (
                "Add an inference provider to finish chat verification"
                if waiting_for_provider
                else "Set up the core application suite"
                if execution["ready"]
                else "Core service manifests are still being prepared"
            ),
        }
    )


@router.post("/jobs/core-install", response_model=models.JobResponse, response_model_exclude_none=True)
def start_core(request: Request, operator: OperatorMutation) -> models.JobResponse:
    """Start the one guided core-suite job for an Authentik operator."""
    store = runtime.job_store()
    key = runtime.idempotency_key(request)
    previous = store.by_idempotency_key(
        key or "",
        kind="lifecycle",
        service_id="core-suite",
        action="install",
        actor=operator["username"],
        actor_subject=runtime.mutation_subject(operator),
        namespace="core.install",
    )
    if previous:
        return models.JobResponse.model_validate({"ok": True, "duplicate": True, "job": previous})
    execution = records.get("status", "core") or {
        "ready": False,
        "services": [service.id for service in load_registry().services if service.stage == "core"],
        "missing": [],
        "capacity": {"ok": False, "reasons": ["Waiting for the worker to check this server."]},
    }
    if not execution["ready"]:
        raise ApiError(409, execution["error"], missing_manifests=execution["missing"])
    active = _active_core_job(store)
    if active:
        raise ApiError(409, "core setup is already running", job=active)
    job = start_core_setup(
        store,
        operator["username"],
        ROOT,
        idempotency_key=key,
        actor_subject=runtime.mutation_subject(operator),
        prepare=runtime.identity_for_job(operator) if operator.get("email") else None,
    )
    return models.JobResponse.model_validate({"ok": True, "job": job})


@router.post("/jobs/core-verify", response_model=models.JobResponse, response_model_exclude_none=True)
def verify_core(request: Request, operator: OperatorMutation) -> models.JobResponse:
    """Queue live contract checks without reinstalling healthy services."""
    store = runtime.job_store()
    previous = store.by_idempotency_key(
        runtime.idempotency_key(request) or "",
        kind="verification",
        service_id="core-suite",
        action="verify",
        actor=operator["username"],
        actor_subject=runtime.mutation_subject(operator),
        namespace="core.verify",
    )
    if previous:
        return models.JobResponse.model_validate({"ok": True, "duplicate": True, "job": previous})
    active = _active_core_job(store)
    if active:
        raise ApiError(409, "core work is already running", job=active)
    return models.JobResponse.model_validate(
        {
            "ok": True,
            "job": start_verify(
                store,
                operator["username"],
                runtime.idempotency_key(request),
                actor_subject=runtime.mutation_subject(operator),
            ),
        }
    )
