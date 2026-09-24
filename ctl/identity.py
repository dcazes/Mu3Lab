"""Durable, honest identity projection and idempotent OIDC blueprint recovery."""

from __future__ import annotations

from dataclasses import dataclass
from ctl.authentik_blueprints import write_oidc_application_blueprint
from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.registry import Registry, Service
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env


@dataclass(frozen=True)
class OidcContract:
    name: str
    client_id_key: str
    client_secret_key: str
    redirects: tuple[str, ...]


OIDC_CONTRACTS: dict[str, OidcContract] = {
    "actual-budget": OidcContract("Actual Budget", "ACTUAL_OPENID_CLIENT_ID", "ACTUAL_OPENID_CLIENT_SECRET", ("/openid/callback",)),
    "mealie": OidcContract("Mealie", "MEALIE_OIDC_CLIENT_ID", "MEALIE_OIDC_CLIENT_SECRET", ("/login", "/login?direct=1")),
    "nextcloud": OidcContract("Nextcloud", "NEXTCLOUD_OIDC_CLIENT_ID", "NEXTCLOUD_OIDC_CLIENT_SECRET", ("/apps/user_oidc/code",)),
    "immich": OidcContract("Immich", "IMMICH_OIDC_CLIENT_ID", "IMMICH_OIDC_CLIENT_SECRET", ("/auth/login", "/user-settings", "/api/oauth/mobile-redirect",)),
    "paperless-ngx": OidcContract("Paperless-ngx", "PAPERLESS_OIDC_CLIENT_ID", "PAPERLESS_OIDC_CLIENT_SECRET", ("/accounts/oidc/authentik/login/callback/",)),
    "adventurelog": OidcContract("AdventureLog", "ADVENTURELOG_OIDC_CLIENT_ID", "ADVENTURELOG_OIDC_CLIENT_SECRET", ("/accounts/oidc/mu3lab-adventurelog/login/callback/",)),
    "lobehub": OidcContract("LobeChat", "AUTH_AUTHENTIK_ID", "AUTH_AUTHENTIK_SECRET", ("/api/auth/callback/authentik",)),
}

TRUSTED_HEADER = {"baby-buddy"}
PROXY_GATE = {"litellm", "surfsense"}
LOCAL = {"authentik", "vaultwarden", "freellmapi"}
NO_UI = {"ingress", "ollama"}
OIDC_LAUNCH_PATHS = {"nextcloud": "/index.php/apps/user_oidc/login/1"}


def mode_for(service: Service) -> str:
    if service.id in OIDC_CONTRACTS:
        return "native_oidc"
    if service.id in TRUSTED_HEADER:
        return "trusted_header"
    if service.id in PROXY_GATE:
        return "proxy_gate"
    if service.id in LOCAL:
        return "local"
    # An excluded-auth service has a private route but no user identity or
    # login flow (for example Firecrawl's API).  Keep it distinct from a
    # local-login application so launch controls never incorrectly say Login.
    if service.auth == "excluded":
        return "none"
    if service.id in NO_UI or not service.ui.get("available", False):
        return "none"
    return "local"


def projection(service: Service, item: dict, state: ControlState | None) -> dict:
    """Return browser-safe identity truth without deriving native SSO from YAML alone."""
    mode = mode_for(service)
    saved = state.service_identity(service.id) if state else None
    route_ready = bool(item.get("route_ready"))
    healthy = item.get("health_state") == "healthy"
    # The additive identity projection must remain compatible with older
    # service responses.  The route URL is the authoritative browser target;
    # the legacy ui.url is only a fallback for mixed-version rollouts.
    launch_url = str(item.get("url") or (item.get("ui") or {}).get("url") or "")
    if service.id in OIDC_LAUNCH_PATHS and launch_url:
        launch_url = launch_url.rstrip("/") + OIDC_LAUNCH_PATHS[service.id]
    recovery = service.id in {"actual-budget", "mealie", "nextcloud", "immich", "paperless-ngx", "adventurelog", "vaultwarden", "freellmapi"}
    if saved:
        current = str(saved["state"])
        detail = str(saved.get("detail") or "")
        # A worker restart or an older worker binary can leave the durable
        # identity row at configuring after its job has already failed. Do
        # not strand the launcher in a permanently disabled state.
        if current == "configuring":
            job_store = JobStore.runtime()
            job = job_store.get(str(saved.get("last_job_id") or "")) if job_store else None
            if job and str(job.get("state")) in {"failed", "cancelled"}:
                current = "degraded"
                detail = str(job.get("detail") or "Sign-in reconciliation failed; retry repair.")
        if current == "ready" and (not route_ready or not healthy):
            current = "degraded"
            detail = "Sign-in is configured, but the application or its private route is unavailable."
    elif mode == "none":
        current, detail = "unsupported", (service.identity_note or service.ui.get("unavailable_reason") or "This service has no end-user interface.")
    elif service.id == "authentik":
        current = "ready" if route_ready and healthy else "degraded"
        detail = "This is the identity provider; the current Authentik session opens its administration UI directly."
    elif mode == "local":
        current, detail = "unsupported", service.identity_note or "This application requires its own local sign-in."
    elif mode == "proxy_gate":
        current = "ready" if route_ready and healthy else "degraded"
        detail = "Authentik protects access to this route; the application does not receive a native per-user OIDC session."
    elif mode == "trusted_header":
        current = "ready" if route_ready and healthy else "degraded"
        detail = "Authentik supplies the verified user identity to the application."
    else:
        current, detail = "unconfigured", "Native Authentik sign-in has not completed owner migration and live callback verification."
    return {
        "mode": mode, "state": current,
        "launch_url": launch_url if service.id not in NO_UI else "",
        "detail": detail, "last_verified_at": str((saved or {}).get("last_verified_at", "")),
        "recovery_available": recovery,
        "job_id": str((saved or {}).get("last_job_id", "")),
        "error": (saved or {}).get("last_error", {}),
    }


def reconcile_blueprints(registry: Registry, host: str,
                         paths: RuntimePaths = RuntimePaths()) -> list[str]:
    """Restore missing OIDC blueprints strictly from already-persisted secrets."""
    if not host:
        return []
    written: list[str] = []
    for service_id, contract in OIDC_CONTRACTS.items():
        service = registry.get(service_id)
        project = paths.projects / service_id
        env_path = project / ".env"
        if not env_path.is_file() or not service.private_https_port:
            continue
        values = read_runtime_env(env_path)
        client_id = values.get(contract.client_id_key, "")
        secret = values.get(contract.client_secret_key, "")
        if not client_id or not secret:
            continue
        write_oidc_application_blueprint(
            paths.root, host, service_id=service_id, name=contract.name,
            private_port=service.private_https_port, client_id=client_id,
            client_secret=secret, redirect_paths=contract.redirects,
        )
        written.append(service_id)
    return written
