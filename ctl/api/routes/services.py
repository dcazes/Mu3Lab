"""Curated service status, lifecycle actions, configuration, and logs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import actions, onboarding_state, service_config
from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import (
    AdminVerifiedAccount,
    Identity,
    IdentityData,
    Member,
    MemberMutation,
    Operator,
    OperatorMutation,
    VerifiedAccount,
    job_identity,
)
from ctl.api.service_view import ACTIVE_WORKFLOW_STATES, INSTALLED_STATES, service_snapshot
from ctl.backups import readiness as backup_readiness
from ctl.control_state import ControlState
from ctl.identity import mode_for
from ctl.jobs import JobStore, redact
from ctl.releases import latest as latest_release
from ctl.runtime import RuntimePaths
from ctl.service_ops import SUPPORTED_ACTIONS, UNINSTALL_ACTIONS, allowed_actions, project_path
from ctl.service_state import status as service_status
from ctl.service_state import tailnet_dns_name

ROOT = Path(__file__).resolve().parents[3]
MANUAL_INITIALIZATION_MODES = frozenset(
    {"oidc_first_login", "browser_registration", "local_account_manual", "manual_owner", "trusted_header"}
)

router = APIRouter(prefix="/api/v1/services", tags=["services"])


@router.get("")
def list_services(identity: Identity) -> dict[str, Any]:
    return service_snapshot(identity)


@router.post("/onboarding/resume")
def resume_onboarding(operator: VerifiedAccount) -> dict[str, Any]:
    """Adopt pending pre-upgrade identity setup; the worker verifies real app evidence."""
    control = runtime.control_state()
    store = runtime.job_store()
    active = {job["service_id"] for job in store.active_jobs()}
    adopted = []
    for service in runtime.registry().services:
        if mode_for(service) != "native_oidc":
            continue
        if (control.installation(service.id) or {}).get("state") != "running":
            continue
        saved = control.service_identity(service.id) or {}
        if saved.get("state") not in {None, "unconfigured", "migration_required", "ready"} or service.id in active:
            continue
        record = onboarding_state.read(service.id)
        if record.get("config_version") == onboarding_state.CONFIG_VERSION:
            continue
        owner_uid = (
            record.get("owner", {}).get("owner_uid")
            or saved.get("owner_uid")
            or (control.initialization(service.id) or {}).get("owner_uid")
        )
        if owner_uid == operator["subject_id"]:
            # Upgrade only the installing owner's apps. Unknown legacy ownership
            # requires the existing explicit sign-in reconciliation action.
            onboarding_state.remember_owner(service.id, job_identity(operator))
            job = store.create(
                kind="lifecycle",
                service_id=service.id,
                action="configure_identity",
                actor=operator["username"],
                detail="Applying automatic sign-in setup to the existing installation.",
            )
            control.set_service_identity(
                service.id,
                "native_oidc",
                "configuring",
                owner_uid=owner_uid,
                job_id=job["id"],
                detail="Updating first-use sign-in automatically.",
            )
            adopted.append(service.id)
    return {"ok": True, "adopted": adopted}


def _effective_state(service: Any, control: ControlState | None) -> str:
    current_state = str(service_status(service, tailnet_dns_name(), ROOT)["state"])
    installed = control.installation(service.id) if control else None
    if installed and str(installed["state"]) in ACTIVE_WORKFLOW_STATES:
        current_state = str(installed["state"])
    elif installed and str(installed["state"]) == "failed" and current_state not in INSTALLED_STATES:
        current_state = "failed"
    if service_config.missing_required(service):
        current_state = "config_required"
    return current_state


# Household members may add apps; running, stopping and removing them is administration.
MEMBER_ACTIONS = frozenset({"install", "retry_setup"})


@router.post("/{service_id}/actions")
async def service_action(service_id: str, request: Request, operator: MemberMutation) -> dict[str, Any]:
    """Queue one allowlisted service lifecycle action for the durable worker."""
    body = await runtime.json_body(request)
    # State checks probe apps and read stores; keep them off the event loop.
    return await run_in_threadpool(_queue_service_action, service_id, body, request, operator)


def _queue_service_action(service_id: str, body: dict, request: Request, operator: IdentityData) -> dict[str, Any]:
    action = str(body.get("action", ""))
    if action not in MEMBER_ACTIONS and not operator.get("is_admin"):
        raise ApiError(403, "Only a Mu3Lab administrator can do this.", code="admin_required")
    if action not in SUPPORTED_ACTIONS:
        raise ApiError(400, "unsupported service action")
    service = runtime.service(service_id)
    if service.is_blocked:
        raise ApiError(409, service.blocked_reason)
    # Deleting data cannot be undone: the request must repeat the app's name,
    # so a replayed or scripted "uninstall" can never escalate to it.
    if action == "uninstall_delete_data" and str(body.get("confirm", "")).strip() != service.name:
        raise ApiError(400, f"type {service.name} to confirm deleting its data")
    provisions_account = action in {"install", "retry_setup"}
    if (
        provisions_account
        and (service.account.get("mode") in {"environment_bootstrap", "api_bootstrap"} or service.id == "actual-budget")
        and (not operator.get("subject_id") or not operator.get("email"))
    ):
        raise ApiError(
            409,
            {
                "code": "identity_email_missing",
                "stage": "account_preflight",
                "message": "A verified Authentik email is required. Sign out, sign back in, and retry.",
                "retryable": True,
                "recommended_action": "Refresh the Authentik session and retry installation.",
            },
        )
    if action == "stop" and service.lifecycle == "always_on":
        raise ApiError(409, "always-on infrastructure cannot be stopped")
    if not (service.compose_path(ROOT) / "docker-compose.yml").is_file():
        raise ApiError(409, "service manifest is not deployable")
    control = ControlState.runtime()
    required = "uninstall" if action in UNINSTALL_ACTIONS else action
    if required not in allowed_actions(service, _effective_state(service, control)):
        raise ApiError(409, "action is not valid for the current service state")
    store = runtime.job_store()
    key = runtime.idempotency_key(request)
    previous = store.by_idempotency_key(key or "")
    if previous:
        return {"ok": True, "duplicate": True, "job": previous}
    try:
        job = store.create(
            kind="lifecycle",
            service_id=service.id,
            action=action,
            actor=operator["username"],
            detail=f"Operator requested {action} for {service.name}.",
            idempotency_key=key,
            prepare=runtime.identity_for_job(operator)
            if provisions_account and operator.get("subject_id") and operator.get("email")
            else None,
        )
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    if control and job.get("state") == "queued":
        queued_state = "uninstalling" if action in UNINSTALL_ACTIONS else "queued"
        control.set_installation(service.id, queued_state, job_id=str(job["id"]))
    return {"ok": True, "job": job}


@router.post("/{service_id}/identity/reconcile")
def reconcile_service_identity(service_id: str, request: Request, operator: AdminVerifiedAccount) -> dict[str, Any]:
    """Queue bounded identity configuration without disguising it as repair."""
    service = runtime.service(service_id)
    if service.is_blocked:
        raise ApiError(409, service.blocked_reason)
    store = runtime.job_store()
    control = runtime.control_state()
    active = next(
        (
            job
            for job in store.active_jobs()
            if job["service_id"] == service_id
            and job["action"] == "configure_identity"
            and job["state"] in {"queued", "running"}
        ),
        None,
    )
    if active:
        return {"ok": True, "duplicate": True, "job": active}
    job = store.create(
        kind="lifecycle",
        service_id=service_id,
        action="configure_identity",
        actor=str(operator["username"]),
        detail=f"Operator requested sign-in reconciliation for {service.name}.",
        idempotency_key=runtime.idempotency_key(request),
        prepare=runtime.identity_for_job(operator),
    )
    control.set_service_identity(
        service_id,
        mode_for(service),
        "configuring",
        owner_uid=str(operator["subject_id"]),
        job_id=str(job["id"]),
        detail="Sign-in reconciliation is queued.",
    )
    return {"ok": True, "job": job}


@router.get("/{service_id}/logs")
def service_logs(service_id: str, _operator: Operator, tail: int = 120, container: str = "") -> JSONResponse:
    """Bounded, redacted logs for a curated Compose service."""
    service = runtime.service(service_id)
    project = project_path(service, ROOT)
    compose_path = project / "docker-compose.yml"
    if not compose_path.is_file():
        raise ApiError(409, "service manifest is not deployable")
    try:
        definition = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        known = set((definition.get("services") or {}).keys())
    except (OSError, yaml.YAMLError, AttributeError):
        known = set()
    if container and container not in known:
        raise ApiError(400, "unknown service container")
    rc, output = actions.compose_logs(project, lambda _line: None, tail=max(20, min(tail, 500)), container=container)
    return JSONResponse(
        {
            "ok": rc == 0,
            "service_id": service.id,
            "container": container,
            "lines": [redact(line) for line in output[-40000:].splitlines()],
        },
        status_code=200 if rc == 0 else 500,
    )


@router.post("/{service_id}/initialization/confirm")
def confirm_service_initialization(service_id: str, operator: OperatorMutation) -> dict[str, Any]:
    service = runtime.service(service_id)
    mode = str(service.account.get("mode", "none"))
    if mode not in MANUAL_INITIALIZATION_MODES:
        raise ApiError(409, "this application does not use manual initialization")
    control = runtime.control_state()
    installation = control.installation(service.id)
    if not installation or installation["state"] not in {"running", "stopped", "degraded"}:
        raise ApiError(409, "install the application before confirming setup")
    result = control.set_initialization(
        service.id,
        mode,
        "ready",
        job_id=str(installation.get("last_job_id", "")),
        owner_uid=str(operator.get("subject_id") or ""),
    )
    store = JobStore.runtime()
    if store:
        store.record_audit(
            actor=str(operator["username"]),
            event="service.initialization.confirmed",
            detail=f"Operator confirmed supported first-user setup for {service.id}.",
        )
    return {"ok": True, "initialization": result}


def _managed_keys(service: Any) -> set[str]:
    return {str(field["key"]) for field in service.configuration if field.get("managed")}


@router.get("/{service_id}/configuration")
def get_service_configuration(service_id: str, _operator: Member) -> dict[str, Any]:
    service = runtime.service(service_id)
    managed = _managed_keys(service)
    return {
        "ok": True,
        "service_id": service.id,
        "fields": [field for field in service_config.read(service) if str(field["key"]) not in managed],
    }


@router.put("/{service_id}/configuration")
async def put_service_configuration(service_id: str, request: Request, operator: OperatorMutation) -> dict[str, Any]:
    values = (await runtime.json_body(request)).get("values", {})
    return await run_in_threadpool(_write_service_configuration, service_id, values, operator)


def _write_service_configuration(service_id: str, values: Any, operator: IdentityData) -> dict[str, Any]:
    service = runtime.service(service_id)
    managed = _managed_keys(service)
    if isinstance(values, dict) and set(values).intersection(managed):
        raise ApiError(422, "managed account fields cannot be changed here")
    try:
        fields = [field for field in service_config.write(service, values) if str(field["key"]) not in managed]
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        raise ApiError(422, str(exc)) from exc
    store = JobStore.runtime()
    if store:
        store.record_audit(
            actor=operator["username"],
            event="service.configuration.changed",
            detail=f"Typed configuration updated for {service.id}.",
        )
    return {
        "ok": True,
        "service_id": service.id,
        "fields": fields,
        "restart_required": (RuntimePaths().projects / service.id / "docker-compose.yml").is_file(),
    }


@router.get("/{service_id}/updates")
def service_updates(service_id: str, _operator: Member) -> dict[str, Any]:
    """Check the curated upstream repository for its latest stable release."""
    service = runtime.service(service_id)
    repository = str(service.update.get("repository", ""))
    if not repository:
        raise ApiError(409, "no reviewed upstream release source")
    try:
        release = latest_release(repository, str(service.update.get("current_version", "")))
    except (ValueError, RuntimeError) as exc:
        raise ApiError(503, str(exc)) from exc
    reason = (
        "A verified service snapshot is required before updating."
        if backup_readiness().get("state") != "verified"
        else service.blocked_reason
        if service.is_blocked
        else "Update execution remains disabled until the restore executor is available."
    )
    return {"ok": True, **release, "update_enabled": False, "blocked_reason": reason}
