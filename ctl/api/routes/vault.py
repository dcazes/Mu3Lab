"""Save Mu3Lab logins and provider sign-up entries into the owner's Vaultwarden."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import browser_extension, workflow_secrets
from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import Operator, OwnerMutation
from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.vault_setup import VAULTWARDEN_LOCAL_URL, SeedResult, desired_items, seed
from ctl.vaultwarden_api import VaultError, VaultSession

router = APIRouter(prefix="/api/v1/vault", tags=["vault"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}
_STATUS = {
    "invalid_input": 400,
    "invalid_credentials": 401,
    "two_factor_required": 401,
    "unreachable": 503,
}


def _host() -> str:
    try:
        from ctl.service_state import tailnet_dns_name

        return tailnet_dns_name()
    except (OSError, ValueError):
        return ""


def _run(owner: dict[str, Any], email: str, password: str, totp: str) -> SeedResult:
    items = desired_items(
        registry=runtime.registry(),
        host=_host(),
        owner_uid=str(owner["subject_id"]),
        username=str(owner["username"]),
        email=str(owner.get("email") or email),
    )
    with VaultSession(VAULTWARDEN_LOCAL_URL) as session:
        session.login(email, password, totp=totp)
        return seed(session, items)


@router.get("/status")
def vault_status(_operator: Operator) -> dict[str, Any]:
    state = ControlState.runtime()
    return {
        "ok": True,
        **(state.vault_seeded() if state else {"seeded": False, "seeded_at": ""}),
        "browser_extension": browser_extension.status(),
    }


@router.post("/setup")
async def setup_vault(request: Request, owner: OwnerMutation) -> JSONResponse:
    """Use the master password for this one request only; it is never stored or logged."""
    payload = await runtime.json_body(request)
    email = str(payload.get("email", "")).strip()
    password = str(payload.get("master_password", ""))
    totp = str(payload.get("totp", "")).strip()
    try:
        result = await run_in_threadpool(_run, owner, email, password, totp)
    except VaultError as exc:
        raise ApiError(_STATUS.get(exc.code, 502), str(exc), headers=_NO_STORE, code=exc.code) from exc
    owner_uid = str(owner["subject_id"])
    state = ControlState.runtime()
    if state:
        state.mark_vault_seeded(str(owner["username"]))
    for handoff_id in result.saved_handoffs:
        if state:
            state.confirm_handoff(handoff_id, owner_uid)
        workflow_secrets.delete(handoff_id, owner_uid)
    store = JobStore.runtime()
    if store:
        store.record_audit(
            actor=str(owner["username"]),
            event="vault.seeded",
            detail=f"Saved {len(result.created)} new and {len(result.updated)} updated logins to Vaultwarden.",
        )
    return JSONResponse({"ok": True, **result.public()}, headers=_NO_STORE)
