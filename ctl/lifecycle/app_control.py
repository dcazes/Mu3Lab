"""Stop and start one app the same way for backups, restores and updates.

Every start uses ``compose_settings``, the same values the installer uses, so
an app restarted by a backup or a restore comes back exactly as installed. A
start counts only once the app's own health check passes; its chat
connectors are stopped first and started again after.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ctl import actions
from ctl.control_state import ControlState
from ctl.lifecycle import compose_settings
from ctl.lifecycle.health import wait_healthy
from ctl.registry import Service

# Releases can migrate large databases on first start.
START_TIMEOUT = 900

Log = Callable[[str], None]


class StopFailed(RuntimeError):
    """The app could not be stopped; the message is safe to show."""


def project(service: Service, root: Path) -> Path:
    from ctl.service_ops import project_path

    return project_path(service, root)


def stop(service: Service, root: Path, log: Log) -> None:
    from ctl.mcp_ops import sync_application

    if not sync_application(service.id, running=False, root=root, log=log):
        raise StopFailed(f"A chat connector for {service.name} could not stop safely.")
    folder = project(service, root)
    try:
        env, extra = compose_settings.settings(service, folder)
    except RuntimeError as exc:
        raise StopFailed(f"{service.name} could not be stopped: {exc}") from exc
    rc, output = actions.compose_action(folder, "stop", log, env=env, extra_files=extra)
    if rc:
        raise StopFailed(f"{service.name} could not be stopped: {output[-300:]}")


def start(service: Service, root: Path, log: Log) -> tuple[bool, str]:
    """Start the app and wait for its health check; returns (healthy, why not)."""
    from ctl.mcp_ops import sync_application

    folder = project(service, root)
    try:
        env, extra = compose_settings.settings(service, folder)
    except RuntimeError as exc:
        return False, str(exc)
    rc, output = actions.compose_up(
        folder, log, timeout=START_TIMEOUT + 300, wait_timeout=START_TIMEOUT, env=env, extra_files=extra
    )
    if rc:
        return False, output[-500:]
    healthy, detail = wait_healthy(service, timeout=START_TIMEOUT)
    if not healthy:
        return False, detail
    if not sync_application(service.id, running=True, root=root, log=log):
        log("One enabled chat connector needs attention after the app started.")
    return True, ""


def was_running(state: ControlState | None, service: Service) -> bool:
    """Whether the household expects the app to be running (a stopped app stays stopped)."""
    installation = state.installation(service.id) if state else None
    return not installation or installation["state"] != "stopped"
