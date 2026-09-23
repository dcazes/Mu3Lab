"""Durable, registry-bound application installation and lifecycle execution."""

from __future__ import annotations

import json
import hashlib
import base64
import os
import secrets as token_secrets
import shutil
import time
import urllib.error
import urllib.request
import re
from pathlib import Path

import yaml

from ctl import actions
from ctl.control_state import ControlState
from ctl.jobs import JobStore, redact
from ctl.registry import RegistryError, Service, load
from ctl.routes import apply as apply_route
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl import workflow_secrets

SUPPORTED_ACTIONS = frozenset({"install", "retry_setup", "start", "stop", "restart", "repair", "reset", "configure_identity"})


def project_path(service: Service, root: Path) -> Path:
    """Prefer a materialized optional project; core uses its checkout contract."""
    runtime = RuntimePaths().projects / service.id
    if service.stage == "optional" and (runtime / "docker-compose.yml").is_file():
        return runtime
    return service.compose_path(root)


def reset_failed_application(service: Service, root: Path, log) -> tuple[bool, str]:
    """Clean a failed optional application so it can be selected again.

    This is deliberately not an install/retry operation.  It removes only
    Compose containers/orphans and generated transient override files.  It
    never passes ``--volumes`` and never deletes the persistent data root or
    the generated .env credentials needed to reconnect to existing data.
    """
    if service.stage != "optional":
        return False, "Only optional applications can be reset from the catalog."
    project = project_path(service, root)
    compose_file = project / "docker-compose.yml"
    if not compose_file.is_file():
        return True, "No materialized application containers were found."
    rc, output = actions.compose_down(project, log)
    if rc:
        return False, output or "Failed application containers could not be removed."
    for name in ("docker-compose.digest.yml", "docker-compose.bootstrap.yml"):
        try:
            (project / name).unlink(missing_ok=True)
        except OSError as exc:
            return False, f"Temporary setup file could not be removed: {exc}"
    # These blueprints are generated for the first installation attempt.  A
    # failed app must not leave a stale Authentik application to be reused by
    # a later selection; successful installs regenerate the same idempotent
    # blueprint during materialization.
    blueprint = RuntimePaths().projects / "authentik" / "blueprints" / f"mu3lab-{service.id}.yaml"
    try:
        blueprint.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"Temporary identity setup file could not be removed: {exc}"
    env_path = project / ".env"
    if env_path.is_file():
        values = read_runtime_env(env_path)
        for key in ("MU3LAB_BOOTSTRAP_USERNAME", "MU3LAB_BOOTSTRAP_EMAIL",
                    "MU3LAB_BOOTSTRAP_PASSWORD", "NEXTCLOUD_ADMIN_USER",
                    "NEXTCLOUD_ADMIN_PASSWORD"):
            values.pop(key, None)
        env_path.write_text(runtime_env_text(values), encoding="utf-8")
        os.chmod(env_path, 0o600)
    return True, "Failed containers and temporary setup files removed; persistent data preserved."


def allowed_actions(service: Service, state: str) -> list[str]:
    if service.is_blocked:
        return []
    if service.stage == "optional" and state in {"planned", "not_installed"}:
        return ["install"]
    if state in {"failed", "needs_attention", "needs_setup", "degraded"}:
        return (["repair", "restart"] if service.stage == "core"
                else ["retry_setup", "restart"])
    if state == "stopped":
        return ["start"]
    if state in {"ready", "running", "starting", "configured", "installed"}:
        result = ["repair", "restart"] if service.stage == "optional" else ["restart"]
        if service.lifecycle != "always_on":
            result.insert(0, "stop")
        return result
    return []


def _event(store: JobStore, job_id: str, stage: str, detail: str) -> None:
    store.append_event(job_id, "stage", f"{stage}: {detail}")


