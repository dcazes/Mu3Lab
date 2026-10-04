"""Request identity and mutation checks for the control-plane API.

The control plane listens on loopback only. Caddy forwards Authentik's identity
headers along with a shared proxy token; headers without that token are
ignored, so a browser can never forge an identity. Every state-changing request
additionally needs a same-origin HTTPS `Origin` and a CSRF token bound to the
caller's Authentik session.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, Request

from ctl.api.errors import ApiError
from ctl.secrets import platform_values
from ctl.store.workflows import JobIdentity

ROOT = Path(__file__).resolve().parents[2]
OPERATOR_GROUPS = frozenset({"mu3lab-operators", "authentik Admins"})
# Household members use the dashboard and every app; administration stays with operators.
HOUSEHOLD_GROUP = "mu3lab-household"
MEMBER_GROUPS = OPERATOR_GROUPS | {HOUSEHOLD_GROUP}
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

IdentityData = dict[str, Any]


def _runtime_env() -> dict[str, str]:
    try:
        return platform_values()
    except (OSError, ValueError):
        return {}


def ingress_token() -> str:
    return _runtime_env().get("MU3LAB_INGRESS_TOKEN", "")


def trusted_proxy(request: Request) -> bool:
    expected = ingress_token()
    supplied = request.headers.get("x-mu3lab-proxy-token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def csrf_token(request: Request) -> str:
    """Bind a non-secret request token to the verified Authentik session JWT."""
    if not trusted_proxy(request):
        return ""
    session = request.headers.get("x-authentik-jwt", "")
    control_secret = _runtime_env().get("MU3LAB_CTL_TOKEN", "")
    if not session or not control_secret:
        return ""
    return hmac.new(control_secret.encode(), session.encode(), hashlib.sha256).hexdigest()


def mutation_allowed(request: Request) -> bool:
    """Require same-origin browser intent and a token bound to this SSO session."""
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.netloc.lower() != host.lower():
        return False
    expected = csrf_token(request)
    supplied = request.headers.get("x-mu3lab-csrf", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _record_operator_traversal() -> None:
    # An operator request is machine evidence that Tailscale, Caddy, and
    # Authentik are all in front of the dashboard.
    from ctl import bootstrap_state
    from ctl.provisioning import ProvisioningStore

    try:
        bootstrap_state.confirm("dashboard_protection")
    except OSError:
        pass
    try:
        store = ProvisioningStore.runtime()
        if store:
            current = {item["phase_id"]: item["actual_state"] for item in store.summary()["phases"]}
            if current.get("dashboard_protection") != "verified":
                store.update(
                    "dashboard_protection",
                    "verified",
                    detail="Authenticated operator session verified through Authentik.",
                )
    except (OSError, ValueError):
        pass


def resolve_identity(request: Request) -> IdentityData:
    """Trust Authentik headers only across the authenticated Caddy hop."""
    trusted = trusted_proxy(request)

    def header(name: str) -> str:
        return request.headers.get(name, "").strip() if trusted else ""

    username, subject_id, display_name = (
        header("x-authentik-username"),
        header("x-authentik-uid"),
        header("x-authentik-name"),
    )
    email = header("x-authentik-email")
    if email and _EMAIL.fullmatch(email):
        local, domain = email.rsplit("@", 1)
        email = f"{local}@{domain.lower()}"
    else:
        email = ""
    # Authentik's proxy provider serializes groups with a pipe delimiter.
    groups = tuple(value.strip() for value in header("x-authentik-groups").split("|") if value.strip())
    authenticated = bool(username)
    operator = authenticated and bool(OPERATOR_GROUPS.intersection(groups))
    member = authenticated and bool(MEMBER_GROUPS.intersection(groups))
    if operator:
        _record_operator_traversal()
    return {
        "ok": True,
        "control_plane_auth": "authentik_forward_auth" if authenticated else "not_configured",
        "username": username,
        "subject_id": subject_id,
        "email": email,
        "display_name": display_name,
        "groups": list(groups),
        "detail": (
            "Authenticated through Authentik."
            if member
            else "Tailnet access is private, but Authentik protection and operator role mapping are not configured yet."
        ),
        "role": "admin" if operator else "member" if member else "",
        "is_admin": operator,
        # May make the changes their role allows; each endpoint checks which.
        "writes_enabled": member,
    }


def current_identity(request: Request) -> IdentityData:
    return resolve_identity(request)


Identity = Annotated[IdentityData, Depends(current_identity)]


def require_operator(identity: Identity) -> IdentityData:
    """Administration: system settings, app lifecycle, AI providers, chat tools."""
    if not identity.get("is_admin"):
        raise ApiError(403, "Only a Mu3Lab administrator can do this.", code="admin_required")
    return identity


Operator = Annotated[IdentityData, Depends(require_operator)]


def require_member(identity: Identity) -> IdentityData:
    """Anyone in the household, administrators included."""
    if not identity.get("writes_enabled"):
        raise ApiError(403, "operator identity required")
    return identity


Member = Annotated[IdentityData, Depends(require_member)]


def require_member_mutation(request: Request, identity: Member) -> IdentityData:
    if not mutation_allowed(request):
        raise ApiError(403, "same-origin CSRF verification failed", code="csrf_failed")
    return identity


MemberMutation = Annotated[IdentityData, Depends(require_member_mutation)]


def require_operator_mutation(request: Request, identity: Operator) -> IdentityData:
    if not mutation_allowed(request):
        raise ApiError(403, "same-origin CSRF verification failed", code="csrf_failed")
    return identity


OperatorMutation = Annotated[IdentityData, Depends(require_operator_mutation)]


def require_owner(identity: Member) -> IdentityData:
    """A household member whose Authentik subject is known, for their own resources."""
    if not identity.get("subject_id"):
        raise ApiError(403, "operator identity required")
    return identity


Owner = Annotated[IdentityData, Depends(require_owner)]


def require_owner_mutation(request: Request, identity: Owner) -> IdentityData:
    if not mutation_allowed(request):
        raise ApiError(403, "same-origin CSRF verification failed", code="csrf_failed")
    return identity


OwnerMutation = Annotated[IdentityData, Depends(require_owner_mutation)]


def require_verified_account(identity: MemberMutation) -> IdentityData:
    """Workflows that create per-user app accounts need a subject and email."""
    if not identity.get("subject_id") or not identity.get("email"):
        raise ApiError(409, "A verified Authentik subject and email are required.")
    return identity


VerifiedAccount = Annotated[IdentityData, Depends(require_verified_account)]


def require_admin_verified_account(identity: VerifiedAccount) -> IdentityData:
    if not identity.get("is_admin"):
        raise ApiError(403, "Only a Mu3Lab administrator can do this.", code="admin_required")
    return identity


AdminVerifiedAccount = Annotated[IdentityData, Depends(require_admin_verified_account)]


def job_identity(identity: IdentityData) -> JobIdentity:
    """The identity handed to a job that provisions an account for this user."""
    return {
        "owner_uid": str(identity["subject_id"]),
        "email": str(identity["email"]),
        "username": str(identity["username"]),
        "display_name": str(identity.get("display_name") or identity["username"]),
    }
