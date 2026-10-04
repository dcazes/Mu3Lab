"""Curated service status, lifecycle actions, configuration, and logs."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import actions, service_config
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import (
    Identity,
    IdentityData,
    Member,
    MemberMutation,
    Operator,
    OperatorMutation,
)
from ctl.api.service_view import ACTIVE_WORKFLOW_STATES, INSTALLED_STATES, service_snapshot
from ctl.backups import BackupError
from ctl.backups import readiness as backup_readiness
from ctl.backups import snapshots as list_backups
from ctl.control_state import ControlState
from ctl.jobs import JobStore, redact
from ctl.lifecycle import app_releases
from ctl.lifecycle.maintenance import MAINTENANCE_ACTIONS
from ctl.registry import Service
from ctl.rules import rules_for
from ctl.runtime import RuntimePaths
from ctl.service_ops import SUPPORTED_ACTIONS, UNINSTALL_ACTIONS, allowed_actions, project_path
from ctl.service_state import status as service_status
from ctl.service_state import tailnet_dns_name

ROOT = Path(__file__).resolve().parents[3]
MANUAL_INITIALIZATION_MODES = frozenset(
    {"oidc_first_login", "browser_registration", "local_account_manual", "manual_owner", "trusted_header"}
)

# An app must be installed (running or stopped) to be backed up, restored or updated.
MAINTENANCE_STATES = frozenset({"ready", "needs_setup", "needs_attention", "stopped"})
SNAPSHOT_ID = re.compile(r"^[0-9a-f]{8,64}$")

router = APIRouter(prefix="/api/v1/services", tags=["services"], route_class=ContractRoute)


@router.get("", response_model=models.ServicesResponse, response_model_exclude_none=True)
def list_services(identity: Identity) -> models.ServicesResponse:
    return models.ServicesResponse.model_validate(service_snapshot(identity))


def _effective_state(service: Service, control: ControlState | None) -> str:
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


@router.post("/{service_id}/actions", response_model=models.JobResponse, response_model_exclude_none=True)
async def service_action(
    payload_model: models.ServiceActionRequest, service_id: str, request: Request, operator: MemberMutation
) -> models.JobResponse:
    """Queue one allowlisted service lifecycle action for the durable worker."""
    body = payload_model.model_dump()
    # State checks probe apps and read stores; keep them off the event loop.
    return models.JobResponse.model_validate(
        await run_in_threadpool(_queue_service_action, service_id, body, request, operator)
    )


def _queue_service_action(service_id: str, body: dict, request: Request, operator: IdentityData) -> dict[str, object]:
    action = str(body.get("action", ""))
    if action not in MEMBER_ACTIONS and not operator.get("is_admin"):
        raise ApiError(403, "Only a Mu3Lab administrator can do this.", code="admin_required")
    if action not in SUPPORTED_ACTIONS:
        raise ApiError(400, "unsupported service action")
    service = runtime.service(service_id)
    # Deleting data cannot be undone: the request must repeat the app's name,
    # so a replayed or scripted "uninstall" can never escalate to it.
    if action == "uninstall_delete_data" and str(body.get("confirm", "")).strip().casefold() != service.name.casefold():
        raise ApiError(400, f"type {service.name} to confirm deleting its data")
    provisions_account = action in {"install", "retry_setup"}
    if (
        provisions_account
        and (
            (
                service.account.get("mode") in {"environment_bootstrap", "api_bootstrap"}
                and service.manifest.account.needs_owner
            )
            or any(rule.needs_owner for rule in rules_for(service.manifest))
        )
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
    params: dict[str, str] = {}
    if action in MAINTENANCE_ACTIONS:
        params = _maintenance_params(service, action, body, _effective_state(service, control))
    else:
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
            params=params,
        )
    except ValueError as exc:
        raise ApiError(409, str(exc)) from exc
    if action in MAINTENANCE_ACTIONS:
        return {"ok": True, "job": job}
    if control and job.get("state") == "queued":
        queued_state = "uninstalling" if action in UNINSTALL_ACTIONS else "queued"
        control.set_installation(service.id, queued_state, job_id=str(job["id"]))
    return {"ok": True, "job": job}


def _maintenance_params(service: Service, action: str, body: dict, state: str) -> dict[str, str]:
    """Check a backup, restore or update request before it is queued."""
    if service.stage != "optional":
        raise ApiError(409, "Only apps installed from the catalog can be backed up, restored or updated here.")
    if state not in MAINTENANCE_STATES:
        raise ApiError(409, "Finish setting up the app first.")
    if action == "backup":
        return {}
    if action == "restore":
        # Restoring replaces the app's current data: repeat its name, as for deleting data.
        if str(body.get("confirm", "")).strip() != service.name:
            raise ApiError(400, f"type {service.name} to confirm replacing its data")
        snapshot_id = str(body.get("snapshot_id", ""))
        if not SNAPSHOT_ID.fullmatch(snapshot_id):
            raise ApiError(400, "choose a backup to restore")
        return {"snapshot_id": snapshot_id}
    # The target is the release checked in as approved, never one from the browser.
    release = _release(service)
    if not release["update_available"]:
        raise ApiError(409, f"{service.name} is already on the release Mu3Lab approves.")
    if not release["update_enabled"]:
        raise ApiError(409, str(release["blocked_reason"]))
    return {"target_version": str(release["approved_version"])}


@router.get("/{service_id}/logs", response_model=models.ServiceLogsResponse, response_model_exclude_none=True)
def service_logs(
    service_id: str, _operator: Operator, tail: int = 120, container: str = ""
) -> models.ServiceLogsResponse | JSONResponse:
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
        models.ServiceLogsResponse.model_validate(
            {
                "ok": rc == 0,
                "service_id": service.id,
                "container": container,
                "lines": [redact(line) for line in output[-40000:].splitlines()],
            }
        ).model_dump(mode="json", exclude_none=True),
        status_code=200 if rc == 0 else 500,
    )


@router.post(
    "/{service_id}/initialization/confirm",
    response_model=models.ServiceInitializationResponse,
    response_model_exclude_none=True,
)
def confirm_service_initialization(service_id: str, operator: OperatorMutation) -> models.ServiceInitializationResponse:
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
    return models.ServiceInitializationResponse.model_validate({"ok": True, "initialization": result})


def _managed_keys(service: Service) -> set[str]:
    return {str(field["key"]) for field in service.configuration if field.get("managed")}


@router.get(
    "/{service_id}/configuration", response_model=models.ServiceConfigResponse, response_model_exclude_none=True
)
def get_service_configuration(service_id: str, _operator: Member) -> models.ServiceConfigResponse:
    service = runtime.service(service_id)
    managed = _managed_keys(service)
    return models.ServiceConfigResponse.model_validate(
        {
            "ok": True,
            "service_id": service.id,
            "fields": [field for field in service_config.read(service) if str(field["key"]) not in managed],
        }
    )


@router.put(
    "/{service_id}/configuration", response_model=models.ServiceConfigResponse, response_model_exclude_none=True
)
async def put_service_configuration(
    payload_model: models.ConfigurationRequest, service_id: str, request: Request, operator: OperatorMutation
) -> models.ServiceConfigResponse:
    values = (payload_model.model_dump()).get("values", {})
    return models.ServiceConfigResponse.model_validate(
        await run_in_threadpool(_write_service_configuration, service_id, values, operator)
    )


def _write_service_configuration(
    service_id: str, values: dict[str, str | bool | int | None], operator: IdentityData
) -> dict[str, object]:
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


def _release(service: Service) -> dict[str, object]:
    """The release Mu3Lab approves for this app, and whether this machine can move to it."""
    if service.stage != "optional":
        approved = str(service.update.get("approved_version", ""))
        release: dict[str, object] = {
            "installed_version": approved,
            "approved_version": approved,
            "update_available": False,
            "supporting_only": False,
        }
    else:
        release = app_releases.status(service, ROOT)
    reason = ""
    if service.stage != "optional":
        reason = f"{service.name} is part of Mu3Lab itself and is updated together with Mu3Lab."
    elif _effective_state(service, runtime.control_state()) not in MAINTENANCE_STATES:
        reason = f"Install {service.name} before updating it."
    elif not backup_readiness().get("available"):
        reason = "Updates need Docker to save a backup first."
    repository = str(service.update.get("repository", ""))
    notes = (
        f"https://github.com/{repository}/releases/tag/{release['approved_version']}"
        if repository and release["approved_version"]
        else ""
    )
    return {
        **release,
        "repository": repository,
        "release_url": notes,
        "update_enabled": release["update_available"] and not reason,
        "blocked_reason": reason,
    }


@router.get("/{service_id}/updates", response_model=models.UpdateResponse, response_model_exclude_none=True)
def service_updates(service_id: str, _operator: Member) -> models.UpdateResponse:
    """Compare the installed release with the one Mu3Lab approves; no network needed."""
    return models.UpdateResponse.model_validate({"ok": True, **_release(runtime.service(service_id))})


@router.get("/{service_id}/backups", response_model=models.BackupsResponse, response_model_exclude_none=True)
def service_backups(service_id: str, _operator: Operator) -> models.BackupsResponse:
    """The app's local backups, newest first."""
    service = runtime.service(service_id)
    if service.stage != "optional":
        raise ApiError(409, "Only apps installed from the catalog are backed up here.")
    try:
        snapshots = list_backups(service.id)
    except BackupError as exc:
        raise ApiError(503, str(exc)) from exc
    return models.BackupsResponse.model_validate(
        {"ok": True, "service_id": service.id, "backups": snapshots, "readiness": backup_readiness()}
    )
