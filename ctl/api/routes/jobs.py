"""Durable jobs, the audit log, and the guided core-suite setup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import Operator, OperatorMutation
from ctl.core_setup import CORE_ORDER, start_verify
from ctl.core_setup import plan as core_plan
from ctl.core_setup import start as start_core_setup
from ctl.jobs import JobStore
from ctl.provisioning import ProvisioningStore
from ctl.registry import RegistryError
from ctl.registry import load as load_registry

ROOT = Path(__file__).resolve().parents[3]
ACTIVE = frozenset({"queued", "running"})

router = APIRouter(prefix="/api/v1", tags=["jobs"])


@router.get("/jobs")
def list_jobs() -> dict[str, Any]:
    store = JobStore.runtime()
    if store is None:
        return {"ok": True, "available": False, "jobs": []}
    return {"ok": True, "available": True, "jobs": store.jobs()}


@router.get("/audit")
def audit() -> dict[str, Any]:
    """Append-only, secret-redacted audit metadata."""
    store = JobStore.runtime()
    if store is None:
        return {"ok": True, "available": False, "events": []}
    return {"ok": True, "available": True, "events": store.audit()}


def _recent_job(store: JobStore, job_id: str) -> dict[str, Any]:
    # By primary key: a job stays reachable however many newer jobs exist.
    job = store.get(job_id)
    if not job:
        raise ApiError(404, "job not found")
    return job


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, _operator: Operator) -> dict[str, Any]:
    store = runtime.job_store()
    return {"ok": True, "job": _recent_job(store, job_id), "events": store.events(job_id)}


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, _operator: Operator) -> dict[str, Any]:
    store = runtime.job_store()
    _recent_job(store, job_id)
    return {"ok": True, "events": store.events(job_id)}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request, operator: OperatorMutation) -> dict[str, Any]:
    store = runtime.job_store()
    original = store.get(job_id)
    # Setup and sign-in jobs act for a person. A retry acts for whoever retries
    # it; the app's recorded owner is kept by the onboarding store regardless.
    needs_identity = bool(
        original
        and original.get("action") in {"install", "retry_setup", "configure_identity"}
        and operator.get("subject_id")
        and operator.get("email")
    )
    try:
        job = store.retry(
            job_id,
            actor=operator["username"],
            idempotency_key=runtime.idempotency_key(request),
            prepare=runtime.identity_for_job(operator) if needs_identity else None,
        )
    except KeyError as exc:
        raise ApiError(404, "job not found") from exc
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    return {"ok": True, "job": job}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, operator: OperatorMutation) -> dict[str, Any]:
    store = runtime.job_store()
    try:
        store.cancel(job_id, actor=operator["username"])
    except KeyError as exc:
        raise ApiError(404, "job not found") from exc
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    return {"ok": True, "state": "cancelled"}


def _active_core_job(store: JobStore) -> dict[str, Any] | None:
    return next(
        (job for job in store.active_jobs() if job["service_id"] == "core-suite" and job["state"] in ACTIVE), None
    )


@router.get("/setup/core")
def core_setup() -> dict[str, Any]:
    """Describe the mandatory suite without claiming it is runnable early."""
    try:
        load_registry()
    except RegistryError as exc:
        return {"ok": False, "ready_to_run": False, "error": str(exc), "services": []}
    execution = core_plan(ROOT)
    store = JobStore.runtime()
    current_job = next(iter(store.jobs_for_service("core-suite", limit=1)), None) if store else None
    provisioning_store = ProvisioningStore.runtime()
    if provisioning_store:
        provisioning_store.reconcile_runtime()
    provisioned = provisioning_store.summary() if provisioning_store else None
    waiting = (provisioned or {}).get("waiting")
    waiting_for_provider = bool(
        current_job
        and current_job.get("state") == "waiting_for_confirmation"
        and waiting
        and waiting.get("phase_id") == "configuration"
    )
    return {
        "ok": True,
        "ready_to_run": execution["ready"] and not waiting_for_provider,
        "services": list(CORE_ORDER),
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


@router.post("/jobs/core-install")
def start_core(request: Request, operator: OperatorMutation) -> dict[str, Any]:
    """Start the one guided core-suite job for an Authentik operator."""
    execution = core_plan(ROOT)
    if not execution["ready"]:
        raise ApiError(409, execution["error"], missing_manifests=execution["missing"])
    store = runtime.job_store()
    key = runtime.idempotency_key(request)
    previous = store.by_idempotency_key(key or "")
    if previous:
        return {"ok": True, "duplicate": True, "job": previous}
    active = _active_core_job(store)
    if active:
        raise ApiError(409, "core setup is already running", job=active)
    job = start_core_setup(store, operator["username"], ROOT, idempotency_key=key)
    if operator.get("subject_id") and operator.get("email"):
        runtime.hand_identity_to_job(
            store,
            job,
            operator,
            detail="Encrypted account-bootstrap storage is unavailable.",
            error_code="bootstrap_contract_unavailable",
            step_id="account_preflight",
        )
    return {"ok": True, "job": job}


@router.post("/jobs/core-verify")
def verify_core(request: Request, operator: OperatorMutation) -> dict[str, Any]:
    """Queue live contract checks without reinstalling healthy services."""
    store = runtime.job_store()
    active = _active_core_job(store)
    if active:
        raise ApiError(409, "core work is already running", job=active)
    return {"ok": True, "job": start_verify(store, operator["username"], runtime.idempotency_key(request))}
