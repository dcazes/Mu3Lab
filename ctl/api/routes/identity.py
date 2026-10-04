"""The caller's identity and CSRF session."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ctl.api import models
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import (
    Identity,
    Member,
    Owner,
    OwnerMutation,
    csrf_token,
)
from ctl.store import checklist

router = APIRouter(prefix="/api/v1", tags=["identity"], route_class=ContractRoute)

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


@router.get("/identity", response_model=models.IdentityResponse, response_model_exclude_none=True)
def identity(value: Identity) -> models.IdentityResponse:
    return models.IdentityResponse.model_validate(value)


@router.get("/session", response_model=models.SessionResponse, response_model_exclude_none=True)
def session(request: Request, _operator: Member) -> models.SessionResponse:
    """Issue browser-readable CSRF material only after verified proxy identity."""
    token = csrf_token(request)
    if not token:
        raise ApiError(403, "operator session required")
    return models.SessionResponse.model_validate({"ok": True, "csrf_token": token})


@router.get("/me/checklist", response_model=models.ChecklistResponse)
def get_checklist(person: Owner) -> models.ChecklistResponse:
    return models.ChecklistResponse.model_validate({"ok": True, "items": checklist.read(str(person["subject_id"]))})


@router.put("/me/checklist", response_model=models.ChecklistResponse)
def put_checklist(payload: models.ChecklistRequest, person: OwnerMutation) -> models.ChecklistResponse:
    return models.ChecklistResponse.model_validate(
        {
            "ok": True,
            "items": checklist.update(
                str(person["subject_id"]), {str(key): value for key, value in payload.items.items()}
            ),
        }
    )
