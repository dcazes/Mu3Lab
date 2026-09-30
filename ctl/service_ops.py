"""Durable, registry-bound application installation and lifecycle execution."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import yaml

from ctl import actions, image_fetch, job_guard, onboarding_state, workflow_secrets
from ctl.control_state import ControlState
from ctl.image_downloads import ImageDownloadStore
from ctl.jobs import JobStore, redact
from ctl.lifecycle.accounts import (
    account_username,
    enforce_identity_settings,
    fresh_account_storage,
    linked_owner_verified,
    verify_bootstrap_account,
)
from ctl.lifecycle.health import wait_healthy
from ctl.lifecycle.integrations import configure_adventurelog_oidc, surfsense_embedding_preflight
from ctl.lifecycle.materialize import materialize, pin_images
from ctl.lifecycle.nextcloud import configure_nextcloud, install_nextcloud_if_needed
from ctl.registry import Registry, RegistryError, Service, load
from ctl.routes import apply as apply_route
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text

SUPPORTED_ACTIONS = frozenset(
    {
        "install",
        "retry_setup",
        "start",
        "stop",
        "restart",
        "repair",
        "reset",
        "configure_identity",
        "uninstall",
        "uninstall_delete_data",
    }
)
UNINSTALL_ACTIONS = frozenset({"uninstall", "uninstall_delete_data"})


def project_path(service: Service, root: Path) -> Path:
    """Use a materialized application project when it owns the running Compose stack."""
    runtime = RuntimePaths().projects / service.id
    if (service.stage == "optional" or service.id == "lobehub") and (runtime / "docker-compose.yml").is_file():
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
        for key in (
            "MU3LAB_BOOTSTRAP_USERNAME",
            "MU3LAB_BOOTSTRAP_EMAIL",
            "MU3LAB_BOOTSTRAP_PASSWORD",
            "NEXTCLOUD_ADMIN_USER",
            "NEXTCLOUD_ADMIN_PASSWORD",
        ):
            values.pop(key, None)
        env_path.write_text(runtime_env_text(values), encoding="utf-8")
        os.chmod(env_path, 0o600)
    return True, "Failed containers and temporary setup files removed; persistent data preserved."


def allowed_actions(service: Service, state: str) -> list[str]:
    """Actions for this state. "uninstall" also admits "uninstall_delete_data"."""
    if service.is_blocked:
        return []
    if service.stage == "optional" and state in {"planned", "not_installed"}:
        return ["install"]
    uninstall = ["uninstall"] if service.stage == "optional" else []
    if state in {"failed", "needs_attention", "needs_setup", "degraded"}:
        return ["repair", "restart"] if service.stage == "core" else ["retry_setup", "restart", *uninstall]
    if state == "stopped":
        return ["start", *uninstall]
    if state in {"ready", "running", "starting", "configured", "installed"}:
        result = ["repair", "restart"] if service.stage == "optional" else ["restart"]
        if service.lifecycle != "always_on":
            result.insert(0, "stop")
        return [*result, *uninstall]
    return []


def _event(store: JobStore, job_id: str, stage: str, detail: str) -> None:
    store.append_event(job_id, "stage", f"{stage}: {detail}")


def _job_log(store: JobStore, job_id: str) -> Callable[[str], None]:
    return lambda line: store.append_event(job_id, "log", line)


def _fail(
    store: JobStore,
    state: ControlState | None,
    job_id: str,
    service_id: str,
    actor: str,
    stage: str,
    code: str,
    detail: str,
) -> None:
    safe = redact(detail)
    error = {
        "code": code,
        "stage": stage,
        "message": safe,
        "retryable": True,
        "recommended_action": "Review the app logs and retry setup.",
    }
    if state and service_id:
        state.set_installation(service_id, "failed", job_id=job_id, error=error)
        try:
            current = state.initialization(service_id)
            if current and current["state"] in {"pending", "initializing"}:
                state.set_initialization(service_id, str(current["mode"]), "failed", job_id=job_id, error=error)
        except ValueError:
            pass
    store.transition(job_id, "failed", actor=actor, detail=safe, error_code=code, step_id=stage)


def _append_runtime_diagnostics(store: JobStore, job_id: str, project: Path) -> None:
    """Attach a small, redacted container-log tail to a failed job.

    Compose's `--wait` output says only that a dependency became unhealthy;
    the useful reason is normally in the application container.  Keep this
    bounded so batch progress remains readable and secrets never enter audit
    storage.
    """
    try:
        _rc, output = actions.compose_logs(project, lambda _line: None, tail=50)
        if output:
            store.append_event(job_id, "diagnostic", redact(output)[-4000:])
    except OSError:
        pass


def _sync_chat_assistants(log: Callable[[str], None]) -> None:
    """Show an app's LobeChat assistant only while the app is installed.

    A convenience: a failure is logged and never fails the app's own job.
    """
    from ctl.lobehub_ops import sync_agents

    ok, detail = sync_agents(log)
    if not ok:
        log(f"LobeChat assistants were not updated: {detail}")


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


def _install(
    store: JobStore, state: ControlState | None, job: dict, service: Service, registry: Registry, actor: str, root: Path
) -> None:
    job_id = str(job["id"])
    account_mode = str(service.account.get("mode", "none"))
    account_identity = workflow_secrets.job_identity(job_id)
    if service.stage != "optional":
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "validate_service",
            "install_not_optional",
            "This service is installed by the core-suite workflow.",
        )
        return
    from ctl.service_config import missing_required

    missing = missing_required(service)
    if missing:
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "validate_configuration",
            "configuration_required",
            "Required configuration is missing: " + ", ".join(missing),
        )
        return
    if (
        account_mode in {"environment_bootstrap", "api_bootstrap"} or service.id == "actual-budget"
    ) and not account_identity:
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "account_preflight",
            "identity_email_missing",
            "A verified Authentik email is required. Sign out, sign back in, and retry.",
        )
        return
    if account_identity:
        account_identity = onboarding_state.remember_owner(service.id, account_identity, RuntimePaths())
    if state:
        initialization_state = (
            "initializing"
            if account_mode in {"environment_bootstrap", "api_bootstrap"}
            else "awaiting_user"
            if account_mode in {"oidc_first_login", "browser_registration", "local_account_manual"}
            else "not_required"
        )
        state.set_initialization(
            service.id,
            account_mode,
            initialization_state,
            job_id=job_id,
            owner_uid=account_identity["owner_uid"] if account_identity else "",
        )
    prior_installation = state.installation(service.id) if state else None
    if state:
        state.set_installation(service.id, "installing", job_id=job_id)
    _event(store, job_id, "validate_service", "Curated service contract accepted.")
    try:
        _event(store, job_id, "materialize_runtime", "Preparing the curated runtime project.")
        if service.id == "actual-budget" and account_identity:
            target = RuntimePaths().projects / service.id
            target.mkdir(parents=True, exist_ok=True)
            values = read_runtime_env(target / ".env")
            # Preserve the guard across retries; finalization explicitly clears it.
            if "MU3LAB_INITIAL_OWNER_USERNAME" not in values:
                values["MU3LAB_INITIAL_OWNER_USERNAME"] = account_identity["username"]
                (target / ".env").write_text(runtime_env_text(values), encoding="utf-8")
                os.chmod(target / ".env", 0o600)
        project = materialize(service, root)
    except OSError as exc:
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "materialize_runtime",
            "materialize_failed",
            f"Runtime project could not be prepared: {exc}",
        )
        return
    if service.id == "surfsense":
        ready, detail = surfsense_embedding_preflight(store, job_id, root)
        if not ready:
            _fail(store, state, job_id, service.id, actor, "embedding_check", "embedding_probe_failed", detail)
            return
    log = _job_log(store, job_id)
    _event(store, job_id, "validate_configuration", "Validating generated Compose configuration.")
    rc, output = actions.compose_config(project, log)
    if rc:
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "validate_configuration",
            "compose_invalid",
            f"Compose validation failed: {output}",
        )
        return
    _event(store, job_id, "pull_images", "Downloading pinned application images.")
    rc, output = _download_images(project, service.id, job_id, log)
    if rc:
        _fail(
            store, state, job_id, service.id, actor, "pull_images", "image_pull_failed", f"Image pull failed: {output}"
        )
        return
    try:
        _event(store, job_id, "resolve_digests", "Resolving images to immutable OCI digests.")
        image_snapshot = pin_images(project)
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        _fail(store, state, job_id, service.id, actor, "resolve_digests", "image_digest_failed", str(exc))
        return
    if state:
        state.set_installation(
            service.id, "starting", job_id=job_id, manifest_version="3", image_digests=image_snapshot
        )
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
    bootstrap_username = ""
    fresh_account = account_mode == "environment_bootstrap" and fresh_account_storage(service.id, project, log)
    if fresh_account and account_identity:
        password = onboarding_state.prepare_login(service.id, RuntimePaths())["password"]
        # user_oidc maps preferred_username verbatim; sanitizing this value
        # would create a different local owner for e.g. email-based usernames.
        bootstrap_username = (
            account_identity["username"] if service.id == "nextcloud" else account_username(account_identity)
        )
        bootstrap_env = {
            "MU3LAB_BOOTSTRAP_USERNAME": bootstrap_username,
            "MU3LAB_BOOTSTRAP_EMAIL": account_identity["email"],
            "MU3LAB_BOOTSTRAP_PASSWORD": password,
        }
        bootstrap_files = [project / "docker-compose.bootstrap.yml"]
        _event(store, job_id, "account_bootstrap", "Creating the initial application administrator.")
    elif account_mode == "environment_bootstrap" and state:
        state.set_initialization(
            service.id,
            account_mode,
            "existing_account",
            job_id=job_id,
            owner_uid=account_identity["owner_uid"] if account_identity else "",
        )
    from ctl.compute import compose_overrides

    # Nextcloud cron depends on a healthy app.  A brand-new app cannot be
    # healthy until `occ maintenance:install` completes, so start only the
    # dependencies and app first.  Cron is started after bootstrap cleanup.
    requested_services = ["db", "redis", "app"] if service.id == "nextcloud" else None
    rc, output = actions.compose_up(
        project,
        log,
        timeout=wait_timeout + 300,
        wait_timeout=initial_wait_timeout,
        env=bootstrap_env,
        extra_files=[*compose_overrides(service.id, project), *bootstrap_files],
        recreate=bool(prior_installation),
        services=requested_services,
    )
    if rc:
        _append_runtime_diagnostics(store, job_id, project)
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "start_service",
            _failure_code("compose_start_failed", output),
            f"Application start failed: {output}",
        )
        return
    if service.id == "nextcloud":
        _event(store, job_id, "base_installation", "Confirming the Nextcloud base installation.")
        installed, install_detail = install_nextcloud_if_needed(project, log)
        if not installed:
            _fail(
                store,
                state,
                job_id,
                service.id,
                actor,
                "base_installation",
                "nextcloud_install_incomplete",
                install_detail,
            )
            return
    if account_mode == "api_bootstrap" and account_identity:
        from ctl.lifecycle.onboarding import OnboardingError, provision

        _event(store, job_id, "account_bootstrap", "Provisioning the application account and first-run defaults.")
        try:
            detail = provision(service, account_identity, RuntimePaths())
        except OnboardingError as exc:
            _fail(store, state, job_id, service.id, actor, "account_bootstrap", "account_provisioning_failed", str(exc))
            return
        _event(store, job_id, "account_verified", detail)
        if state:
            state.set_initialization(
                service.id, account_mode, "ready", job_id=job_id, owner_uid=account_identity["owner_uid"]
            )
    if fresh_account:
        account_verified, account_detail = verify_bootstrap_account(service.id, project, log, bootstrap_username)
        _event(store, job_id, "account_cleanup", "Removing bootstrap variables from the running application container.")
        rc, output = actions.compose_up(
            project,
            log,
            timeout=wait_timeout + 300,
            wait_timeout=wait_timeout,
            extra_files=compose_overrides(service.id, project),
            recreate=True,
        )
        if rc:
            _fail(
                store,
                state,
                job_id,
                service.id,
                actor,
                "account_cleanup",
                "account_verification_failed",
                f"The administrator was created but its bootstrap environment could not be removed: {output}",
            )
            return
        if not account_verified:
            _fail(
                store,
                state,
                job_id,
                service.id,
                actor,
                "account_verification",
                "account_verification_failed",
                "The application became healthy but did not confirm the generated administrator account. "
                + redact(account_detail),
            )
            return
    if state:
        state.set_installation(
            service.id, "verifying", job_id=job_id, manifest_version="3", image_digests=image_snapshot
        )
    _event(store, job_id, "verify_application", "Waiting for application health.")
    healthy, detail = wait_healthy(service)
    if not healthy:
        _append_runtime_diagnostics(store, job_id, project)
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "verify_application",
            _failure_code("health_check_failed", detail),
            f"Application did not become healthy: {detail}",
        )
        return
    if service.id == "nextcloud":
        _event(store, job_id, "configure_application", "Configuring Calendar and Authentik sign-in.")
        configured, detail = configure_nextcloud(project, log)
        if not configured:
            _fail(
                store,
                state,
                job_id,
                service.id,
                actor,
                "configure_application",
                "nextcloud_configuration_failed",
                detail,
            )
            return
    if service.id in {"adventurelog", "mealie"}:
        from ctl.lifecycle.integrations import retire_mealie_default_password

        setup = configure_adventurelog_oidc if service.id == "adventurelog" else retire_mealie_default_password
        configured, detail = setup(project, log)
        if not configured:
            _fail(
                store,
                state,
                job_id,
                service.id,
                actor,
                "configure_application",
                "application_configuration_failed",
                detail,
            )
            return
    if account_mode == "environment_bootstrap" and account_identity:
        record = onboarding_state.read(service.id, RuntimePaths())
        if record.get("password") and not record.get("provisioned"):
            username = account_identity["username"] if service.id == "nextcloud" else account_username(account_identity)
            confirmed = fresh_account
            if not confirmed:
                confirmed, _ = verify_bootstrap_account(service.id, project, log, username)
            if confirmed:
                from ctl.service_state import tailnet_dns_name

                host = tailnet_dns_name()
                login_url = f"https://{host}:{service.private_https_port}" if host else ""
                onboarding_state.complete_login(service.id, username, login_url, RuntimePaths())
    _event(store, job_id, "configure_route", "Publishing private HTTPS route.")
    routed, detail = apply_route(registry, service, root, log)
    if not routed:
        _fail(store, state, job_id, service.id, actor, "configure_route", "route_configuration_failed", detail)
        return
    from ctl.identity import GATED_APPS

    if service.id in GATED_APPS:
        # Caddy sends this app through the Authentik outpost, which answers 404
        # until the app is registered there (and a past uninstall is withdrawn).
        from ctl.identity import reconcile_blueprints
        from ctl.service_state import tailnet_dns_name

        try:
            reconcile_blueprints(registry, tailnet_dns_name())
        except (OSError, ValueError) as exc:
            log(f"Authentik sign-in was not registered yet: {exc}")
    if state:
        state.set_installation(
            service.id,
            "running",
            job_id=job_id,
            manifest_version="3",
            image_digests=image_snapshot,
            route_state="ready",
        )
        from ctl.identity import mode_for

        if mode_for(service) == "native_oidc":
            state.set_service_identity(
                service.id,
                "native_oidc",
                "migration_required",
                owner_uid=account_identity["owner_uid"] if account_identity else "",
                job_id=job_id,
                detail="Open the application once through Authentik to verify owner linking before local login is disabled.",
            )
    if fresh_account and account_identity and state:
        host = ""
        try:
            from ctl.service_state import tailnet_dns_name

            host = tailnet_dns_name()
        except (OSError, ValueError):
            pass
        login_url = f"https://{host}:{service.private_https_port}" if host and service.private_https_port else ""
        onboarding_state.complete_login(service.id, bootstrap_username, login_url, RuntimePaths())
        handoff = workflow_secrets.create_handoff(
            service_id=service.id,
            job_id=job_id,
            owner_uid=account_identity["owner_uid"],
            username=bootstrap_username,
            email=account_identity["email"],
            password=password,
            login_url=login_url,
        )
        state.add_handoff(
            handoff["id"],
            service.id,
            job_id,
            account_identity["owner_uid"],
            handoff["created_at"],
            handoff["expires_at"],
        )
        state.set_initialization(
            service.id,
            account_mode,
            "ready",
            job_id=job_id,
            owner_uid=account_identity["owner_uid"],
            handoff_id=handoff["id"],
        )
    _event(store, job_id, "finalize", "Application and private route verified.")
    if service.id == "lobehub":
        from ctl.lobehub_ops import reconcile

        ready, detail = reconcile(log)
        if not ready:
            _fail(store, state, job_id, service.id, actor, "lobehub_policy", "lobehub_policy_failed", detail)
            return
    else:
        _sync_chat_assistants(log)
    from ctl.mcp_ops import preenable, sync_application

    # Chat can use a newly installed app straight away: its default connector
    # is switched on, given a credential and attached to its assistant.
    try:
        preenable(service.id, root)
    except (OSError, ValueError) as exc:
        log(f"The chat connector could not be prepared: {redact(str(exc))}")
    if not sync_application(service.id, running=True, root=root, log=log):
        log("One enabled MCP needs attention after application installation.")
    onboarding_state.mark_configured(service.id, RuntimePaths())
    store.transition(
        job_id, "succeeded", actor=actor, detail=f"{service.name} installed and verified.", step_id="finalize"
    )


def _repair(
    store: JobStore, state: ControlState | None, job: dict, service: Service, registry: Registry, actor: str, root: Path
) -> None:
    """Recreate an optional runtime from its current curated manifest.

    This is deliberately a repair, not an install: it never pulls images,
    creates accounts, or removes volumes.  It is the safe migration path for
    deployments that previously attached generic db/redis aliases to the
    shared backend network.
    """
    job_id = str(job["id"])
    if service.stage == "core":
        store.transition(
            job_id,
            "running",
            actor=actor,
            detail=f"Repairing private routes for {service.name}.",
            step_id="configure_route",
        )
        log = _job_log(store, job_id)
        from ctl.routes import reconcile_core

        routed, detail = reconcile_core(registry, root, log)
        if not routed:
            _fail(store, state, job_id, service.id, actor, "configure_route", "route_configuration_failed", detail)
            return
        store.append_event(job_id, "step.completed", "core_routes:reconciled")
        store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
        return
    if service.stage != "optional":
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "validate_service",
            "repair_not_optional",
            "Only optional applications have a repairable runtime project.",
        )
        return
    store.transition(
        job_id,
        "running",
        actor=actor,
        detail=f"Repairing {service.name} without changing persistent data.",
        step_id="materialize_runtime",
    )
    try:
        project = materialize(service, root)
    except OSError as exc:
        _fail(store, state, job_id, service.id, actor, "materialize_runtime", "materialize_failed", str(exc))
        return
    log = _job_log(store, job_id)
    _event(store, job_id, "validate_configuration", "Validating the repaired curated Compose configuration.")
    rc, output = actions.compose_config(project, log)
    if rc:
        _fail(store, state, job_id, service.id, actor, "validate_configuration", "compose_invalid", output)
        return
    _event(store, job_id, "repair_runtime", "Recreating containers with the current private network layout.")
    from ctl.compute import compose_overrides

    wait_timeout = 900 if service.id in {"surfsense", "nextcloud"} else 180
    rc, output = actions.compose_up(
        project,
        log,
        timeout=wait_timeout + 300,
        wait_timeout=wait_timeout,
        extra_files=compose_overrides(service.id, project),
        recreate=True,
    )
    if rc:
        _append_runtime_diagnostics(store, job_id, project)
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "repair_runtime",
            _failure_code("compose_repair_failed", output),
            f"Application repair failed: {output}",
        )
        return
    healthy, detail = wait_healthy(service, timeout=wait_timeout)
    if not healthy:
        _append_runtime_diagnostics(store, job_id, project)
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "verify_application",
            _failure_code("health_check_failed", detail),
            f"Application did not become healthy after repair: {detail}",
        )
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
            _fail(store, state, job_id, service.id, actor, "lobehub_policy", "lobehub_policy_failed", detail)
            return
    from ctl.mcp_ops import sync_application

    if not sync_application(service.id, running=True, root=root, log=log):
        log("One enabled MCP needs attention after application repair.")
    store.transition(
        job_id,
        "succeeded",
        actor=actor,
        detail=f"{service.name} repaired with the current curated runtime.",
        step_id="complete",
    )


def _configure_identity(
    store: JobStore, state: ControlState | None, job: dict, service: Service, registry: Registry, actor: str, root: Path
) -> None:
    """Materialize identity configuration without silently claiming migration success."""
    from ctl.identity import mode_for, reconcile_blueprints

    job_id = str(job["id"])
    mode = mode_for(service)
    owner = workflow_secrets.job_identity(job_id)
    if owner:
        owner = onboarding_state.remember_owner(service.id, owner, RuntimePaths())
    else:
        owner = onboarding_state.read(service.id, RuntimePaths()).get("owner")
    owner_uid = owner["owner_uid"] if owner else ""
    store.transition(
        job_id,
        "running",
        actor=actor,
        detail=f"Reconciling sign-in for {service.name}.",
        step_id="identity_configuration",
    )
    try:
        if mode == "native_oidc":
            project = project_path(service, root)
            if service.stage == "optional" or service.id == "lobehub":
                project = materialize(service, root)
            from ctl.service_state import tailnet_dns_name

            written = reconcile_blueprints(registry, tailnet_dns_name())
            if service.id not in written:
                raise ValueError("The persisted OIDC client configuration is incomplete.")
            installation = state.installation(service.id) if state else None
            linked = bool(
                installation
                and installation.get("state") == "running"
                and linked_owner_verified(service, project, owner, _job_log(store, job_id))
            )
            if linked:
                enforce_identity_settings(service, project)
                # Rewrite the blueprint after clearing Actual's first-owner guard.
                reconcile_blueprints(registry, tailnet_dns_name())
            if installation and installation.get("state") == "running":
                from ctl.compute import compose_overrides

                rc, output = actions.compose_up(
                    project,
                    _job_log(store, job_id),
                    timeout=600,
                    wait_timeout=300,
                    extra_files=compose_overrides(service.id, project),
                    recreate=True,
                )
                if rc:
                    raise ValueError("The application could not apply its staged OIDC configuration: " + redact(output))
                if service.account.get("mode") == "api_bootstrap" and owner:
                    from ctl.lifecycle.onboarding import provision

                    provision(service, owner, RuntimePaths())
                    if state:
                        state.set_initialization(
                            service.id, "api_bootstrap", "ready", job_id=job_id, owner_uid=owner_uid
                        )
            if service.id == "adventurelog" and installation and installation.get("state") == "running":
                ok, configured_detail = configure_adventurelog_oidc(
                    project_path(service, root), _job_log(store, job_id)
                )
                if not ok:
                    raise ValueError(configured_detail)
            if installation and installation.get("state") == "running":
                if service.id == "nextcloud":
                    ok, configured_detail = configure_nextcloud(project, _job_log(store, job_id))
                    if not ok:
                        raise ValueError(configured_detail)
                if service.id == "mealie":
                    from ctl.lifecycle.integrations import retire_mealie_default_password

                    ok, configured_detail = retire_mealie_default_password(project, _job_log(store, job_id))
                    if not ok:
                        raise ValueError(configured_detail)
                routed, route_detail = apply_route(registry, service, root, _job_log(store, job_id))
                if not routed:
                    raise ValueError(route_detail)
            detail = (
                "OIDC configuration is installed. Complete a real Authentik callback so Mu3Lab "
                "can verify the existing owner and administrator role before disabling local login."
            )
            target = "ready" if linked else "migration_required"
            if linked:
                detail = (
                    "Verified Authentik account linking; password login is disabled."
                    if service.id == "lobehub"
                    else "Verified Authentik account linking and administrator role; browser password login is disabled."
                )
        elif mode == "trusted_header":
            from ctl.service_state import tailnet_dns_name

            # Caddy already routes this app through the outpost; register its
            # host there too, or every request is answered with a 404.
            reconcile_blueprints(registry, tailnet_dns_name())
            detail = "Authentik trusted-header access is configured; live route health remains authoritative."
            target = "ready"
        elif mode == "proxy_gate":
            from ctl.service_state import tailnet_dns_name

            reconcile_blueprints(registry, tailnet_dns_name())
            detail = "Authentik protects this route, but the application has no native per-user OIDC session."
            target = "ready"
        else:
            detail = (
                service.identity_note or "This application does not support Mu3Lab-managed native Authentik sign-in."
            )
            target = "unsupported"
    except (OSError, ValueError, RegistryError) as exc:
        detail = redact(str(exc))
        if state:
            state.set_service_identity(
                service.id,
                mode,
                "degraded",
                owner_uid=owner_uid,
                job_id=job_id,
                detail=detail,
                error={"code": "identity_configuration_failed", "message": detail},
            )
        _fail(
            store, state, job_id, service.id, actor, "identity_configuration", "identity_configuration_failed", detail
        )
        return
    if state:
        state.set_service_identity(
            service.id, mode, target, owner_uid=owner_uid, job_id=job_id, detail=detail, verified=target == "ready"
        )
        if target == "ready" and mode == "native_oidc":
            state.set_initialization(
                service.id, str(service.account.get("mode", "none")), "ready", job_id=job_id, owner_uid=owner_uid
            )
    if mode == "native_oidc":
        onboarding_state.mark_configured(service.id, RuntimePaths())
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")


def _uninstall(
    store: JobStore, state: ControlState | None, job: dict, service: Service, registry: Registry, actor: str, root: Path
) -> None:
    from ctl.lifecycle.uninstall import uninstall_application

    job_id = str(job["id"])
    delete = job.get("action") == "uninstall_delete_data"
    if service.stage != "optional":
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "validate_service",
            "uninstall_not_optional",
            "Only apps installed from the catalog can be uninstalled.",
        )
        return
    store.transition(
        job_id,
        "running",
        actor=actor,
        detail=f"Uninstalling {service.name}" + (" and deleting its data." if delete else "; its data is kept."),
        step_id="disconnect_chat",
    )
    if state:
        state.set_installation(service.id, "uninstalling", job_id=job_id)
    log = _job_log(store, job_id)
    ok, stage, detail = uninstall_application(
        service, registry, root, log, lambda step, text: _event(store, job_id, step, text), delete=delete
    )
    if not ok:
        _fail(store, state, job_id, service.id, actor, stage, "uninstall_failed", detail)
        return
    if state:
        if delete:
            for handoff_id in state.handoff_ids(service.id):
                workflow_secrets.delete(handoff_id)
        state.reset_service(service.id, keep_handoffs=not delete)
    _sync_chat_assistants(log)
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")


def _download_images(project: Path, service_id: str, job_id: str, log: Callable[[str], None]) -> tuple[int, str]:
    """Download images with Mu3Lab's verified parallel fetcher, else Docker's pull."""
    downloads = ImageDownloadStore.runtime()

    def report(snapshot: dict) -> None:
        if downloads:
            downloads.update(service_id, job_id, snapshot)

    rc, images = actions.compose_image_list(project, log)
    if rc == 0 and images:
        try:
            image_fetch.fetch_images(images, RuntimePaths().runtime / "image-cache", log, report=report)
            return 0, ""
        except image_fetch.FetchError as exc:
            log(f"Fast download unavailable ({exc}); using Docker's own download instead.")
    # Docker's pull reports no totals, so the dashboard shows it without a percentage.
    report({"state": "docker"})
    return actions.compose_pull(project, log)


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
        _fail(
            store,
            state,
            job_id,
            service_id,
            worker_id,
            "validate_service",
            "unknown_service",
            f"Unknown curated service: {exc}",
        )
        return
    if action not in SUPPORTED_ACTIONS or service.is_blocked:
        _fail(
            store,
            state,
            job_id,
            service.id,
            worker_id,
            "validate_service",
            "unsupported_action",
            "The worker rejected an unsupported service action.",
        )
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
    if action in UNINSTALL_ACTIONS:
        _uninstall(store, state, job, service, registry, actor, root)
        return
    if action == "reset":
        if service.stage != "optional":
            _fail(
                store,
                state,
                job_id,
                service.id,
                actor,
                "validate_service",
                "reset_not_optional",
                "Only optional applications can be reset from the catalog.",
            )
            return
        store.transition(
            job_id,
            "running",
            actor=actor,
            detail=f"Cleaning failed {service.name} installation.",
            step_id="reset_cleanup",
        )
        log = _job_log(store, job_id)
        ok, detail = reset_failed_application(service, root, log)
        if not ok:
            _fail(store, state, job_id, service.id, actor, "reset_cleanup", "reset_cleanup_failed", detail)
            return
        if state:
            state.reset_service(service.id)
        _sync_chat_assistants(log)
        store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
        return
    if action == "stop" and service.lifecycle == "always_on":
        _fail(
            store,
            state,
            job_id,
            service.id,
            worker_id,
            "validate_service",
            "always_on",
            "Always-on infrastructure cannot be stopped from the dashboard.",
        )
        return
    project = project_path(service, root)
    if service.id == "authentik" and action in {"start", "restart"}:
        from ctl.lifecycle import authentik_storage

        if authentik_storage.status() != "ready":
            # Starting now would bring Authentik up on an empty data folder.
            _fail(
                store,
                state,
                job_id,
                service.id,
                worker_id,
                "validate_service",
                "authentik_storage_pending",
                "Run ./install.sh once to move Authentik's data into /srv/mu3lab before restarting it.",
            )
            return
    if not (project / "docker-compose.yml").is_file():
        _fail(
            store,
            state,
            job_id,
            service.id,
            worker_id,
            "validate_service",
            "manifest_missing",
            "This curated service has no deployable Compose manifest.",
        )
        return
    store.transition(
        job_id, "running", actor=actor, detail=f"{action.title()} started for {service.name}.", step_id="compose"
    )
    log = _job_log(store, job_id)
    from ctl.compute import compose_overrides

    runtime_env = None
    if service.stage == "core":
        runtime = RuntimePaths()
        runtime_env = {
            "MU3LAB_ENV_FILE": str(runtime.projects / service.id / ".env"),
            "MU3LAB_DATA_ROOT": str(runtime.data),
        }
        if service.id == "litellm":
            runtime_env["MU3LAB_LITELLM_CONFIG"] = str(runtime.projects / "litellm" / "config.yaml")
        elif service.id == "freellmapi":
            runtime_env["MU3LAB_FREELLMAPI_CONFIG"] = str(runtime.projects / "freellmapi" / "freellmapi.config.json")
    if action == "stop":
        from ctl.mcp_ops import sync_application

        if not sync_application(service.id, running=False, root=root, log=log):
            _fail(
                store,
                state,
                job_id,
                service.id,
                actor,
                "stop_mcp",
                "mcp_stop_failed",
                "An enabled MCP could not stop safely.",
            )
            return
    rc, output = actions.compose_action(
        project, action, log, env=runtime_env, extra_files=compose_overrides(service.id, project)
    )
    if rc:
        _fail(
            store,
            state,
            job_id,
            service.id,
            actor,
            "compose",
            "compose_failed",
            f"{service.name} {action} failed: {output}",
        )
        return
    # The command finished; stop here if the job was cancelled meanwhile.
    job_guard.checkpoint()
    target_state = "stopped" if action == "stop" else "running"
    if target_state == "running":
        healthy, detail = wait_healthy(service)
        if not healthy:
            _fail(
                store,
                state,
                job_id,
                service.id,
                actor,
                "verify_application",
                "health_check_failed",
                f"Application did not become healthy: {detail}",
            )
            return
        if service.id == "adventurelog":
            oidc_env = read_runtime_env(project / ".env")
            if oidc_env.get("ADVENTURELOG_OIDC_CLIENT_ID"):
                configured, detail = configure_adventurelog_oidc(project, log)
                if not configured:
                    _fail(
                        store,
                        state,
                        job_id,
                        service.id,
                        actor,
                        "identity_configuration",
                        "identity_configuration_failed",
                        detail,
                    )
                    return
        from ctl.mcp_ops import sync_application

        if not sync_application(service.id, running=True, root=root, log=log):
            log("One enabled MCP needs attention after application start.")
        if service.id == "lobehub":
            from ctl.lobehub_ops import reconcile

            ready, detail = reconcile(log)
            if not ready:
                _fail(store, state, job_id, service.id, actor, "lobehub_policy", "lobehub_policy_failed", detail)
                return
    if state:
        state.set_installation(service.id, target_state, job_id=job_id, route_state="ready")
    store.append_event(job_id, "step.completed", f"compose:{action}")
    store.transition(job_id, "succeeded", actor=actor, detail=f"{service.name} {action} completed.", step_id="complete")
