"""Durable, registry-bound application installation and lifecycle execution."""

from __future__ import annotations

import json
import os
import secrets as token_secrets
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from ctl import actions
from ctl.control_state import ControlState
from ctl.jobs import JobStore, redact
from ctl.registry import RegistryError, Service, load
from ctl.routes import apply as apply_route
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text

SUPPORTED_ACTIONS = frozenset({"install", "retry_setup", "start", "stop", "restart"})


def project_path(service: Service, root: Path) -> Path:
    """Prefer a materialized optional project; core uses its checkout contract."""
    runtime = RuntimePaths().projects / service.id
    if service.stage == "optional" and (runtime / "docker-compose.yml").is_file():
        return runtime
    return service.compose_path(root)


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
        result = ["restart"]
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
    store.transition(job_id, "failed", actor=actor, detail=safe,
                     error_code=code, step_id=stage)


def _install(store: JobStore, state: ControlState | None, job: dict,
             service: Service, registry, actor: str, root: Path) -> None:
    job_id = str(job["id"])
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
    wait_timeout = 900 if service.id == "surfsense" else 120
    rc, output = actions.compose_up(project, log, timeout=wait_timeout + 300,
                                    wait_timeout=wait_timeout)
    if rc:
        _fail(store, state, job_id, service.id, actor, "start_service",
              "compose_start_failed", f"Application start failed: {output}")
        return
    if state:
        state.set_installation(service.id, "verifying", job_id=job_id,
                               manifest_version="3", image_digests=image_snapshot)
    _event(store, job_id, "verify_application", "Waiting for application health.")
    healthy, detail = _wait_healthy(service)
    if not healthy:
        _fail(store, state, job_id, service.id, actor, "verify_application",
              "health_check_failed", f"Application did not become healthy: {detail}")
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
    _event(store, job_id, "finalize", "Application and private route verified.")
    store.transition(job_id, "succeeded", actor=actor,
                     detail=f"{service.name} installed and verified.", step_id="finalize")


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
