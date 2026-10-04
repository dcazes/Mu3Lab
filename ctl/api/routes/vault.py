"""Save Mu3Lab logins and provider sign-up entries into the owner's Vaultwarden."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import browser_extension
from ctl.api import runtime
from ctl.api.errors import ApiError
from ctl.api.security import Member, MemberMutation, OwnerMutation
from ctl.control_state import ControlState
from ctl.integrations.vaultwarden import VaultError, VaultSession
from ctl.jobs import JobStore
from ctl.platform_apps import by_capability
from ctl.runtime import RuntimePaths
from ctl.store import onboarding as onboarding_state
from ctl.vault_setup import VAULTWARDEN_LOCAL_URL, SeedResult, desired_items, seed

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


def _visible(status: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    """Administrators see everyone; a household member sees only themselves."""
    if identity.get("is_admin"):
        return status
    mine = [person for person in status.get("people") or [] if person.get("uid") == identity.get("subject_id")]
    return {**status, "people": mine}


@router.post("/sync")
async def sync_now(_member: MemberMutation) -> dict[str, Any]:
    """Save waiting logins to everyone's vault now instead of at the next automatic run."""
    from ctl import vault_sync

    result = await run_in_threadpool(vault_sync.run, lambda _line: None)
    if result is None:
        raise ApiError(503, "Authentik or Vaultwarden is not ready yet; Mu3Lab retries automatically.")
    return {"ok": True, "automatic": _visible(vault_sync.status(), _member)}


@router.get("/status")
def vault_status(_operator: Member) -> dict[str, Any]:
    from ctl import vault_sync

    state = ControlState.runtime()
    return {
        "ok": True,
        # Logins are saved for everyone automatically; this is the last run.
        "automatic": _visible(vault_sync.status(), _operator),
        **(state.vault_seeded() if state else {"seeded": False, "seeded_at": ""}),
        "pending_logins": len(onboarding_state.pending_logins(str(_operator.get("subject_id") or ""))),
        "browser_extension": browser_extension.status(
            vault_database=RuntimePaths().data / by_capability("password_store").id / "db.sqlite3"
        ),
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
    for service_id in result.saved_onboarding:
        onboarding_state.vault_saved(service_id, owner_uid)
    state = ControlState.runtime()
    if state:
        state.mark_vault_seeded(str(owner["username"]))
    store = JobStore.runtime()
    if store:
        store.record_audit(
            actor=str(owner["username"]),
            event="vault.seeded",
            detail=f"Saved {len(result.created)} new and {len(result.updated)} updated logins to Vaultwarden.",
        )
    return JSONResponse({"ok": True, **result.public()}, headers=_NO_STORE)
