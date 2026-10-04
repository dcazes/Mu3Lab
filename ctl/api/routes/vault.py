"""Save Mu3Lab logins and provider sign-up entries into the owner's Vaultwarden."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ctl import browser_extension, vault_sync
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import IdentityData, Member, MemberMutation, OwnerMutation
from ctl.control_state import ControlState
from ctl.integrations.vaultwarden import VaultError, VaultSession
from ctl.jobs import JobStore
from ctl.platform_apps import by_capability
from ctl.runtime import RuntimePaths
from ctl.service_state import tailnet_dns_name
from ctl.store import onboarding as onboarding_state
from ctl.vault_setup import VAULTWARDEN_LOCAL_URL, SeedResult, desired_items, seed

router = APIRouter(prefix="/api/v1/vault", tags=["vault"], route_class=ContractRoute)

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}
_STATUS = {
    "invalid_input": 400,
    "invalid_credentials": 401,
    "two_factor_required": 401,
    "unreachable": 503,
}


def _host() -> str:
    try:
        return tailnet_dns_name()
    except (OSError, ValueError):
        return ""


def _run(owner: IdentityData, email: str, password: str, totp: str) -> SeedResult:
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


def _visible(status: object, identity: IdentityData) -> models.VaultAutomatic:
    """Administrators see everyone; a household member sees only themselves."""
    result = models.VaultAutomatic.model_validate(status)
    if not identity["is_admin"]:
        result.people = [person for person in result.people or [] if person.uid == identity["subject_id"]]
    return result


@router.post("/sync", response_model=models.VaultSyncResponse, response_model_exclude_none=True)
async def sync_now(_member: MemberMutation) -> models.VaultSyncResponse:
    """Save waiting logins to everyone's vault now instead of at the next automatic run."""

    result = await run_in_threadpool(vault_sync.run, lambda _line: None)
    if result is None:
        raise ApiError(503, "Authentik or Vaultwarden is not ready yet; Mu3Lab retries automatically.")
    return models.VaultSyncResponse.model_validate({"ok": True, "automatic": _visible(vault_sync.status(), _member)})


@router.get("/status", response_model=models.VaultStatus, response_model_exclude_none=True)
def vault_status(_operator: Member) -> models.VaultStatus:

    state = ControlState.runtime()
    return models.VaultStatus.model_validate(
        {
            "ok": True,
            # Logins are saved for everyone automatically; this is the last run.
            "automatic": _visible(vault_sync.status(), _operator),
            **(state.vault_seeded() if state else {"seeded": False, "seeded_at": ""}),
            "pending_logins": len(onboarding_state.pending_logins(str(_operator.get("subject_id") or ""))),
            "browser_extension": browser_extension.status(
                vault_database=RuntimePaths().data / by_capability("password_store").id / "db.sqlite3"
            ),
        }
    )


@router.post("/setup", response_model=models.VaultSetupResult, response_model_exclude_none=True)
async def setup_vault(
    payload_model: models.VaultSetupRequest, request: Request, owner: OwnerMutation
) -> models.VaultSetupResult | JSONResponse:
    """Use the master password for this one request only; it is never stored or logged."""
    payload = payload_model.model_dump()
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
    return JSONResponse(
        models.VaultSetupResult.model_validate({"ok": True, **result.public()}).model_dump(
            mode="json", exclude_none=True
        ),
        headers=_NO_STORE,
    )
