"""Version-specific setup through app APIs; never borrow a user's browser session."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

from ctl import onboarding_state
from ctl.registry import Service
from ctl.runtime import RuntimePaths
from ctl.workflow_secrets import WorkflowSecretError


class OnboardingError(ValueError):
    pass


def request(port: int, path: str, *, method: str = "GET", data=None, token: str = ""):
    """Loopback-only requests with bounded IO and no credentials in error text."""
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read(2 * 1024 * 1024)
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise OnboardingError(f"Application setup request {path} failed (HTTP {exc.code}).") from None
    except (OSError, ValueError) as exc:
        raise OnboardingError(f"Application setup request {path} did not complete.") from exc


def provision_immich(service: Service, owner: Mapping[str, object], url: str, paths: RuntimePaths) -> str:
    config = request(service.https_port, "/api/server/config")
    record = onboarding_state.read(service.id, paths)
    # Existing installations are never assigned a new administrator or password.
    if config.get("isInitialized") and (record.get("provisioned") or not record.get("password")):
        return "Existing Immich administrator preserved; Open will use Authentik."
    login = onboarding_state.prepare_login(service.id, paths)
    email = str(owner["email"])
    if not config.get("isInitialized"):
        request(
            service.https_port,
            "/api/auth/admin-sign-up",
            method="POST",
            data={
                "email": email,
                "name": str(owner.get("display_name") or owner["username"]),
                "password": login["password"],
            },
        )
    auth = request(
        service.https_port, "/api/auth/login", method="POST", data={"email": email, "password": login["password"]}
    )
    token = auth.get("accessToken", "")
    if not token or not auth.get("isAdmin"):
        raise OnboardingError("Immich did not confirm its managed administrator.")
    try:
        request(service.https_port, "/api/users/me/onboarding", method="PUT", data={"isOnboarded": True}, token=token)
        onboarding_state.complete_login(service.id, email, url + "/auth/login?autoLaunch=0", paths)
    finally:
        request(service.https_port, "/api/auth/logout", method="POST", token=token)
    return "Immich administrator and initial onboarding configured; Open will link Authentik."


def provision_surfsense(service: Service, owner: Mapping[str, object], url: str, paths: RuntimePaths) -> str:
    record = onboarding_state.read(service.id, paths)
    if record.get("provisioned"):
        return "The managed SurfSense account is already configured."
    login = onboarding_state.prepare_login(service.id, paths)
    # Registration invokes SurfSense's own default-workspace hook.
    try:
        request(
            service.https_port,
            "/auth/register",
            method="POST",
            data={"email": str(owner["email"]), "password": login["password"]},
        )
    except OnboardingError:
        # A previous attempt may have created the account before the worker stopped.
        # Authenticate the same generated credential; never reset an existing user.
        pass
    body = urllib.parse.urlencode({"username": str(owner["email"]), "password": login["password"]}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{service.https_port}/auth/jwt/login",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            auth = json.load(response)
    except (OSError, ValueError):
        raise OnboardingError(
            "SurfSense could not verify the generated login. An existing account was left unchanged."
        ) from None
    token = auth.get("access_token", "")
    if not token:
        raise OnboardingError("SurfSense did not return a valid login.")
    profile = request(service.https_port, "/users/me", token=token)
    if str(profile.get("email", "")).lower() != str(owner["email"]).lower() or not profile.get("is_active"):
        raise OnboardingError("SurfSense did not confirm the managed account.")
    workspaces = request(service.https_port, "/api/v1/workspaces?owned_only=true", token=token)
    if not isinstance(workspaces, list):
        raise OnboardingError("SurfSense did not confirm its initial workspace.")
    if not workspaces:
        # Recover a registration hook failure without asking for a setup form.
        workspace = request(
            service.https_port,
            "/api/v1/workspaces",
            method="POST",
            data={
                "name": "Personal",
                "description": "Your personal workspace",
                "citations_enabled": True,
            },
            token=token,
        )
        if not isinstance(workspace, dict) or not workspace.get("id"):
            raise OnboardingError("SurfSense could not create its initial workspace.")
    onboarding_state.complete_login(service.id, str(owner["email"]), url, paths)
    return "SurfSense account created; save its generated login to Bitwarden once."


def provision(service: Service, owner: Mapping[str, object], paths: RuntimePaths = RuntimePaths()) -> str:
    from ctl.service_state import tailnet_dns_name

    host = tailnet_dns_name()
    url = f"https://{host}:{service.private_https_port}" if host else ""
    try:
        if service.id == "immich":
            return provision_immich(service, owner, url, paths)
        if service.id == "surfsense":
            return provision_surfsense(service, owner, url, paths)
    except WorkflowSecretError as exc:
        raise OnboardingError(str(exc)) from exc
    return "Application account provisioning is configured."
