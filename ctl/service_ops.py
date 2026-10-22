"""Durable, registry-bound application installation and lifecycle execution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ctl import actions, job_guard, setup_checks
from ctl.app_settings import AppSettings
from ctl.chat_ops import sync_agents
from ctl.compute import app_mode
from ctl.control_state import ControlState
from ctl.engine.compose import Compose
from ctl.engine.hooks import StepFailed
from ctl.engine.install import context as hook_context
from ctl.engine.install import run_install
from ctl.engine.jobs import _event, _fail, _job_log
from ctl.engine.project import Facts
from ctl.engine.rewire import rewire_consumers
from ctl.jobs import JobStore, redact
from ctl.lifecycle import app_control, compute_switch, maintenance
from ctl.lifecycle.health import wait_healthy
from ctl.lifecycle.maintenance import BACKUP_ACTIONS, MAINTENANCE_ACTIONS
from ctl.lifecycle.uninstall import uninstall_application
from ctl.mcp_ops import sync_application
from ctl.registry import Registry, RegistryError, Service, load
from ctl.rules import rules_for
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl.service_state import tailnet_dns_name
from ctl.store import onboarding as onboarding_state

SUPPORTED_ACTIONS = frozenset(
    {
        "install",
        "retry_setup",
        "start",
        "stop",
        "restart",
        "reset",
        "uninstall",
        "uninstall_delete_data",
        "restore",
        "update",
        "recover",
        "keep_update",
        "backup_stop",
        "backup_start",
        "switch_compute",
    }
)
UNINSTALL_ACTIONS = frozenset({"uninstall", "uninstall_delete_data"})


def project_path(service: Service, root: Path) -> Path:
    """Use a materialized application project when it owns the running Compose stack."""
    runtime = RuntimePaths().projects / service.id
    if service.stage in {"core", "optional"} or (runtime / "docker-compose.yml").is_file():
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
    # The release record stays: data from an earlier install was migrated to it.
    for name in ("docker-compose.bootstrap.yml",):
        try:
            (project / name).unlink(missing_ok=True)
        except OSError as exc:
            return False, f"Temporary setup file could not be removed: {exc}"
    env_path = project / ".env"
    settings = AppSettings(service.id)
    settings.remove_generated(name for name in settings.generated() if name.startswith("MU3LAB_BOOTSTRAP_"))
    if env_path.is_file():
        values = read_runtime_env(env_path)
        values = {key: value for key, value in values.items() if not key.startswith("MU3LAB_BOOTSTRAP_")}
        write_atomic(env_path, runtime_env_text(values).encode())
    return True, "Failed containers and temporary setup files removed; persistent data preserved."


def allowed_actions(service: Service, state: str) -> list[str]:
    """Actions for this state. "uninstall" also admits "uninstall_delete_data"."""
    if service.stage == "optional" and state in {"planned", "not_installed"}:
        return ["install"]
    uninstall = ["uninstall"] if service.stage == "optional" else []
    if state in {"failed", "needs_attention", "needs_setup", "degraded"}:
        return ["restart"] if service.stage != "optional" else ["retry_setup", "restart", *uninstall]
    if state in {"stopped", "setup_required"}:
        return ["start", *uninstall]
    if state in {"ready", "running", "starting", "configured", "installed", "checking", "stale"}:
        result = ["restart"]
        if service.lifecycle != "always_on":
            result.insert(0, "stop")
        return [*result, *uninstall]
    return []


def _sync_chat_assistants(log: Callable[[str], None]) -> None:
    """Show an app's LobeChat assistant only while the app is installed.

    A convenience: a failure is logged and never fails the app's own job.
    """
    ok, detail = sync_agents(log)
    if not ok:
        log(f"LobeChat assistants were not updated: {detail}")


def _uninstall(
    store: JobStore, state: ControlState | None, job: dict, service: Service, registry: Registry, actor: str, root: Path
) -> None:
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
        state.reset_service(service.id)
    setup_checks.forget(service.id, RuntimePaths())
    # Apps that used this one (Open WebUI using speech) drop those settings now.
    rewire_consumers(registry.catalog.get(service.id), registry.catalog, log)
    _sync_chat_assistants(log)
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")


def _switch_compute(
    store: JobStore, state: ControlState | None, job: dict, service: Service, actor: str, root: Path
) -> None:
    """Move the app to the CPU or GPU build the compute mode now asks for (compute_switch)."""
    job_id = str(job["id"])
    log = _job_log(store, job_id)
    running = app_control.was_running(state, service)
    store.transition(
        job_id, "running", actor=actor, detail=f"Switching {service.name}'s version.", step_id="compute_switch"
    )

    def step(name: str, detail: str) -> None:
        _event(store, job_id, name, detail)

    try:
        detail = compute_switch.switch(service, root, log, step, running=running)
    except compute_switch.SwitchFailed as failure:
        _fail(store, state, job_id, service.id, actor, "compute_switch", failure.code, str(failure))
        return
    if state:
        state.set_installation(service.id, "running" if running else "stopped", job_id=job_id, route_state="ready")
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")


def _ready_to_start(
    store: JobStore, job: dict, service: Service, registry: Registry, actor: str, log: Callable[[str], None]
) -> bool:
    """Check again what the app waits on (``ctl.setup_checks``); when something is missing it stays stopped.

    The job ends with the sentence saying what to open; the app keeps its stopped
    state rather than turning into a failed install.
    """
    job_id = str(job["id"])
    if service.stage != "optional":
        return True
    paths = RuntimePaths()
    app = registry.catalog.get(service.id)
    rules = rules_for(app.manifest)
    ctx = hook_context(
        app,
        Facts(tailnet_dns_name(), paths, registry.catalog),
        rules,
        onboarding_state.read(app.id, paths).get("owner"),
        log,
        lambda name, detail: _event(store, job_id, name, detail),
    )
    try:
        missing = setup_checks.first_missing(ctx, rules)
    except (StepFailed, OSError, ValueError, RuntimeError) as exc:
        failure = exc if isinstance(exc, StepFailed) else StepFailed("setup_check", "setup_check_failed", str(exc))
        store.transition(
            job_id, "failed", actor=actor, detail=failure.message, error_code=failure.code, step_id=failure.stage
        )
        return False
    if missing:
        setup_checks.remember(app.id, missing, paths)
        store.transition(
            job_id, "failed", actor=actor, detail=missing.message, error_code=missing.code, step_id=missing.stage
        )
        return False
    setup_checks.forget(app.id, paths)
    return True


def _start_needed_apps(
    store: JobStore,
    state: ControlState | None,
    job: dict,
    service: Service,
    registry: Registry,
    actor: str,
    root: Path,
) -> bool:
    """Start the stopped catalog apps this one needs (theirs first), so the voice assistant brings up Speech.

    Core apps run by themselves; an app that is not installed is the install batch's job. A failure
    ends the job with the needed app named and leaves this app's own state as it was.
    """
    job_id = str(job["id"])
    seen: set[str] = set()

    def needed(current: Service) -> list[Service]:
        found: list[Service] = []
        for dependency_id in current.dependencies:
            if dependency_id in seen:
                continue
            seen.add(dependency_id)
            try:
                dependency = registry.get(dependency_id)
            except RegistryError:
                continue
            found += needed(dependency)
            record = state.installation(dependency.id) if state else None
            if dependency.stage == "optional" and record and record["state"] == "stopped":
                found.append(dependency)
        return found

    log = _job_log(store, job_id)
    for dependency in needed(service):
        _event(store, job_id, "start_dependencies", f"Starting {dependency.name}, which {service.name} needs.")
        project = project_path(dependency, root)
        rc, output = Compose(project, gpu_mode=app_mode(project, dependency.uses_gpu)).action("start", log)
        healthy, detail = (False, output) if rc else wait_healthy(dependency)
        if not healthy:
            # The app itself keeps its state; the needed app is the one to look at.
            store.transition(
                job_id,
                "failed",
                actor=actor,
                detail=redact(f"{dependency.name}, which {service.name} needs, did not start: {detail}"),
                error_code="dependency_start_failed",
                step_id="start_dependencies",
            )
            return False
        if not sync_application(dependency.id, running=True, root=root, log=log):
            log(f"One enabled MCP of {dependency.name} needs attention after its start.")
        if state:
            state.set_installation(dependency.id, "running", job_id=job_id, route_state="ready")
    return True


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
    if action not in SUPPORTED_ACTIONS:
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
        if _start_needed_apps(store, state, job, service, registry, actor, root):
            run_install(store, state, job, service, registry, actor, root)
        return
    if action in UNINSTALL_ACTIONS:
        _uninstall(store, state, job, service, registry, actor, root)
        return
    if action in MAINTENANCE_ACTIONS | BACKUP_ACTIONS:
        maintenance.execute(store, state, job, service, root)
        return
    if action == "switch_compute":
        _switch_compute(store, state, job, service, actor, root)
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
        setup_checks.forget(service.id, RuntimePaths())
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
    if action == "stop" and not sync_application(service.id, running=False, root=root, log=log):
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
    if action == "start" and not _ready_to_start(store, job, service, registry, actor, log):
        return
    if action in {"start", "restart"} and not _start_needed_apps(store, state, job, service, registry, actor, root):
        return
    compose = Compose(project, gpu_mode=app_mode(project, service.uses_gpu))
    rc, output = compose.action(action, log)
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
        if not sync_application(service.id, running=True, root=root, log=log):
            log("One enabled MCP needs attention after application start.")
        if "chat" in service.manifest.capabilities:
            # Open WebUI starts without Mu3Lab's tool connections; give them back now, not in five minutes.
            sync_agents(log)
    if state:
        state.set_installation(service.id, target_state, job_id=job_id, route_state="ready")
    store.append_event(job_id, "step.completed", f"compose:{action}")
    store.transition(job_id, "succeeded", actor=actor, detail=f"{service.name} {action} completed.", step_id="complete")
