"""Durable, registry-bound application installation and lifecycle execution."""

from __future__ import annotations

import json
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

SUPPORTED_ACTIONS = frozenset({"install", "retry_setup", "start", "stop", "restart", "repair", "reset"})


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
        return ["retry_setup", "restart"]
    if state == "stopped":
        return ["start", "restart"]
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
    elif service.id == "adventurelog":
        values.setdefault("POSTGRES_PASSWORD", token_secrets.token_urlsafe(36))
        values.setdefault("SECRET_KEY", token_secrets.token_urlsafe(48))
    elif service.id == "paperless-ngx":
        values.setdefault("PAPERLESS_DBPASS", token_secrets.token_urlsafe(36))
        values.setdefault("PAPERLESS_SECRET_KEY", token_secrets.token_urlsafe(48))
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
        elif service.id == "paperless-ngx":
            values.setdefault("PAPERLESS_URL", public_url)
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
    """Install the pinned apps and configure the curated Authentik provider."""
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
    if str(enabled.get("calendar", "")) != "6.5.4" or str(enabled.get("user_oidc", "")) != "8.11.0":
        return False, "Nextcloud Calendar 6.5.4 and user_oidc 8.11.0 must both be enabled."
    discovery = f"https://{host}/application/o/mu3lab-nextcloud/.well-known/openid-configuration"
    command = [*occ, "user_oidc:provider", "mu3lab", f"--clientid={client_id}",
               f"--clientsecret={client_secret}", f"--discoveryuri={discovery}"]
    rc, output = actions.compose_exec(project, "app", command, log, timeout=120)
    if rc:
        return False, "Nextcloud could not configure its Authentik provider: " + redact(output)
    rc, output = actions.compose_exec(project, "app", [*occ, "user_oidc:provider", "mu3lab"], log, timeout=120)
    if rc or client_id not in output:
        return False, "Nextcloud did not confirm the Authentik provider configuration."
    return True, "Calendar and Authentik sign-in are configured."


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
    wait_timeout = 900 if service.id in {"surfsense", "nextcloud"} else 120
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
    store.transition(job_id, "succeeded", actor=actor,
                     detail=f"{service.name} repaired with the current curated runtime.", step_id="complete")


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
    rc, output = actions.compose_action(
        project, action, log, extra_files=compose_overrides(service.id, project))
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
    if state:
        state.set_installation(service.id, target_state, job_id=job_id,
                               route_state="ready")
    store.append_event(job_id, "step.completed", f"compose:{action}")
    store.transition(job_id, "succeeded", actor=actor,
                     detail=f"{service.name} {action} completed.", step_id="complete")
