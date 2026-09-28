"""The caller's identity, CSRF session, and one-time credential handoffs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ctl import workflow_secrets
from ctl.api.errors import ApiError
from ctl.api.security import Identity, Operator, Owner, OwnerMutation, csrf_token, mutation_allowed, resolve_identity
from ctl.control_state import ControlState
from ctl.jobs import JobStore

router = APIRouter(prefix="/api/v1", tags=["identity"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


@router.get("/identity")
def identity(value: Identity) -> dict[str, Any]:
    return value


@router.get("/session")
def session(request: Request, _operator: Operator) -> dict[str, Any]:
    """Issue browser-readable CSRF material only after verified proxy identity."""
    token = csrf_token(request)
    if not token:
        raise ApiError(403, "operator session required")
    return {"ok": True, "csrf_token": token}


@router.get("/credential-handoffs")
def credential_handoffs(owner: Owner) -> dict[str, Any]:
    owner_uid = str(owner["subject_id"])
    state = ControlState.runtime()
    expired = workflow_secrets.cleanup()
    if state:
        state.expire_handoffs(expired)
    secret_metadata = {item["id"]: item for item in workflow_secrets.metadata(owner_uid)}
    records = [
        {**item, **secret_metadata[item["id"]]}
        for item in (state.handoffs(owner_uid) if state else [])
        if item["state"] == "available" and item["id"] in secret_metadata
    ]
    return {"ok": True, "handoffs": records}


@router.post("/credential-handoffs/{handoff_id}/reveal")
def reveal_credential(handoff_id: str, request: Request) -> JSONResponse:
    # Checked inline so that even refusals carry no-store headers.
    owner = resolve_identity(request)
    owner_uid = str(owner.get("subject_id") or "")
    if not owner["writes_enabled"] or not owner_uid or not mutation_allowed(request):
        raise ApiError(403, "operator mutation verification failed", headers=_NO_STORE)
    credential = workflow_secrets.reveal(handoff_id, owner_uid)
    if not credential:
        raise ApiError(404, "credential is unavailable or expired", headers=_NO_STORE)
    return JSONResponse({"ok": True, "credential": credential}, headers=_NO_STORE)


@router.post("/credential-handoffs/{handoff_id}/confirm")
def confirm_credential(handoff_id: str, owner: OwnerMutation) -> dict[str, Any]:
    owner_uid = str(owner["subject_id"])
    state = ControlState.runtime()
    if not state or not state.confirm_handoff(handoff_id, owner_uid):
        raise ApiError(404, "credential handoff not found")
    workflow_secrets.delete(handoff_id, owner_uid)
    store = JobStore.runtime()
    if store:
        store.record_audit(
            actor=str(owner["username"]),
            event="credential_handoff.confirmed",
            detail="Generated application credential was confirmed saved and removed.",
        )
    return {"ok": True, "state": "confirmed"}
