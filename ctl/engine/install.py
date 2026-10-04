"""Install any optional catalog app through its manifest and shared rules."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

from ctl import actions, image_fetch, onboarding_state, workflow_secrets
from ctl.compute import resolved_mode
from ctl.control_state import ControlState
from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext, StartPlan, StepFailed, load_app_hooks
from ctl.engine.jobs import _append_runtime_diagnostics, _event, _fail, _failure_code, _job_log, _start_failure_message
from ctl.engine.project import Facts
from ctl.engine.runtime import render_rules
from ctl.identity import installed, mode_for, sync_sign_in
from ctl.image_downloads import ImageDownloadStore
from ctl.integrations.authentik import Authentik, AuthentikError
from ctl.jobs import JobStore, redact
from ctl.lifecycle import app_releases
from ctl.lifecycle.health import wait_healthy
from ctl.lifecycle.signin_check import SignInError, verify_sign_in, wait_for_provider
from ctl.lobehub_ops import sync_agents
from ctl.manifest.catalog import App
from ctl.mcp_ops import preenable, sync_application
from ctl.registry import Registry, Service, load
from ctl.routes import apply as apply_route
from ctl.rules import Rule, rules_for
from ctl.runtime import RuntimePaths
from ctl.service_config import missing_required
from ctl.service_state import tailnet_dns_name
from ctl.workflow_secrets import JobIdentity


def context(
    app: App,
    facts: Facts,
    rules: list[Rule],
    owner: JobIdentity | None,
    log: Callable[[str], None],
    stage: Callable[[str, str], None],
) -> HookContext:
    project = facts.paths.projects / app.id

    def rerender() -> None:
        render_rules(app, facts, rules, owner)

    def reregister() -> None:
        sync_sign_in(facts.catalog, facts.dns_name, Authentik.runtime(), facts.paths)

    return HookContext(
        app,
        project,
        Compose(project, gpu_mode=resolved_mode() if app.manifest.service.uses_gpu else "cpu"),
        log,
        stage,
        owner,
        facts,
        rerender=rerender,
        reregister_sign_in=reregister,
    )


def run_install(
    store: JobStore, state: ControlState | None, job: dict, service: Service, registry: Registry, actor: str, root: Path
) -> None:
    job_id = str(job["id"])
    stage_name, failure_code = "validate_service", "manifest_invalid"
    log = _job_log(store, job_id)
    project: Path | None = None

    def stage(name: str, detail: str) -> None:
        nonlocal stage_name
        stage_name = name
        _event(store, job_id, name, detail)

    try:
        if service.stage != "optional":
            raise StepFailed(
                "validate_service", "install_not_optional", "This service is installed by the core-suite workflow."
            )
        app = registry.catalog.get(service.id)
        rules = rules_for(app.manifest)
        missing = missing_required(service)
        if missing:
            raise StepFailed(
                "validate_configuration",
                "configuration_required",
                "Required configuration is missing: " + ", ".join(missing),
            )
        paths = RuntimePaths()
        owner = workflow_secrets.job_identity(job_id, paths)
        account_mode = app.manifest.account.mode
        if (
            account_mode in {"environment_bootstrap", "api_bootstrap"} or any(rule.needs_owner for rule in rules)
        ) and not owner:
            raise StepFailed(
                "account_preflight",
                "identity_email_missing",
                "A verified Authentik email is required. Sign out, sign back in, and retry.",
            )
        if owner:
            owner = onboarding_state.remember_owner(app.id, owner, paths)
        owner_uid = owner["owner_uid"] if owner else ""
        prior_install = state.installation(app.id) if state else None
        if state:
            initialization = (
                "initializing"
                if account_mode in {"environment_bootstrap", "api_bootstrap"}
                else "awaiting_user"
                if account_mode == "oidc_first_login"
                else "not_required"
            )
            state.set_initialization(app.id, account_mode, initialization, job_id=job_id, owner_uid=owner_uid)
            state.set_installation(app.id, "installing", job_id=job_id)
        stage("validate_service", "Curated service contract accepted.")
        failure_code = "materialize_failed"
        stage("materialize_runtime", "Preparing the curated runtime project.")
        facts = Facts(tailnet_dns_name(), paths, registry.catalog)
        project = render_rules(app, facts, rules, owner)
        app_releases.align(service, root, paths)
        ctx = context(app, facts, rules, owner, log, stage)
        for rule in rules:
            rule.before_start(ctx)
        failure_code = "compose_invalid"
        stage("validate_configuration", "Validating generated Compose configuration.")
        rc, output = ctx.compose.validate(log)
        if rc:
            raise StepFailed(stage_name, failure_code, f"Compose validation failed: {output}")
        failure_code = "image_pull_failed"
        stage("pull_images", "Downloading pinned application images.")
        rc, output = download_images(project, app.id, job_id, log)
        if rc:
            raise StepFailed(stage_name, failure_code, f"Image pull failed: {output}")
        failure_code = "image_digest_failed"
        stage("resolve_digests", "Resolving images to immutable OCI digests.")
        images = app_releases.pin(service, root, log, paths)
        if state:
            state.set_installation(app.id, "starting", job_id=job_id, manifest_version="3", image_digests=images)
        if app.manifest.sign_in.method == "oidc":
            failure_code = "sign_in_unavailable"
            stage("authentik_ready", "Waiting for Authentik to publish this app's sign-in.")
            ctx.reregister_sign_in()
            wait_for_provider(facts.dns_name, app.id)
        plan = StartPlan()
        for rule in rules:
            rule.plan_start(ctx, plan)
        if state and account_mode == "environment_bootstrap" and not ctx.notes.get("first_admin"):
            state.set_initialization(app.id, account_mode, "existing_account", job_id=job_id, owner_uid=owner_uid)
        failure_code = "compose_start_failed"
        stage("start_service", "Starting application containers.")
        timeout = app.manifest.service.start_timeout_seconds

        def start(recreate: bool) -> tuple[int, str]:
            return ctx.compose.up(
                log,
                wait_seconds=timeout if plan.wait else None,
                services=plan.services,
                env=plan.env,
                extra=plan.extra_files,
                recreate=recreate,
            )

        rc, output = start(bool(prior_install))
        if rc and _failure_code("", output) == "dependency_unhealthy":
            stage("start_service", "A container stopped during its first start; waiting for its restart.")
            rc, output = start(False)
        if rc:
            raise StepFailed(stage_name, _failure_code(failure_code, output), _start_failure_message(output))

        def finish_start() -> None:
            stage(
                "account_cleanup" if plan.env or plan.extra_files else "start_service",
                "Starting all containers without first-start settings.",
            )
            rc, output = ctx.compose.up(log, wait_seconds=timeout, recreate=bool(plan.env or plan.extra_files))
            if rc:
                raise StepFailed(
                    stage_name, "account_verification_failed" if plan.env else "compose_start_failed", output
                )

        try:
            for rule in rules:
                rule.after_start(ctx)
        except (StepFailed, OSError, ValueError, RuntimeError):
            # Even failed account verification must not leave bootstrap credentials running.
            if plan.needs_final_start:
                finish_start()
            raise
        if plan.needs_final_start:
            finish_start()
        if account_mode == "api_bootstrap":
            failure_code = "account_provisioning_failed"
            stage("account_bootstrap", "Provisioning the application account and first-run defaults.")
            detail = load_app_hooks(app).bootstrap_account(ctx)
            stage("account_verified", detail)
            if state:
                state.set_initialization(app.id, account_mode, "ready", job_id=job_id, owner_uid=owner_uid)
        if state:
            state.set_installation(app.id, "verifying", job_id=job_id, manifest_version="3", image_digests=images)
        failure_code = "health_check_failed"
        stage("verify_application", "Waiting for application health.")
        healthy, detail = wait_healthy(service)
        if not healthy:
            raise StepFailed(
                stage_name, _failure_code(failure_code, detail), f"Application did not become healthy: {detail}"
            )
        for rule in rules:
            rule.after_healthy(ctx)
        failure_code = "route_configuration_failed"
        stage("configure_route", "Publishing private HTTPS route.")
        routed, detail = apply_route(registry, service, root, log)
        if not routed:
            raise StepFailed(stage_name, failure_code, detail)
        if app.manifest.sign_in.method in {"gate", "trusted_header"}:
            failure_code = "sign_in_unavailable"
            stage("authentik_ready", "Publishing this app's protected sign-in.")
            ctx.reregister_sign_in()
        failure_code = "sign_in_failed"
        stage("verify_sign_in", "Checking that Authentik sign-in works.")
        detail = verify_sign_in(app.id, facts.dns_name, log)
        stage("verify_sign_in", detail)
        mode = mode_for(service)
        sso_only = mode in {"native_oidc", "trusted_header"}
        if sso_only:
            onboarding_state.discard_password(app.id, paths)
        if state:
            state.set_installation(
                app.id, "running", job_id=job_id, manifest_version="3", image_digests=images, route_state="ready"
            )
            if sso_only:
                state.set_service_identity(
                    app.id, mode, "ready", owner_uid=owner_uid, job_id=job_id, detail=detail, verified=True
                )
                state.set_initialization(app.id, account_mode, "ready", job_id=job_id, owner_uid=owner_uid)
        stage("finalize", "Application and private route verified.")
        ok, detail = sync_agents(log)
        if not ok:
            log(f"Chat assistants were not updated: {redact(detail)}")
        try:
            preenable(app.id, root)
        except (AuthentikError, OSError, ValueError) as exc:
            log(f"The chat connector could not be prepared: {redact(str(exc))}")
        if not sync_application(app.id, running=True, root=root, log=log):
            log("One enabled MCP needs attention after application installation.")
        onboarding_state.mark_configured(app.id, paths)
        store.transition(
            job_id, "succeeded", actor=actor, detail=f"{service.name} installed and verified.", step_id="finalize"
        )
    except (StepFailed, AuthentikError, SignInError, OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        failure = exc if isinstance(exc, StepFailed) else StepFailed(stage_name, failure_code, str(exc))
        if project is not None and failure.stage in {"start_service", "verify_application", "verify_sign_in"}:
            _append_runtime_diagnostics(store, job_id, project)
        _fail(store, state, job_id, service.id, actor, failure.stage, failure.code, failure.message)


def run_periodic(store: JobStore, root: Path, log: Callable[[str], None]) -> None:
    """Run declared maintenance rules for installed apps, isolating each app's failures."""
    catalog = load().catalog
    paths = RuntimePaths()
    facts = Facts(tailnet_dns_name(), paths, catalog)
    for app in catalog.apps:
        if not installed(app, paths):
            continue
        try:
            rules = rules_for(app.manifest)
            ctx = context(
                app,
                facts,
                rules,
                onboarding_state.read(app.id, paths).get("owner"),
                log,
                lambda name, detail: log(f"{name}: {detail}"),
            )
            for rule in rules:
                detail = rule.periodic(ctx)
                if detail:
                    log(redact(detail))
                    store.record_audit(actor="worker", event="application.rule", detail=redact(detail))
        except (StepFailed, AuthentikError, OSError, ValueError) as exc:
            log(f"{app.manifest.name} maintenance deferred safely: {redact(str(exc))}")


def download_images(project: Path, service_id: str, job_id: str, log: Callable[[str], None]) -> tuple[int, str]:
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