def _materialize(service: Service, root: Path) -> Path:
    source = service.compose_path(root)
    target = RuntimePaths().projects / service.id
    target.mkdir(mode=0o750, parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name.startswith(".env") or item.name == "__pycache__" or item.is_symlink():
            continue
        destination = target / item.name
        if item.is_file():
            shutil.copy2(item, destination)
        elif item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", ".git"))
    env_path = target / ".env"
    values = read_runtime_env(env_path)
    values.setdefault("MU3LAB_DATA_ROOT", str(RuntimePaths().data))
    if service.id == "immich":
        values.setdefault("DB_PASSWORD", token_secrets.token_urlsafe(36))
        values.setdefault("DB_USERNAME", "postgres")
        values.setdefault("DB_DATABASE_NAME", "immich")
        values.setdefault("IMMICH_OIDC_CLIENT_ID", "mu3lab-immich")
        values.setdefault("IMMICH_OIDC_CLIENT_SECRET", token_secrets.token_urlsafe(40))
    elif service.id == "adventurelog":
        values.setdefault("POSTGRES_PASSWORD", token_secrets.token_urlsafe(36))
        values.setdefault("SECRET_KEY", token_secrets.token_urlsafe(48))
        values.setdefault("ADVENTURELOG_OIDC_CLIENT_ID", "mu3lab-adventurelog")
        values.setdefault("ADVENTURELOG_OIDC_CLIENT_SECRET", token_secrets.token_urlsafe(40))
    elif service.id == "paperless-ngx":
        values.setdefault("PAPERLESS_DBPASS", token_secrets.token_urlsafe(36))
        values.setdefault("PAPERLESS_SECRET_KEY", token_secrets.token_urlsafe(48))
        values.setdefault("PAPERLESS_OIDC_CLIENT_ID", "mu3lab-paperless-ngx")
        values.setdefault("PAPERLESS_OIDC_CLIENT_SECRET", token_secrets.token_urlsafe(40))
    elif service.id == "nextcloud":
        values.setdefault("NEXTCLOUD_DB_PASSWORD", token_secrets.token_urlsafe(36))
        values.setdefault("NEXTCLOUD_REDIS_PASSWORD", token_secrets.token_urlsafe(36))
        values.setdefault("NEXTCLOUD_OIDC_CLIENT_ID", "mu3lab-nextcloud")
        values.setdefault("NEXTCLOUD_OIDC_CLIENT_SECRET", token_secrets.token_urlsafe(40))
    elif service.id == "surfsense":
        values.setdefault("DB_USER", "surfsense")
        values.setdefault("DB_NAME", "surfsense")
        values.setdefault("DB_PASSWORD", token_secrets.token_urlsafe(36))
        values.setdefault("SECRET_KEY", token_secrets.token_urlsafe(48))
        values.setdefault("ZERO_ADMIN_PASSWORD", token_secrets.token_urlsafe(36))
        values.setdefault("SEARXNG_SECRET", token_secrets.token_urlsafe(36))
        values.setdefault("AUTH_TYPE", "LOCAL")
        values.setdefault("REGISTRATION_ENABLED", "TRUE")
        values.setdefault("SANDBOX_ENABLED", "FALSE")
        values.setdefault("EMBEDDING_MODEL", "litellm://ollama/nomic-embed-text")
        values.setdefault("EMBEDDING_BASE_URL", "http://ollama:11434")
        # v0.0.40 also reads this legacy spelling in selected code paths.
        values.setdefault("EMBEDDING_API_BASE_URL", "http://ollama:11434")
    elif service.id == "firecrawl":
        values.setdefault("POSTGRES_PASSWORD", token_secrets.token_urlsafe(36))
        values.setdefault("RABBITMQ_DEFAULT_PASS", token_secrets.token_urlsafe(36))
        values.setdefault("BULL_AUTH_KEY", token_secrets.token_urlsafe(36))
    elif service.id == "lobehub":
        values.setdefault("POSTGRES_PASSWORD", token_secrets.token_urlsafe(36))
        values.setdefault("AUTH_SECRET", token_secrets.token_urlsafe(48))
        # LobeHub validates this as base64-decoded AES key material and only
        # accepts 16, 24, or 32 bytes.  Generate the documented 256-bit form.
        values.setdefault("KEY_VAULTS_SECRET",
                          base64.b64encode(token_secrets.token_bytes(32)).decode("ascii"))
        values.setdefault("RUSTFS_ACCESS_KEY", "mu3lab-lobehub")
        values.setdefault("RUSTFS_SECRET_KEY", token_secrets.token_urlsafe(48))
        values.setdefault("AUTH_AUTHENTIK_ID", "mu3lab-lobehub")
        values.setdefault("AUTH_AUTHENTIK_SECRET", token_secrets.token_urlsafe(40))
        litellm = read_runtime_env(RuntimePaths().projects / "litellm" / ".env")
        if litellm.get("LITELLM_MASTER_KEY"):
            values.setdefault("LITELLM_MASTER_KEY", litellm["LITELLM_MASTER_KEY"])
        from ctl.lobehub_ops import apply_model_policy
        apply_model_policy(values, root)
    elif service.id == "homarr":
        # Homarr requires exactly 32 bytes represented as a 64-character hex key.
        # Keep it stable across repairs so encrypted integration data remains readable.
        values.setdefault("HOMARR_SECRET_ENCRYPTION_KEY", token_secrets.token_hex(32))
        values.setdefault("HOMARR_OIDC_CLIENT_ID", "mu3lab-homarr")
        values.setdefault("HOMARR_OIDC_CLIENT_SECRET", token_secrets.token_urlsafe(40))
        root_values = read_runtime_env(root / ".env")
        if root_values.get("MU3LAB_HOMARR_TOKEN"):
            values.setdefault("HOMARR_CONTROL_TOKEN", root_values["MU3LAB_HOMARR_TOKEN"])
    try:
        from ctl.service_state import tailnet_dns_name
        dns_name = tailnet_dns_name()
    except (OSError, ValueError):
        dns_name = ""
    if dns_name and service.private_https_port:
        public_url = f"https://{dns_name}:{service.private_https_port}"
        if service.id == "mealie":
            values.setdefault("MEALIE_BASE_URL", public_url)
            values.setdefault("MEALIE_OIDC_ENABLED", "true")
            values.setdefault("MEALIE_OIDC_SIGNUP_ENABLED", "true")
            values.setdefault("MEALIE_OIDC_CLIENT_ID", "mu3lab-mealie")
            values.setdefault("MEALIE_OIDC_CLIENT_SECRET", token_secrets.token_urlsafe(40))
            values.setdefault("MEALIE_OIDC_CONFIGURATION_URL",
                              f"https://{dns_name}/application/o/mu3lab-mealie/.well-known/openid-configuration")
            values.setdefault("MEALIE_OIDC_AUTO_REDIRECT", "false")
            values.setdefault("MEALIE_OIDC_REMEMBER_ME", "true")
            values.setdefault("MEALIE_ALLOW_PASSWORD_LOGIN", "true")
            from ctl.authentik_blueprints import write_oidc_application_blueprint
            write_oidc_application_blueprint(
                RuntimePaths().root, dns_name, service_id="mealie", name="Mealie",
                private_port=service.private_https_port,
                client_id=values["MEALIE_OIDC_CLIENT_ID"],
                client_secret=values["MEALIE_OIDC_CLIENT_SECRET"],
                redirect_paths=("/login", "/login?direct=1"),
            )
        elif service.id == "actual-budget":
            values.setdefault("ACTUAL_OPENID_CLIENT_ID", "mu3lab-actual-budget")
            values.setdefault("ACTUAL_OPENID_CLIENT_SECRET", token_secrets.token_urlsafe(40))
            values.setdefault("ACTUAL_OPENID_DISCOVERY_URL",
                              f"https://{dns_name}/application/o/mu3lab-actual-budget/.well-known/openid-configuration")
            values.setdefault("ACTUAL_OPENID_SERVER_HOSTNAME", public_url)
            values.setdefault("ACTUAL_OPENID_ENFORCE", "false")
            values.setdefault("ACTUAL_USER_CREATION_MODE", "login")
            from ctl.authentik_blueprints import write_oidc_application_blueprint
            write_oidc_application_blueprint(
                RuntimePaths().root, dns_name, service_id="actual-budget", name="Actual Budget",
                private_port=service.private_https_port,
                client_id=values["ACTUAL_OPENID_CLIENT_ID"],
                client_secret=values["ACTUAL_OPENID_CLIENT_SECRET"],
                redirect_paths=("/openid/callback",),
            )
        elif service.id == "adventurelog":
            values.setdefault("SITE_URL", public_url)
            values.setdefault("ADVENTURELOG_OIDC_DISCOVERY_URL",
                              f"https://{dns_name}/application/o/mu3lab-adventurelog/.well-known/openid-configuration")
            values.setdefault("ADVENTURELOG_FORCE_SOCIAL_LOGIN", "false")
            from ctl.authentik_blueprints import write_oidc_application_blueprint
            write_oidc_application_blueprint(
                RuntimePaths().root, dns_name, service_id="adventurelog", name="AdventureLog",
                private_port=service.private_https_port,
                client_id=values["ADVENTURELOG_OIDC_CLIENT_ID"],
                client_secret=values["ADVENTURELOG_OIDC_CLIENT_SECRET"],
                redirect_paths=("/accounts/oidc/mu3lab-adventurelog/login/callback/",),
            )
        elif service.id == "paperless-ngx":
            values.setdefault("PAPERLESS_URL", public_url)
            discovery = f"https://{dns_name}/application/o/mu3lab-paperless-ngx/.well-known/openid-configuration"
            values.setdefault("PAPERLESS_APPS", "allauth.socialaccount.providers.openid_connect")
            values.setdefault("PAPERLESS_SOCIALACCOUNT_PROVIDERS", json.dumps({
                "openid_connect": {"APPS": [{"provider_id": "authentik", "name": "Authentik",
                    "client_id": values["PAPERLESS_OIDC_CLIENT_ID"],
                    "secret": values["PAPERLESS_OIDC_CLIENT_SECRET"],
                    "settings": {"server_url": discovery}}]},
            }, separators=(",", ":")))
            values.setdefault("PAPERLESS_DISABLE_REGULAR_LOGIN", "false")
            values.setdefault("PAPERLESS_REDIRECT_LOGIN_TO_SSO", "false")
            from ctl.authentik_blueprints import write_oidc_application_blueprint
            write_oidc_application_blueprint(
                RuntimePaths().root, dns_name, service_id="paperless-ngx", name="Paperless-ngx",
                private_port=service.private_https_port,
                client_id=values["PAPERLESS_OIDC_CLIENT_ID"],
                client_secret=values["PAPERLESS_OIDC_CLIENT_SECRET"],
                redirect_paths=("/accounts/oidc/authentik/login/callback/",),
            )
        elif service.id == "immich":
            discovery = f"https://{dns_name}/application/o/mu3lab-immich/.well-known/openid-configuration"
            config = {"oauth": {"enabled": True, "issuerUrl": discovery,
                                 "clientId": values["IMMICH_OIDC_CLIENT_ID"],
                                 "clientSecret": values["IMMICH_OIDC_CLIENT_SECRET"],
                                 "scope": "openid email profile",
                                 "signingAlgorithm": "RS256", "autoRegister": True,
                                 "autoLaunch": False, "buttonText": "Login with Authentik",
                                 "roleClaim": "mu3lab_role", "mobileOverrideEnabled": False,
                                 "mobileRedirectUri": ""}}
            (target / "immich-config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            os.chmod(target / "immich-config.json", 0o600)
            from ctl.authentik_blueprints import write_oidc_application_blueprint
            write_oidc_application_blueprint(
                RuntimePaths().root, dns_name, service_id="immich", name="Immich",
                private_port=service.private_https_port,
                client_id=values["IMMICH_OIDC_CLIENT_ID"],
                client_secret=values["IMMICH_OIDC_CLIENT_SECRET"],
                redirect_paths=("/auth/login", "/user-settings", "/api/oauth/mobile-redirect"),
            )
        elif service.id == "nextcloud":
            values.setdefault("NEXTCLOUD_TRUSTED_DOMAINS", f"{dns_name} localhost 127.0.0.1")
            values.setdefault("NEXTCLOUD_TRUSTED_PROXIES", "127.0.0.1")
            values.setdefault("NEXTCLOUD_OVERWRITEHOST", f"{dns_name}:{service.private_https_port}")
            values.setdefault("NEXTCLOUD_OVERWRITECLIURL", public_url)
            from ctl.authentik_blueprints import write_oidc_application_blueprint
            write_oidc_application_blueprint(
                RuntimePaths().root, dns_name, service_id="nextcloud", name="Nextcloud",
                private_port=service.private_https_port,
                client_id=values["NEXTCLOUD_OIDC_CLIENT_ID"],
                client_secret=values["NEXTCLOUD_OIDC_CLIENT_SECRET"],
                redirect_paths=("/apps/user_oidc/code",),
            )
        elif service.id == "surfsense":
            values.setdefault("SURFSENSE_PUBLIC_URL", public_url)
        elif service.id == "lobehub":
            values.setdefault("APP_URL", public_url)
            values.setdefault("S3_ENDPOINT", public_url + "/lobe-assets")
            values.setdefault("AUTH_SSO_PROVIDERS", "authentik")
            values.setdefault("AUTH_AUTHENTIK_ISSUER",
                              f"https://{dns_name}/application/o/mu3lab-lobehub/")
            values.setdefault("AUTH_DISABLE_EMAIL_PASSWORD", "1")
            values.setdefault("OPENAI_PROXY_URL", "http://litellm:4000/v1")
            values["OPENAI_MODEL_LIST"] = "-all,+mu3lab-chat"
            from ctl.authentik_blueprints import write_oidc_application_blueprint
            write_oidc_application_blueprint(
                RuntimePaths().root, dns_name, service_id="lobehub", name="LobeChat",
                private_port=service.private_https_port,
                client_id=values["AUTH_AUTHENTIK_ID"],
                client_secret=values["AUTH_AUTHENTIK_SECRET"],
                redirect_paths=("/api/auth/callback/authentik",),
            )
        elif service.id == "homarr":
            values.setdefault("HOMARR_BASE_URL", public_url)
            values.setdefault("HOMARR_OIDC_ISSUER",
                              f"https://{dns_name}/application/o/mu3lab-homarr/")
            values.setdefault("HOMARR_OIDC_URI",
                              f"https://{dns_name}/application/o/authorize/")
            values.setdefault("HOMARR_OIDC_LOGOUT_URL",
                              f"https://{dns_name}/application/o/mu3lab-homarr/end-session/")
            # Homarr v2 uses its own web port (3000) and does not use the
            # v1 AUTH_OIDC_URI / NEXTAUTH_URL compatibility variables.
            values.setdefault("HOMARR_CONTROL_API_BASE", "http://172.21.0.1:19460")
            from ctl.authentik_blueprints import write_oidc_application_blueprint
            write_oidc_application_blueprint(
                RuntimePaths().root, dns_name, service_id="homarr", name="Homarr",
                private_port=service.private_https_port,
                client_id=values["HOMARR_OIDC_CLIENT_ID"],
                client_secret=values["HOMARR_OIDC_CLIENT_SECRET"],
                redirect_paths=("/api/auth/callback/oidc",),
            )
    env_path.write_text(runtime_env_text(values), encoding="utf-8")
    os.chmod(env_path, 0o600)
    return target


def _wait_healthy(service: Service, timeout: int = 180) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last = "health check did not run"
    while time.monotonic() < deadline:
        try:
            if service.health["kind"] == "http":
                with urllib.request.urlopen(str(service.health["url"]), timeout=5) as response:
                    if 200 <= response.status < 400:
                        return True, f"HTTP {response.status}"
                    last = f"HTTP {response.status}"
            else:
                import socket
                with socket.create_connection(("127.0.0.1", int(service.health["port"])), timeout=5):
                    return True, "TCP ready"
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(2)
    return False, last


def _surfsense_embedding_preflight(store: JobStore, job_id: str, root: Path) -> tuple[bool, str]:
    """Verify SurfSense's fixed internal Ollama embedding dependency."""
    _event(store, job_id, "embedding_check", "Verifying the curated Ollama embedding model.")
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return False, f"Ollama is not available for SurfSense embeddings: {exc}"
    names = {str(item.get("name", "")).split(":", 1)[0]
             for item in payload.get("models", []) if isinstance(item, dict)}
    if "nomic-embed-text" not in names:
        _event(store, job_id, "embedding_model_pull", "Pulling the required local embedding model.")
        ollama = load().get("ollama")
        project = ollama.compose_path(root)
        rc, _ = actions.compose_exec(project, "ollama",
                                     ["ollama", "pull", "nomic-embed-text"],
                                     lambda line: store.append_event(job_id, "log", line),
                                     timeout=600)
        if rc:
            return False, "The required Ollama embedding model could not be prepared."
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/embeddings", method="POST",
        data=json.dumps({"model": "nomic-embed-text", "prompt": "Mu3Lab readiness"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        vector = result.get("embedding")
        if not isinstance(vector, list) or not vector:
            return False, "Ollama returned no embedding vector."
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return False, f"The local embedding probe failed: {exc}"
    return True, "Ollama embedding model is ready."


def _account_username(identity: dict[str, str]) -> str:
    candidate = identity.get("username", "") or identity.get("email", "").split("@", 1)[0]
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate).strip("-.")[:64]
    return value or "mu3lab-admin"


def _nextcloud_installed(project: Path, log) -> bool:
    """Ask Nextcloud itself; config.php exists before installation commits."""
    rc, output = actions.compose_exec(
        project, "app", ["runuser", "-u", "www-data", "--", "php", "occ", "status", "--output=json"],
        log, timeout=90)
    if rc:
        return False
    try:
        start = output.index("{")
        return bool(json.loads(output[start:]).get("installed", False))
    except (ValueError, json.JSONDecodeError, TypeError):
        return False


def _fresh_account_storage(service_id: str, project: Path | None = None, log=None) -> bool:
    data = RuntimePaths().data
    if service_id == "nextcloud":
        # A config file is written before installation commits. The only safe
        # source of truth is Nextcloud's own status command.
        return not (project is not None and log is not None and _nextcloud_installed(project, log))
    directory = (data / "paperless" / "postgres" if service_id == "paperless-ngx"
                 else data / "adventurelog" / "postgres")
    try:
        return not directory.exists() or not any(directory.iterdir())
    except OSError:
        return False


def _verify_bootstrap_account(service_id: str, project: Path,
                              log, expected_username: str = "") -> tuple[bool, str]:
    """Use the pinned Django application's own model layer to verify creation."""
    if service_id == "paperless-ngx":
        container, variable = "webserver", "PAPERLESS_ADMIN_USER"
    elif service_id == "adventurelog":
        container, variable = "app", "DJANGO_ADMIN_USERNAME"
    elif service_id == "nextcloud":
        rc, output = actions.compose_exec(
            project, "app", ["runuser", "-u", "www-data", "--", "php", "occ",
                             "user:list", "--output=json"], log, timeout=90)
        try:
            users = json.loads(output[output.index("{"):]) if rc == 0 else {}
        except (ValueError, json.JSONDecodeError):
            users = {}
        return rc == 0 and expected_username in users, output
    else:
        return True, "No automatic account verification required."
    code = (
        "import os; from django.contrib.auth import get_user_model; "
        f"u=get_user_model().objects.filter(username=os.environ.get('{variable}','')).first(); "
        "print('MU3LAB_ACCOUNT_OK' if u and u.is_superuser else 'MU3LAB_ACCOUNT_MISSING')"
    )
    rc, output = actions.compose_exec(project, container,
                                      ["python", "manage.py", "shell", "-c", code],
                                      log, timeout=90)
    return rc == 0 and "MU3LAB_ACCOUNT_OK" in output, output


def _configure_nextcloud(project: Path, log) -> tuple[bool, str]:
    """Install the latest Nextcloud-compatible apps and configure Authentik.

    ``occ app:install`` resolves the current app-store release compatible
    with the installed Nextcloud server.  Do not compare that result with a
    stale hard-coded app version: a newer compatible Calendar release must
    not make an otherwise healthy Nextcloud installation fail.
    """
    env = read_runtime_env(project / ".env")
    client_id = env.get("NEXTCLOUD_OIDC_CLIENT_ID", "")
    client_secret = env.get("NEXTCLOUD_OIDC_CLIENT_SECRET", "")
    host = env.get("NEXTCLOUD_OVERWRITEHOST", "").split(":", 1)[0]
    if not client_id or not client_secret or not host:
        return False, "Nextcloud OIDC runtime values are incomplete."
    occ = ["runuser", "-u", "www-data", "--", "php", "occ"]
    for app_id in ("calendar", "user_oidc"):
        rc, output = actions.compose_exec(project, "app", [*occ, "app:install", app_id], log, timeout=300)
        if rc and "already installed" not in output.lower():
            return False, f"Nextcloud could not install {app_id}: {redact(output)}"
        rc, output = actions.compose_exec(project, "app", [*occ, "app:enable", app_id], log, timeout=120)
        if rc:
            return False, f"Nextcloud could not enable {app_id}: {redact(output)}"
    rc, output = actions.compose_exec(project, "app", [*occ, "app:list", "--output=json"], log, timeout=120)
    try:
        app_state = json.loads(output[output.index("{"):]) if rc == 0 else {}
    except (ValueError, json.JSONDecodeError):
        app_state = {}
    enabled = app_state.get("enabled", {}) if isinstance(app_state, dict) else {}
    missing = [app_id for app_id in ("calendar", "user_oidc") if not str(enabled.get(app_id, ""))]
    if missing:
        return False, "Nextcloud required app(s) are not enabled: " + ", ".join(missing)
    versions = ", ".join(f"{app_id} {enabled[app_id]}" for app_id in ("calendar", "user_oidc"))
    discovery = f"https://{host}/application/o/mu3lab-nextcloud/.well-known/openid-configuration"
    command = [*occ, "user_oidc:provider", "mu3lab", f"--clientid={client_id}",
               f"--clientsecret={client_secret}", f"--discoveryuri={discovery}",
               "--mapping-uid=preferred_username", "--unique-uid=0"]
    rc, output = actions.compose_exec(project, "app", command, log, timeout=120)
    if rc:
        return False, "Nextcloud could not configure its Authentik provider: " + redact(output)
    rc, output = actions.compose_exec(project, "app", [*occ, "user_oidc:provider", "mu3lab"], log, timeout=120)
    if rc or client_id not in output:
        return False, "Nextcloud did not confirm the Authentik provider configuration."
    for key in ("auto_provision", "soft_auto_provision"):
        rc, output = actions.compose_exec(
            project, "app", [*occ, "config:system:set", "user_oidc", key,
                             "--type=boolean", "--value=true"], log, timeout=120)
        if rc:
            return False, f"Nextcloud could not enable {key}: " + redact(output)
    # Keep local login available during owner migration.  The identity worker
    # changes this to SSO-only only after a real callback proves that the
    # Authentik subject resolves to the existing administrator.
    rc, output = actions.compose_exec(
        project, "app", [*occ, "config:app:set", "user_oidc",
                         "allow_multiple_user_backends", "--value=1"], log, timeout=120)
    if rc:
        return False, "Nextcloud could not stage its safe OIDC migration policy: " + redact(output)
    # Authentik is reached through the node's private Tailnet hostname. The
    # hostname resolves to a CGNAT address, which Nextcloud's DNS pinning
    # otherwise rejects as a local server during discovery/token exchange.
    rc, output = actions.compose_exec(
        project, "app", [*occ, "config:system:set", "allow_local_remote_servers",
                         "--type=boolean", "--value=true"], log, timeout=120)
    if rc:
        return False, "Nextcloud could not permit its private Authentik discovery route: " + redact(output)
    return True, f"Latest compatible Nextcloud apps enabled ({versions}); Calendar and Authentik sign-in are configured."


def _configure_adventurelog_oidc(project: Path, log) -> tuple[bool, str]:
    """Upsert AdventureLog's supported django-allauth SocialApp idempotently."""
    code = (
        "import os; from allauth.socialaccount.models import SocialApp; "
        "from django.contrib.sites.models import Site; "
        "cid=os.environ['ADVENTURELOG_OIDC_CLIENT_ID']; "
        "app,_=SocialApp.objects.update_or_create(provider='openid_connect', provider_id=cid, "
        "defaults={'name':'Authentik','client_id':cid,'secret':os.environ['ADVENTURELOG_OIDC_CLIENT_SECRET'],"
        "'settings':{'server_url':os.environ['ADVENTURELOG_OIDC_DISCOVERY_URL']}}); "
        "app.sites.set(Site.objects.all()); print('MU3LAB_OIDC_APP_OK')"
    )
    rc, output = actions.compose_exec(
        project, "app", ["python", "manage.py", "shell", "-c", code], log, timeout=120)
    if rc or "MU3LAB_OIDC_APP_OK" not in output:
        return False, "AdventureLog could not confirm its Authentik SocialApp: " + redact(output)
    return True, "AdventureLog Authentik SocialApp is configured."


def _linked_owner_verified(service: Service, project: Path,
                           owner: dict[str, str], log) -> bool:
    """Read app-owned identity evidence without reading password hashes."""
    email = str(owner.get("email", "")).strip().lower()
    if not email:
        return False
    expected = hashlib.sha256(email.encode()).hexdigest()
    if service.id == "nextcloud":
        username = str(owner.get("username", "")).strip()
        if not username:
            return False
        rc, output = actions.compose_exec(
            project, "app", ["runuser", "-u", "www-data", "--", "php", "occ",
                             "user:info", username, "--output=json"], log, timeout=120)
        if rc:
            return False
        try:
            profile = json.loads(output[output.index("{"):])
        except (ValueError, json.JSONDecodeError):
            return False
        groups = {str(group) for group in profile.get("groups", [])}
        return (str(profile.get("user_id", "")) == username
                and str(profile.get("email", "")).strip().lower() == email
                and bool(profile.get("enabled")) and "admin" in groups)
    if service.id == "mealie":
        import sqlite3
        database = RuntimePaths().data / "mealie" / "mealie.db"
        try:
            conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
            row = conn.execute(
                "SELECT email, admin, auth_method FROM users WHERE lower(email) = ?", (email,)
            ).fetchone()
            conn.close()
        except (OSError, sqlite3.Error):
            return False
        return bool(row and int(row[1]) == 1 and str(row[2]).upper() == "OIDC")
    if service.id == "lobehub":
        # Better Auth stores the linked provider separately from the user.  Ask
        # PostgreSQL to return only an irreversible email digest plus the two
        # booleans needed for verification; raw identity data and OIDC tokens
        # must never enter job logs.
        expected_md5 = hashlib.md5(email.encode(), usedforsecurity=False).hexdigest()
        query = (
            "SELECT md5(lower(u.email)), u.email_verified, a.provider_id "
            "FROM users u JOIN accounts a ON a.user_id = u.id "
            "WHERE a.provider_id = 'authentik';"
        )
        rc, output = actions.compose_exec(
            project, "postgres",
            ["psql", "-U", "postgres", "-d", "lobehub", "-Atc", query],
            log, timeout=120)
        if rc:
            return False
        return any(
            parts[0] == expected_md5 and parts[1] == "t" and parts[2] == "authentik"
            for line in output.splitlines()
            if len(parts := line.strip().split("|")) == 3
        )
    if service.id not in {"paperless-ngx", "adventurelog"}:
        return False
    container = "webserver" if service.id == "paperless-ngx" else "app"
    code = (
        "import hashlib; from allauth.socialaccount.models import SocialAccount; "
        "print('\\n'.join(hashlib.sha256((x.user.email or '').strip().lower().encode()).hexdigest() "
        "for x in SocialAccount.objects.select_related('user').all() "
        "if x.user.is_superuser and x.user.email))"
    )
    rc, output = actions.compose_exec(
        project, container, ["python", "manage.py", "shell", "-c", code], log, timeout=120)
    return rc == 0 and expected in output.splitlines()


def _enforce_identity_settings(service: Service, project: Path) -> None:
    values = read_runtime_env(project / ".env")
    if service.id == "mealie":
        values["MEALIE_OIDC_AUTO_REDIRECT"] = "true"
        values["MEALIE_ALLOW_PASSWORD_LOGIN"] = "false"
        values["MEALIE_ALLOW_SIGNUP"] = "false"
    elif service.id == "paperless-ngx":
        values["PAPERLESS_DISABLE_REGULAR_LOGIN"] = "true"
        values["PAPERLESS_REDIRECT_LOGIN_TO_SSO"] = "true"
    elif service.id == "adventurelog":
        values["ADVENTURELOG_FORCE_SOCIAL_LOGIN"] = "true"
    else:
        return
    (project / ".env").write_text(runtime_env_text(values), encoding="utf-8")
    os.chmod(project / ".env", 0o600)


def _install_nextcloud_if_needed(project: Path, log) -> tuple[bool, str]:
    """Complete a fresh bootstrap explicitly when Docker auto-install did not.

    The reviewed bootstrap override injects NEXTCLOUD_ADMIN_* into the running
    app. Variable names, rather than secret values, are intentionally used in
    the command so job events cannot disclose credentials.
    """
    if _nextcloud_installed(project, log):
        return True, "Nextcloud base installation is already complete."
    # The image needs a short period to finish Apache/PHP startup after the
    # database and Redis dependencies become healthy.  Do not ask Compose to
    # wait for the app health check here: that check intentionally requires
    # an already-installed instance, which would deadlock a fresh install.
    deadline = time.monotonic() + 120
    last = "Nextcloud occ is not ready yet."
    while time.monotonic() < deadline:
        rc, output = actions.compose_exec(
            project, "app", ["runuser", "-u", "www-data", "--", "php", "occ", "status",
                             "--output=json"], log, timeout=30)
        if rc == 0:
            break
        last = redact(output) or last
        time.sleep(2)
    else:
        return False, "Nextcloud did not become ready for first-run installation: " + last
    script = (
        "set -eu; "
        "test -n \"${NEXTCLOUD_ADMIN_USER:-}\"; "
        "test -n \"${NEXTCLOUD_ADMIN_PASSWORD:-}\"; "
        "runuser -u www-data -- php occ maintenance:install "
        "--database pgsql --database-host db --database-name nextcloud "
        "--database-user nextcloud --database-pass=\"$POSTGRES_PASSWORD\" "
        "--admin-user=\"$NEXTCLOUD_ADMIN_USER\" --admin-pass=\"$NEXTCLOUD_ADMIN_PASSWORD\""
    )
    rc, output = actions.compose_exec(project, "app", ["sh", "-ec", script], log, timeout=300)
    if rc and "already installed" not in output.lower():
        return False, "Nextcloud base installation failed: " + redact(output)
    if not _nextcloud_installed(project, log):
        return False, "Nextcloud did not confirm a completed base installation."
    return True, "Nextcloud base installation completed."


def _image_snapshot(output: str) -> dict[str, str]:
    """Accept Compose's object-per-line or array JSON formats."""
    rows: list[dict] = []
    try:
        value = json.loads(output)
        rows = value if isinstance(value, list) else [value]
    except ValueError:
        for line in output.splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except ValueError:
                continue
    snapshot: dict[str, str] = {}
    for row in rows:
        name = str(row.get("Service") or row.get("Name") or row.get("Repository") or "")
        digest = str(row.get("ID") or row.get("Digest") or row.get("Image") or "")
        if name and digest:
            snapshot[name] = digest
    return snapshot


def _pin_images(project: Path) -> dict[str, str]:
    """Resolve every declared image and write a Compose digest override."""
    definition = yaml.safe_load((project / "docker-compose.yml").read_text(encoding="utf-8")) or {}
    services = definition.get("services") or {}
    snapshot: dict[str, str] = {}
    override: dict[str, dict[str, dict[str, str]]] = {"services": {}}
    for service_name, contract in services.items():
        if not isinstance(contract, dict) or not contract.get("image"):
            continue
        image = str(contract["image"])
        if "@sha256:" in image:
            pinned = image
        else:
            rc, output = actions.docker_image_digest(image)
            pinned = output.strip()
            if rc or "@sha256:" not in pinned:
                raise RuntimeError(f"image {service_name} did not resolve to an immutable digest")
        snapshot[str(service_name)] = pinned
        override["services"][str(service_name)] = {"image": pinned}
    if not snapshot:
        raise RuntimeError("the curated deployment did not declare any images")
    target = project / "docker-compose.digest.yml"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(override, sort_keys=True), encoding="utf-8")
    temporary.replace(target)
    return snapshot


def _fail(store: JobStore, state: ControlState | None, job_id: str, service_id: str,
          actor: str, stage: str, code: str, detail: str) -> None:
    safe = redact(detail)
    error = {"code": code, "stage": stage, "message": safe,
             "retryable": True, "recommended_action": "Review the app logs and retry setup."}
    if state and service_id:
        state.set_installation(service_id, "failed", job_id=job_id, error=error)
        try:
            current = state.initialization(service_id)
            if current and current["state"] in {"pending", "initializing"}:
                state.set_initialization(service_id, str(current["mode"]), "failed",
                                         job_id=job_id, error=error)
        except ValueError:
            pass
    store.transition(job_id, "failed", actor=actor, detail=safe,
                     error_code=code, step_id=stage)


def _append_runtime_diagnostics(store: JobStore, job_id: str, project: Path) -> None:
    """Attach a small, redacted container-log tail to a failed job.

    Compose's `--wait` output says only that a dependency became unhealthy;
    the useful reason is normally in the application container.  Keep this
    bounded so batch progress remains readable and secrets never enter audit
    storage.
    """
    try:
        rc, output = actions.compose_logs(project, lambda _line: None, tail=50)
        if output:
            store.append_event(job_id, "diagnostic", redact(output)[-4000:])
    except OSError:
        pass


def _failure_code(default: str, detail: str) -> str:
    """Turn common Docker failure text into stable, user-actionable codes."""
    value = detail.lower()
    if "password authentication failed" in value or "authentication failed for user" in value:
        return "database_auth_failed"
    if "unhealthy" in value or "dependency failed to start" in value:
        return "dependency_unhealthy"
    if "timed out" in value or "wait timeout" in value:
        return "application_health_timeout"
    return default


def _install(store: JobStore, state: ControlState | None, job: dict,
             service: Service, registry, actor: str, root: Path) -> None:
    job_id = str(job["id"])
    account_mode = str(service.account.get("mode", "none"))
    account_identity = workflow_secrets.job_identity(job_id)
    if service.stage != "optional":
        _fail(store, state, job_id, service.id, actor, "validate_service",
              "install_not_optional", "This service is installed by the core-suite workflow.")
        return
    from ctl.service_config import missing_required
    missing = missing_required(service)
    if missing:
        _fail(store, state, job_id, service.id, actor, "validate_configuration",
              "configuration_required",
              "Required configuration is missing: " + ", ".join(missing))
        return
    if account_mode == "environment_bootstrap" and not account_identity:
        _fail(store, state, job_id, service.id, actor, "account_preflight",
              "identity_email_missing",
              "A verified Authentik email is required. Sign out, sign back in, and retry.")
        return
    if state:
        initialization_state = ("initializing" if account_mode == "environment_bootstrap"
                                else "awaiting_user" if account_mode in {
                                    "oidc_first_login", "browser_registration", "local_account_manual"
                                } else "not_required")
        state.set_initialization(service.id, account_mode, initialization_state,
                                 job_id=job_id,
                                 owner_uid=str((account_identity or {}).get("owner_uid", "")))
    prior_installation = state.installation(service.id) if state else None
    if state:
        state.set_installation(service.id, "installing", job_id=job_id)
    _event(store, job_id, "validate_service", "Curated service contract accepted.")
    try:
        _event(store, job_id, "materialize_runtime", "Preparing the curated runtime project.")
        project = _materialize(service, root)
    except OSError as exc:
        _fail(store, state, job_id, service.id, actor, "materialize_runtime",
              "materialize_failed", f"Runtime project could not be prepared: {exc}")
        return
    if service.id == "surfsense":
        ready, detail = _surfsense_embedding_preflight(store, job_id, root)
        if not ready:
            _fail(store, state, job_id, service.id, actor, "embedding_check",
                  "embedding_probe_failed", detail)
            return
    log = lambda line: store.append_event(job_id, "log", line)
    _event(store, job_id, "validate_configuration", "Validating generated Compose configuration.")
    rc, output = actions.compose_config(project, log)
    if rc:
        _fail(store, state, job_id, service.id, actor, "validate_configuration",
              "compose_invalid", f"Compose validation failed: {output}")
        return
    _event(store, job_id, "pull_images", "Pulling pinned application images.")
    rc, output = actions.compose_pull(project, log)
    if rc:
        _fail(store, state, job_id, service.id, actor, "pull_images",
              "image_pull_failed", f"Image pull failed: {output}")
        return
    try:
        _event(store, job_id, "resolve_digests", "Resolving images to immutable OCI digests.")
        image_snapshot = _pin_images(project)
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        _fail(store, state, job_id, service.id, actor, "resolve_digests",
              "image_digest_failed", str(exc))
        return
    if state:
        state.set_installation(service.id, "starting", job_id=job_id,
                               manifest_version="3", image_digests=image_snapshot)
    _event(store, job_id, "start_service", "Starting application containers.")
    wait_timeout = 900 if service.id in {"surfsense", "nextcloud", "lobehub"} else 120
    # A fresh Nextcloud intentionally reports unhealthy until its database
    # installation has completed.  Waiting on that healthcheck before the
    # explicit `occ maintenance:install` below deadlocks the workflow: Docker
    # waits for installed=true while the worker is waiting for Docker.  Start
    # it without Compose's health wait, complete the base install, then the
    # normal application health verification can require installed=true.
    initial_wait_timeout = None if service.id == "nextcloud" else wait_timeout
    bootstrap_env: dict[str, str] | None = None
    bootstrap_files: list[Path] = []
    password = ""
    fresh_account = account_mode == "environment_bootstrap" and _fresh_account_storage(service.id, project, log)
    if fresh_account and account_identity:
        password = workflow_secrets.generate_password()
        bootstrap_env = {
            "MU3LAB_BOOTSTRAP_USERNAME": _account_username(account_identity),
            "MU3LAB_BOOTSTRAP_EMAIL": account_identity["email"],
            "MU3LAB_BOOTSTRAP_PASSWORD": password,
        }
        bootstrap_files = [project / "docker-compose.bootstrap.yml"]
        _event(store, job_id, "account_bootstrap", "Creating the initial application administrator.")
    elif account_mode == "environment_bootstrap" and state:
        state.set_initialization(service.id, account_mode, "existing_account", job_id=job_id,
                                 owner_uid=str((account_identity or {}).get("owner_uid", "")))
    from ctl.compute import compose_overrides
    # Nextcloud cron depends on a healthy app.  A brand-new app cannot be
    # healthy until `occ maintenance:install` completes, so start only the
    # dependencies and app first.  Cron is started after bootstrap cleanup.
    requested_services = ["db", "redis", "app"] if service.id == "nextcloud" else None
    rc, output = actions.compose_up(
        project, log, timeout=wait_timeout + 300, wait_timeout=initial_wait_timeout,
        env=bootstrap_env, extra_files=[*compose_overrides(service.id, project), *bootstrap_files],
        recreate=bool(prior_installation), services=requested_services)
    if rc:
        _append_runtime_diagnostics(store, job_id, project)
        _fail(store, state, job_id, service.id, actor, "start_service",
              _failure_code("compose_start_failed", output), f"Application start failed: {output}")
        return
    if service.id == "nextcloud":
        _event(store, job_id, "base_installation", "Confirming the Nextcloud base installation.")
        installed, install_detail = _install_nextcloud_if_needed(project, log)
        if not installed:
            _fail(store, state, job_id, service.id, actor, "base_installation",
                  "nextcloud_install_incomplete", install_detail)
            return
    if fresh_account:
        account_verified, account_detail = _verify_bootstrap_account(
            service.id, project, log, str(bootstrap_env.get("MU3LAB_BOOTSTRAP_USERNAME", "")))
        _event(store, job_id, "account_cleanup",
               "Removing bootstrap variables from the running application container.")
        rc, output = actions.compose_up(
            project, log, timeout=wait_timeout + 300, wait_timeout=wait_timeout,
            extra_files=compose_overrides(service.id, project), recreate=True)
        if rc:
            _fail(store, state, job_id, service.id, actor, "account_cleanup",
                  "account_verification_failed",
                  f"The administrator was created but its bootstrap environment could not be removed: {output}")
            return
        if not account_verified:
            _fail(store, state, job_id, service.id, actor, "account_verification",
                  "account_verification_failed",
                  "The application became healthy but did not confirm the generated administrator account. "
                  + redact(account_detail))
            return
    if state:
        state.set_installation(service.id, "verifying", job_id=job_id,
                               manifest_version="3", image_digests=image_snapshot)
    _event(store, job_id, "verify_application", "Waiting for application health.")
    healthy, detail = _wait_healthy(service)
    if not healthy:
        _append_runtime_diagnostics(store, job_id, project)
        _fail(store, state, job_id, service.id, actor, "verify_application",
              _failure_code("health_check_failed", detail), f"Application did not become healthy: {detail}")
        return
    if service.id == "nextcloud":
        _event(store, job_id, "configure_application", "Configuring Calendar and Authentik sign-in.")
        configured, detail = _configure_nextcloud(project, log)
        if not configured:
            _fail(store, state, job_id, service.id, actor, "configure_application",
                  "nextcloud_configuration_failed", detail)
            return
    _event(store, job_id, "configure_route", "Publishing private HTTPS route.")
    routed, detail = apply_route(registry, service, root, log)
    if not routed:
        _fail(store, state, job_id, service.id, actor, "configure_route",
              "route_configuration_failed", detail)
        return
    if state:
        state.set_installation(service.id, "running", job_id=job_id,
                               manifest_version="3", image_digests=image_snapshot,
                               route_state="ready")
        from ctl.identity import mode_for
        if mode_for(service) == "native_oidc":
            state.set_service_identity(
                service.id, "native_oidc", "migration_required",
                owner_uid=str((account_identity or {}).get("owner_uid", "")), job_id=job_id,
                detail="Open the application once through Authentik to verify owner linking before local login is disabled.")
    if fresh_account and account_identity and state:
        host = ""
        try:
            from ctl.service_state import tailnet_dns_name
            host = tailnet_dns_name()
        except (OSError, ValueError):
            pass
        login_url = f"https://{host}:{service.private_https_port}" if host and service.private_https_port else ""
        handoff = workflow_secrets.create_handoff(
            service_id=service.id, job_id=job_id, owner_uid=account_identity["owner_uid"],
            username=bootstrap_env["MU3LAB_BOOTSTRAP_USERNAME"], email=account_identity["email"],
            password=password, login_url=login_url)
        state.add_handoff(handoff["id"], service.id, job_id, account_identity["owner_uid"],
                          handoff["created_at"], handoff["expires_at"])
        state.set_initialization(service.id, account_mode, "ready", job_id=job_id,
                                 owner_uid=account_identity["owner_uid"], handoff_id=handoff["id"])
    _event(store, job_id, "finalize", "Application and private route verified.")
    if service.id == "lobehub":
        from ctl.lobehub_ops import reconcile
        ready, detail = reconcile(log)
        if not ready:
            _fail(store, state, job_id, service.id, actor, "lobehub_policy",
                  "lobehub_policy_failed", detail)
            return
    from ctl.mcp_ops import sync_application
    if not sync_application(service.id, running=True, root=root, log=log):
        log("One enabled MCP needs attention after application installation.")
    store.transition(job_id, "succeeded", actor=actor,
                     detail=f"{service.name} installed and verified.", step_id="finalize")


def _repair(store: JobStore, state: ControlState | None, job: dict,
            service: Service, registry, actor: str, root: Path) -> None:
    """Recreate an optional runtime from its current curated manifest.

    This is deliberately a repair, not an install: it never pulls images,
    creates accounts, or removes volumes.  It is the safe migration path for
    deployments that previously attached generic db/redis aliases to the
    shared backend network.
    """
    job_id = str(job["id"])
    if service.stage == "core":
        job_id = str(job["id"])
        store.transition(job_id, "running", actor=actor,
                         detail=f"Repairing private routes for {service.name}.", step_id="configure_route")
        log = lambda line: store.append_event(job_id, "log", line)
        from ctl.routes import reconcile_core
        routed, detail = reconcile_core(registry, root, log)
        if not routed:
            _fail(store, state, job_id, service.id, actor, "configure_route",
                  "route_configuration_failed", detail)
            return
        store.append_event(job_id, "step.completed", "core_routes:reconciled")
        store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
        return
    if service.stage != "optional":
        _fail(store, state, job_id, service.id, actor, "validate_service",
              "repair_not_optional", "Only optional applications have a repairable runtime project.")
        return
    store.transition(job_id, "running", actor=actor,
                     detail=f"Repairing {service.name} without changing persistent data.", step_id="materialize_runtime")
    try:
        project = _materialize(service, root)
    except OSError as exc:
        _fail(store, state, job_id, service.id, actor, "materialize_runtime", "materialize_failed", str(exc))
        return
    log = lambda line: store.append_event(job_id, "log", line)
    _event(store, job_id, "validate_configuration", "Validating the repaired curated Compose configuration.")
    rc, output = actions.compose_config(project, log)
    if rc:
        _fail(store, state, job_id, service.id, actor, "validate_configuration", "compose_invalid", output)
        return
    _event(store, job_id, "repair_runtime", "Recreating containers with the current private network layout.")
    from ctl.compute import compose_overrides
    wait_timeout = 900 if service.id in {"surfsense", "nextcloud"} else 180
    rc, output = actions.compose_up(project, log, timeout=wait_timeout + 300, wait_timeout=wait_timeout,
                                    extra_files=compose_overrides(service.id, project), recreate=True)
    if rc:
        _append_runtime_diagnostics(store, job_id, project)
        _fail(store, state, job_id, service.id, actor, "repair_runtime",
              _failure_code("compose_repair_failed", output), f"Application repair failed: {output}")
        return
    healthy, detail = _wait_healthy(service, timeout=wait_timeout)
    if not healthy:
        _append_runtime_diagnostics(store, job_id, project)
        _fail(store, state, job_id, service.id, actor, "verify_application",
              _failure_code("health_check_failed", detail), f"Application did not become healthy after repair: {detail}")
        return
    routed, detail = apply_route(registry, service, root, log)
    if not routed:
        _fail(store, state, job_id, service.id, actor, "configure_route", "route_configuration_failed", detail)
        return
    if state:
        state.set_installation(service.id, "running", job_id=job_id, manifest_version="4", route_state="ready")
    if service.id == "lobehub":
        from ctl.lobehub_ops import reconcile
        ready, detail = reconcile(log)
        if not ready:
            _fail(store, state, job_id, service.id, actor, "lobehub_policy",
                  "lobehub_policy_failed", detail)
            return
    from ctl.mcp_ops import sync_application
    if not sync_application(service.id, running=True, root=root, log=log):
        log("One enabled MCP needs attention after application repair.")
    store.transition(job_id, "succeeded", actor=actor,
                     detail=f"{service.name} repaired with the current curated runtime.", step_id="complete")


def _configure_identity(store: JobStore, state: ControlState | None, job: dict,
                        service: Service, registry, actor: str, root: Path) -> None:
    """Materialize identity configuration without silently claiming migration success."""
    from ctl.identity import mode_for, reconcile_blueprints

    job_id = str(job["id"])
    mode = mode_for(service)
    owner = workflow_secrets.job_identity(job_id) or {}
    owner_uid = str(owner.get("owner_uid", ""))
    store.transition(job_id, "running", actor=actor,
                     detail=f"Reconciling sign-in for {service.name}.", step_id="identity_configuration")
    try:
        if mode == "native_oidc":
            project = project_path(service, root)
            if service.stage == "optional":
                project = _materialize(service, root)
            from ctl.service_state import tailnet_dns_name
            written = reconcile_blueprints(registry, tailnet_dns_name())
            if service.id not in written:
                raise ValueError("The persisted OIDC client configuration is incomplete.")
            installation = state.installation(service.id) if state else None
            linked = bool(
                installation and installation.get("state") == "running"
                and _linked_owner_verified(
                    service, project, owner,
                    lambda line: store.append_event(job_id, "log", line)))
            if linked:
                _enforce_identity_settings(service, project)
            if installation and installation.get("state") == "running":
                from ctl.compute import compose_overrides
                rc, output = actions.compose_up(
                    project, lambda line: store.append_event(job_id, "log", line),
                    timeout=600, wait_timeout=300,
                    extra_files=compose_overrides(service.id, project), recreate=True)
                if rc:
                    raise ValueError("The application could not apply its staged OIDC configuration: " + redact(output))
            if service.id == "adventurelog" and installation and installation.get("state") == "running":
                ok, configured_detail = _configure_adventurelog_oidc(
                    project_path(service, root),
                    lambda line: store.append_event(job_id, "log", line))
                if not ok:
                    raise ValueError(configured_detail)
            detail = ("OIDC configuration is installed. Complete a real Authentik callback so Mu3Lab "
                      "can verify the existing owner and administrator role before disabling local login.")
            target = "ready" if linked or service.id == "homarr" else "migration_required"
            if service.id == "homarr":
                detail = ("Authentik OIDC is configured and Homarr no longer offers local credentials. "
                          "Complete the Authentik redirect to finish Homarr's first-run group setup.")
            elif linked:
                detail = ("Verified Authentik account linking; password login is disabled."
                          if service.id == "lobehub" else
                          "Verified Authentik account linking and administrator role; browser password login is disabled.")
        elif mode == "trusted_header":
            detail = "Authentik trusted-header access is configured; live route health remains authoritative."
            target = "ready"
        elif mode == "proxy_gate":
            detail = "Authentik protects this route, but the application has no native per-user OIDC session."
            target = "ready"
        else:
            detail = (service.identity_note or
                      "This application does not support Mu3Lab-managed native Authentik sign-in.")
            target = "unsupported"
    except (OSError, ValueError, RegistryError) as exc:
        detail = redact(str(exc))
        if state:
            state.set_service_identity(service.id, mode, "degraded", owner_uid=owner_uid,
                                       job_id=job_id, detail=detail,
                                       error={"code": "identity_configuration_failed", "message": detail})
        _fail(store, state, job_id, service.id, actor, "identity_configuration",
              "identity_configuration_failed", detail)
        return
    if state:
        state.set_service_identity(service.id, mode, target, owner_uid=owner_uid,
                                   job_id=job_id, detail=detail,
                                   verified=target == "ready")
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")


def execute_claimed(store: JobStore, job: dict, worker_id: str, root: Path) -> None:
    job_id = str(job["id"])
    service_id = str(job.get("service_id") or "")
    action = str(job.get("action") or "")
    actor = str(job.get("actor") or worker_id)
    state = ControlState.runtime()
    try:
        registry = load()
        service = registry.get(service_id)
    except RegistryError as exc:
        _fail(store, state, job_id, service_id, worker_id, "validate_service",
              "unknown_service", f"Unknown curated service: {exc}")
        return
    if action not in SUPPORTED_ACTIONS or service.is_blocked:
        _fail(store, state, job_id, service.id, worker_id, "validate_service",
              "unsupported_action", "The worker rejected an unsupported service action.")
        return
    if action in {"install", "retry_setup"}:
        _install(store, state, job, service, registry, actor, root)
        return
    if action == "repair":
        _repair(store, state, job, service, registry, actor, root)
        return
    if action == "configure_identity":
        _configure_identity(store, state, job, service, registry, actor, root)
        return
    if action == "reset":
        if service.stage != "optional":
            _fail(store, state, job_id, service.id, actor, "validate_service",
                  "reset_not_optional", "Only optional applications can be reset from the catalog.")
            return
        store.transition(job_id, "running", actor=actor,
                         detail=f"Cleaning failed {service.name} installation.", step_id="reset_cleanup")
        log = lambda line: store.append_event(job_id, "log", line)
        ok, detail = reset_failed_application(service, root, log)
        if not ok:
            _fail(store, state, job_id, service.id, actor, "reset_cleanup",
                  "reset_cleanup_failed", detail)
            return
        if state:
            state.reset_service(service.id)
        store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
        return
    if action == "stop" and service.lifecycle == "always_on":
        _fail(store, state, job_id, service.id, worker_id, "validate_service",
              "always_on", "Always-on infrastructure cannot be stopped from the dashboard.")
        return
    project = project_path(service, root)
    if not (project / "docker-compose.yml").is_file():
        _fail(store, state, job_id, service.id, worker_id, "validate_service",
              "manifest_missing", "This curated service has no deployable Compose manifest.")
        return
    store.transition(job_id, "running", actor=actor,
                     detail=f"{action.title()} started for {service.name}.", step_id="compose")
    log = lambda line: store.append_event(job_id, "log", line)
    from ctl.compute import compose_overrides
    runtime_env = None
    if service.stage == "core":
        runtime = RuntimePaths()
        runtime_env = {"MU3LAB_ENV_FILE": str(runtime.projects / service.id / ".env"),
                       "MU3LAB_DATA_ROOT": str(runtime.data)}
        if service.id == "litellm":
            runtime_env["MU3LAB_LITELLM_CONFIG"] = str(runtime.projects / "litellm" / "config.yaml")
        elif service.id == "freellmapi":
            runtime_env["MU3LAB_FREELLMAPI_CONFIG"] = str(runtime.projects / "freellmapi" / "freellmapi.config.json")
    if action == "stop":
        from ctl.mcp_ops import sync_application
        if not sync_application(service.id, running=False, root=root, log=log):
            _fail(store, state, job_id, service.id, actor, "stop_mcp",
                  "mcp_stop_failed", "An enabled MCP could not stop safely.")
            return
    rc, output = actions.compose_action(
        project, action, log, env=runtime_env,
        extra_files=compose_overrides(service.id, project))
    if rc:
        _fail(store, state, job_id, service.id, actor, "compose",
              "compose_failed", f"{service.name} {action} failed: {output}")
        return
    target_state = "stopped" if action == "stop" else "running"
    if target_state == "running":
        healthy, detail = _wait_healthy(service)
        if not healthy:
            _fail(store, state, job_id, service.id, actor, "verify_application",
                  "health_check_failed", f"Application did not become healthy: {detail}")
            return
        if service.id == "adventurelog":
            oidc_env = read_runtime_env(project / ".env")
            if oidc_env.get("ADVENTURELOG_OIDC_CLIENT_ID"):
                configured, detail = _configure_adventurelog_oidc(project, log)
                if not configured:
                    _fail(store, state, job_id, service.id, actor, "identity_configuration",
                          "identity_configuration_failed", detail)
                    return
        from ctl.mcp_ops import sync_application
        if not sync_application(service.id, running=True, root=root, log=log):
            log("One enabled MCP needs attention after application start.")
        if service.id == "lobehub":
            from ctl.lobehub_ops import reconcile
            ready, detail = reconcile(log)
            if not ready:
                _fail(store, state, job_id, service.id, actor, "lobehub_policy",
                      "lobehub_policy_failed", detail)
                return
    if state:
        state.set_installation(service.id, target_state, job_id=job_id,
                               route_state="ready")
    store.append_event(job_id, "step.completed", f"compose:{action}")
    store.transition(job_id, "succeeded", actor=actor,
                     detail=f"{service.name} {action} completed.", step_id="complete")
