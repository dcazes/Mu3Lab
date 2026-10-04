"""Shared lifecycle job reporting and failure diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ctl import actions
from ctl.control_state import ControlState
from ctl.jobs import JobStore, redact


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


def _start_failure_message(output: str) -> str:
    if _failure_code("", output) == "docker_network_space_exhausted":
        return (
            "Docker has run out of private network addresses for apps. Run ./install.sh again: "
            "it gives Docker a larger address space without touching your apps or data."
        )
    return f"Application start failed: {output}"


def _failure_code(default: str, detail: str) -> str:
    """Turn common Docker failure text into stable, user-actionable codes."""
    value = detail.lower()
    if "fully subnetted" in value:
        return "docker_network_space_exhausted"
    if "password authentication failed" in value or "authentication failed for user" in value:
        return "database_auth_failed"
    if "unhealthy" in value or "dependency failed to start" in value:
        return "dependency_unhealthy"
    if "timed out" in value or "wait timeout" in value:
        return "application_health_timeout"
    return default
