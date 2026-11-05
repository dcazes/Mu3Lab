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


def start(service: Service, root: Path, log: Log, *, build: bool = False) -> tuple[bool, str]:
    """Start the app and wait for its health check; returns (healthy, why not).

    ``build`` rebuilds the images Mu3Lab builds from source, for an update that brought new files.
    """
    from ctl.mcp_ops import sync_application

    folder = project(service, root)
    try:
        env, extra = compose_settings.settings(service, folder)
    except RuntimeError as exc:
        return False, str(exc)
    # An app whose first start is known to take longer (RomM) asks for more.
    wait = max(START_TIMEOUT, service.manifest.service.start_timeout_seconds)
    # Orphans: a service a new release dropped (Dawarich's Sidekiq worker) must
    # not keep running beside it.
    rc, output = actions.compose_up(
        folder,
        log,
        timeout=wait + 300,
        wait_timeout=wait,
        env=env,
        extra_files=extra,
        remove_orphans=True,
        build=build,
    )
    if rc:
        return False, output[-500:]
    healthy, detail = wait_healthy(service, timeout=wait)
    if not healthy:
        return False, detail
    if not sync_application(service.id, running=True, root=root, log=log):
        log("One enabled chat connector needs attention after the app started.")
    if "chat" in service.manifest.capabilities:
        _sync_chat(log)
    return True, ""


def _sync_chat(log: Log) -> None:
    """A chat app starts without Mu3Lab's tool connections (Open WebUI keeps none); give them back now.

    A convenience: the worker tries again within minutes, so a failure never fails the start.
    """
    from ctl.chat_ops import sync_agents

    try:
        ok, detail = sync_agents(log)
    except Exception as exc:  # any failure here must leave the healthy start standing
        ok, detail = False, str(exc)
    if not ok:
        log(f"Chat assistants were not updated after the start: {detail}")


def was_running(state: ControlState | None, service: Service) -> bool:
    """Whether the household expects the app to be running (a stopped app stays stopped)."""
    installation = state.installation(service.id) if state else None
    return not installation or installation["state"] != "stopped"
