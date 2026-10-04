"""The caller's identity and CSRF session."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ctl.api.errors import ApiError
from ctl.api.security import (
    Identity,
    Member,
    csrf_token,
)

router = APIRouter(prefix="/api/v1", tags=["identity"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


@router.get("/identity")
def identity(value: Identity) -> dict[str, Any]:
    return value


@router.get("/session")
def session(request: Request, _operator: Member) -> dict[str, Any]:
    """Issue browser-readable CSRF material only after verified proxy identity."""
    token = csrf_token(request)
    if not token:
        raise ApiError(403, "operator session required")
    return {"ok": True, "csrf_token": token}
