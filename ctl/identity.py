"""Manifest-driven sign-in projection and synchronous Authentik configuration."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from ctl import resource_locks
from ctl.authentik_blueprints import (
    DASHBOARD_BLUEPRINT,
    GatedApp,
    OidcApp,
    oidc_blueprint_name,
    removal_blueprint_name,
    render_gate_blueprint,
    render_oidc_blueprint,
    render_removal_blueprint,
)
from ctl.control_state import ControlState
from ctl.engine.template import render
from ctl.integrations.authentik import Authentik
from ctl.jobs import JobStore
from ctl.manifest.catalog import App, Catalog
from ctl.manifest.models import AppManifest
from ctl.registry import RegistryError, Service, load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.store import records

# Every sign-in change goes through Authentik one at a time: the dashboard's
# startup sync, app installs and removals, and the installer (another process).
SIGN_IN_LOCK = "sign-in"
# What each sign-in import last applied (a fingerprint, never the content: it holds client secrets).
APPLIED_SCOPE = "sign_in_applied"
_APPLICATION_SLUG = re.compile(r"model: authentik_core\.application\b[^\n]*\n(?:[^\n]*\n){0,6}?\s*slug: (\S+)")


def launch_path(app: AppManifest, paths: RuntimePaths | None = None) -> str:
    oidc = app.sign_in.oidc
    if oidc is None:
        return app.ui.path
    values = {secret.env: secret.value for secret in app.secrets if secret.kind == "fixed"}
    values.update(read_runtime_env((paths or RuntimePaths()).projects / app.id / ".env"))
    return render(oidc.launch_path, values.get)


def authentik_only(app_id: str) -> bool:
    try:
        return load().get(app_id).manifest.sign_in.method in {"oidc", "trusted_header"}
    except (KeyError, ValueError, RegistryError):
        return False


def mode_for(service: Service) -> str:
    return {
        "oidc": "native_oidc",
        "trusted_header": "trusted_header",
        "gate": "proxy_gate",
        "local": "local",
        "none": "none",
    }[service.manifest.sign_in.method]


def projection(service: Service, item: dict, state: ControlState | None) -> dict:
    """Return browser-safe identity truth without deriving native SSO from YAML alone."""
    mode = mode_for(service)
    saved = state.service_identity(service.id) if state else None
    failed_setup = False
    route_ready = bool(item.get("route_ready"))
    healthy = item.get("health_state") == "healthy"
    # The additive identity projection must remain compatible with older
    # service responses.  The route URL is the authoritative browser target;
    # the legacy ui.url is only a fallback for mixed-version rollouts.
    launch_url = str(item.get("url") or (item.get("ui") or {}).get("url") or "")
    if service.manifest.sign_in.oidc and launch_url:
        # The launch path starts at the app's origin, not its start page (ContextForge's is /admin/).
        address = urlsplit(launch_url)
        launch_url = f"{address.scheme}://{address.netloc}" + launch_path(service.manifest)
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
                current, failed_setup = "degraded", True
                detail = str(job.get("detail") or "Sign-in setup failed.")
        if current == "ready" and (not route_ready or not healthy):
            current = "degraded"
            detail = "Sign-in is configured, but the application or its private route is unavailable."
    elif mode == "none":
        current, detail = (
            "unsupported",
            (
                service.identity_note
                or service.ui.get("unavailable_reason")
                or "This service has no end-user interface."
            ),
        )
    elif service.manifest.sign_in.session_provider:
        current = "ready" if route_ready and healthy else "degraded"
        detail = "This is the identity provider; the current sign-in session opens its administration UI directly."
    elif mode == "local":
        current, detail = "unsupported", service.identity_note or "This application requires its own local sign-in."
    elif mode == "proxy_gate":
        current = "ready" if route_ready and healthy else "degraded"
        detail = (
            "Authentik protects access to this route; the application does not receive a native per-user OIDC session."
        )
    elif mode == "trusted_header":
        current = "ready" if route_ready and healthy else "degraded"
        detail = "Authentik supplies the verified user identity to the application."
    else:
        current, detail = (
            "unconfigured",
            "Authentik sign-in is checked when the app is installed.",
        )
    if item.get("state") == "stopped" and current in {"ready", "degraded"} and not failed_setup:
        # Turned off is a choice, not a sign-in problem: the check answers again once it starts.
        current, detail = "off", "Turned off. Sign-in works again once the app is started."
    return {
        "mode": mode,
        "state": current,
        "launch_url": launch_url if service.ui.get("available", False) else "",
        "detail": detail,
        "last_verified_at": str((saved or {}).get("last_verified_at", "")),
        "job_id": str((saved or {}).get("last_job_id", "")),
        "error": (saved or {}).get("last_error", {}),
    }


def installed(app: App, paths: RuntimePaths) -> bool:
    return app.manifest.tier != "optional" or (paths.projects / app.id / "docker-compose.yml").is_file()


def gate_blueprint(catalog: Catalog, host: str, paths: RuntimePaths) -> str:
    # The foundation proxy's route is the dashboard address; its manifest has no end-user sign-in.
    dashboard = next(
        (
            app
            for app in catalog.apps
            if app.manifest.tier == "foundation"
            and app.manifest.sign_in.method == "none"
            and app.manifest.route is not None
        ),
        None,
    )
    if dashboard is None:
        raise ValueError("The app catalog has no foundation dashboard route.")
    assert dashboard.manifest.route is not None
    gated = tuple(
        GatedApp(app.id, app.manifest.name, app.manifest.route.https_port, app.manifest.route.audience)
        for app in catalog.apps
        if app.manifest.route is not None
        and app.manifest.route.access in {"gate", "trusted_header"}
        and installed(app, paths)
    ) + tuple(
        # Each gated extra address is its own forward-auth provider in Authentik.
        GatedApp(f"{app.id}-{extra.name}", f"{app.manifest.name} {extra.label}", extra.https_port, extra.audience)
        for app in catalog.apps
        for extra in app.manifest.extra_routes
        if extra.access == "gate" and installed(app, paths)
    )
    return render_gate_blueprint(host, dashboard.manifest.route.https_port, gated)


def _oidc_settings(app: App, paths: RuntimePaths) -> OidcApp | None:
    manifest = app.manifest
    oidc = manifest.sign_in.oidc
    if not installed(app, paths) or oidc is None or manifest.route is None:
        return None
    values = read_runtime_env(paths.projects / app.id / ".env")
    client_id, secret = values.get(oidc.env.client_id, ""), values.get(oidc.env.client_secret, "")
    if not client_id or not secret:
        return None  # Core projects are materialized later; kept credentials alone don't install an optional app.
    return OidcApp(
        app.id,
        manifest.name,
        manifest.route.https_port,
        client_id,
        secret,
        oidc.redirect_paths,
        values.get(oidc.initial_owner_env, ""),
        (oidc.role_claim.claim, oidc.role_claim.admin, oidc.role_claim.user, oidc.role_claim.multi)
        if oidc.role_claim
        else None,
        manifest.route.audience,
        oidc.forwards_sign_in,
    )


def _gated(app: App) -> bool:
    return app.manifest.gated


def sync_sign_in(
    catalog: Catalog,
    host: str,
    authentik: Authentik,
    paths: RuntimePaths | None = None,
    *,
    app_id: str | None = None,
) -> list[str]:
    """Apply sign-in blueprints; with ``app_id``, only the one that app's sign-in lives in.

    Each import rewrites every provider it names and takes tens of seconds, so
    an app install must not re-import every other app's sign-in as well.
    """
    paths = paths or RuntimePaths()
    if not host:
        raise ValueError("This server's private address is not known yet.")
    with resource_locks.hold(SIGN_IN_LOCK, paths=paths):
        return _sync_sign_in(catalog, host, authentik, paths, app_id)


def _sync_sign_in(
    catalog: Catalog, host: str, authentik: Authentik, paths: RuntimePaths, app_id: str | None, force: bool = False
) -> list[str]:
    target = catalog.get(app_id) if app_id else None
    # An import Authentik already holds is skipped (each takes tens of seconds): same content, same
    # Authentik release, and its applications still there. Anything else is imported again.
    unchanged = _Unchanged(authentik, paths, force)
    if target is None or _gated(target):
        authentik.remove_duplicate_default_brands()
        unchanged.apply(DASHBOARD_BLUEPRINT, gate_blueprint(catalog, host, paths))
    applied: list[str] = []
    for app in (target,) if target else catalog.apps:
        settings = _oidc_settings(app, paths)
        if settings is None:
            continue
        unchanged.apply(oidc_blueprint_name(app.id), render_oidc_blueprint(host, settings))
        applied.append(app.id)
    return applied


class _Unchanged:
    """Applies a sign-in import only when Authentik does not already hold it."""

    def __init__(self, authentik: Authentik, paths: RuntimePaths, force: bool) -> None:
        self.authentik, self.paths, self.force = authentik, paths, force
        self._version: str | None = None
        self._slugs: set[str] | None = None

    def _fingerprint(self, content: str) -> str:
        if self._version is None:
            self._version = self.authentik.version()
        return hashlib.sha256(f"{self._version}\n{content}".encode()).hexdigest()

    def _present(self, content: str) -> bool:
        wanted = set(_APPLICATION_SLUG.findall(content))
        if not wanted:
            return True
        if self._slugs is None:
            self._slugs = self.authentik.application_slugs()
        return wanted <= self._slugs

    def apply(self, name: str, content: str) -> None:
        fingerprint = self._fingerprint(content)
        saved = records.get(APPLIED_SCOPE, name, self.paths).get("fingerprint")
        if not self.force and saved == fingerprint and self._present(content):
            return
        self.authentik.apply_blueprint(name, content)
        records.put(APPLIED_SCOPE, name, {"fingerprint": fingerprint, "content": _digest(content)}, self.paths)
        self._slugs = None  # the import may have added applications


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def gate_applied(catalog: Catalog, host: str, paths: RuntimePaths | None = None) -> bool:
    """Authentik last received exactly this dashboard gate, from the installer or an app install."""
    paths = paths or RuntimePaths()
    saved = records.get(APPLIED_SCOPE, DASHBOARD_BLUEPRINT, paths).get("content")
    return saved == _digest(gate_blueprint(catalog, host, paths))


def forget_applied(name: str, paths: RuntimePaths | None = None) -> None:
    """After a removal import, the next install of that app imports its sign-in again."""
    records.delete(APPLIED_SCOPE, name, paths or RuntimePaths())


def remove_sign_in(
    app: App, authentik: Authentik, *, catalog: Catalog | None = None, host: str = "", paths: RuntimePaths | None = None
) -> None:
    method = app.manifest.sign_in.method
    # An app on the home network signs people in itself and may still be gated on the tailnet.
    gated = app.manifest.gated
    if method != "oidc" and not gated:
        return
    if gated:
        if catalog is None:
            raise ValueError("Removing a gated app needs the remaining app catalog.")
        # Exclude explicitly too: a caller may be retrying cleanup before removing the project.
        remaining = Catalog(tuple(item for item in catalog.apps if item.id != app.id))
    with resource_locks.hold(SIGN_IN_LOCK, paths=paths or RuntimePaths()):
        if gated:
            sync_sign_in(remaining, host, authentik, paths)
        authentik.apply_blueprint(
            removal_blueprint_name(app.id), render_removal_blueprint(app.id, app.manifest.name, oidc=method == "oidc")
        )
        forget_applied(oidc_blueprint_name(app.id), paths)
