"""Uninstall one optional application, keeping or deleting its data.

Every step undoes one thing an install did, in an order that keeps a partly
finished uninstall safe to run again: chat connectors, containers, the private
route, Authentik sign-in, the runtime project, then (only when asked) data.

Keeping data leaves ``/srv/mu3lab/data/<app>``, the project's ``.env``
(database passwords that unlock that data) and the release record (the
release that data was migrated to), so a reinstall reconnects to it.
Deleting data removes both, plus the app's images and saved credentials.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

import yaml

from ctl import actions
from ctl.identity import remove_sign_in
from ctl.integrations.authentik import Authentik, AuthentikError
from ctl.lifecycle import app_releases
from ctl.platform_apps import by_capability
from ctl.registry import Registry, Service
from ctl.runtime import RuntimePaths
from ctl.service_state import tailnet_dns_name

Log = Callable[[str], None]
Stage = Callable[[str, str], None]

_DATA_MOUNT = re.compile(r"\$\{MU3LAB_DATA_ROOT(?::-[^}]*)?\}/([a-z0-9-]+)(?=[/:]|$)")


def _mount_sources(compose_file: Path) -> list[str]:
    try:
        document = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    sources: list[str] = []
    for definition in (document.get("services") or {}).values():
        for volume in (definition or {}).get("volumes") or []:
            # The whole short-form string: the data root default itself contains ":".
            source = volume.get("source") if isinstance(volume, dict) else volume
            if source:
                sources.append(str(source))
    return sources


def _data_names(compose_files: list[Path]) -> set[str]:
    names: set[str] = set()
    for compose_file in compose_files:
        for source in _mount_sources(compose_file):
            match = _DATA_MOUNT.match(source)
            if match:
                names.add(match.group(1))
    return names


def data_directories(service: Service, root: Path) -> list[Path]:
    """The app's top-level folders under the data root, never one another stack also mounts."""
    own_dir = service.compose_path(root)
    own = _data_names(sorted(own_dir.glob("docker-compose*.yml")))
    others = _data_names(
        [path for base in (root / "apps",) for path in base.rglob("docker-compose*.yml") if path.parent != own_dir]
    )
    data = RuntimePaths().data
    return [data / name for name in sorted(own - others)]


def _cleanup_image(root: Path) -> str:
    """Borrow the always-present ingress image; its busybox `rm` runs as root."""
    document = yaml.safe_load(
        (root / "apps" / by_capability("private_proxy").id / "docker-compose.yml").read_text(encoding="utf-8")
    )
    return str(next(iter(document["services"].values()))["image"])


def delete_data(directories: list[Path], root: Path, log: Log) -> tuple[bool, str]:
    """Delete data folders whose files belong to container users (postgres, www-data).

    The dashboard runs unprivileged, so the removal runs in a short-lived
    container that sees only the data root, with networking off.
    """
    data = RuntimePaths().data
    present = [path for path in directories if path.exists()]
    if not present:
        return True, "No application data was found."
    if any(path.parent != data for path in present):
        return False, "Refusing to delete a folder outside the Mu3Lab data root."
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "0:0",
        "--entrypoint",
        "rm",
        "-v",
        f"{data}:/mu3lab-data",
        _cleanup_image(root),
        "-rf",
        "--",
        *(f"/mu3lab-data/{path.name}" for path in present),
    ]
    rc, output = actions.docker_cmd(command, log, timeout=1800)
    remaining = [path.name for path in present if path.exists()]
    if rc or remaining:
        return False, output or "Some application data could not be deleted: " + ", ".join(remaining)
    return True, "Application data deleted."


def _release_chat_connectors(service_id: str, root: Path, log: Log, *, forget: bool) -> bool:
    """Stop the app's MCP connectors; when its data goes, their credentials go too."""
    from ctl.control_state import ControlState
    from ctl.mcp_catalog import load as load_catalog
    from ctl.mcp_ops import sync_application
    from ctl.mcp_registry import credential_path
    from ctl.registry import load as load_registry

    stopped = sync_application(service_id, running=False, root=root, log=log)
    state = ControlState.runtime()
    if not forget or state is None:
        return stopped
    for server in load_catalog(load_registry()):
        if server.service_id != service_id:
            continue
        # These were issued by the app being deleted; a reinstall issues new ones.
        credential_path(server.id).unlink(missing_ok=True)
        if state.mcp_server(server.id):
            state.set_mcp_server(server.id, service_id, enabled=False, state="disabled")
    return stopped


def _remove_sign_in(service: Service, registry: Registry, paths: RuntimePaths) -> None:
    if service.manifest.sign_in.method in {"oidc", "gate", "trusted_header"}:
        remove_sign_in(
            registry.catalog.get(service.id),
            Authentik.runtime(paths),
            catalog=registry.catalog,
            host=tailnet_dns_name(),
            paths=paths,
        )


def _remove_project(project: Path, *, keep_env: bool) -> None:
    if not project.exists():
        return
    if not keep_env:
        shutil.rmtree(project)
        return
    for item in project.iterdir():
        # The release record says which release the kept data was migrated to.
        if item.name in {".env", app_releases.RECORD}:
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()


def _remove_images(images: list[str], log: Log) -> None:
    """Best effort: Docker refuses to remove an image another stack still uses."""
    for image in images:
        rc, _output = actions.docker_cmd(["docker", "image", "rm", image], log, timeout=300)
        if rc:
            log(f"Kept image {image}; another app may still use it.")


def _disconnect_calendars(log: Log) -> None:
    """Copy calendars into Mu3Lab and revoke app passwords while Nextcloud is still running."""
    from ctl import nextcloud_calendar
    from ctl.control_state import ControlState

    state = ControlState.runtime()
    for owner_uid in state.calendar_owner_uids() if state else []:
        warning = nextcloud_calendar.disconnect(owner_uid).get("warning")
        if warning:
            log(str(warning))


def uninstall_application(
    service: Service, registry: Registry, root: Path, log: Log, stage: Stage, *, delete: bool
) -> tuple[bool, str, str]:
    """Return ``(ok, failed_stage, detail)``; safe to run again after a failure."""
    paths = RuntimePaths()
    project = paths.projects / service.id
    compose = project / "docker-compose.yml"

    stage("disconnect_chat", "Disconnecting the app from chat.")
    if not _release_chat_connectors(service.id, root, log, forget=delete):
        return False, "disconnect_chat", "A chat connector for this app could not be stopped."
    if service.id == by_capability("files_calendar").id:
        # Even when its data is kept, the dashboard switches to Mu3Lab's own
        # calendar; reinstalling reconnects automatically and sends changes back.
        _disconnect_calendars(log)

    images: list[str] = []
    if compose.is_file():
        if delete:
            rc, listed = actions.compose_image_list(project, log)
            images = listed if rc == 0 else []
        stage("remove_containers", "Stopping and removing the app's containers.")
        rc, output = actions.compose_down(project, log)
        if rc:
            return False, "remove_containers", output or "The app's containers could not be removed."

    stage("remove_route", "Removing the app's private web address.")
    from ctl.routes import withdraw

    ok, detail = withdraw(registry, service, root, log)
    if not ok:
        return False, "remove_route", detail

    stage("remove_sign_in", "Removing the app from Authentik sign-in.")
    try:
        _remove_project(project, keep_env=not delete)
        _remove_sign_in(service, registry, paths)
    except (AuthentikError, OSError, ValueError) as exc:
        return False, "remove_sign_in", f"Sign-in cleanup failed: {exc}"

    if not delete:
        return True, "", f"{service.name} uninstalled. Its data is kept; reinstalling picks up where you left off."

    stage("delete_data", "Deleting the app's data.")
    ok, detail = delete_data(data_directories(service, root), root, log)
    if not ok:
        return False, "delete_data", detail
    _remove_images(images, log)
    from ctl.store import onboarding as onboarding_state

    onboarding_state.forget(service.id, paths)
    return True, "", f"{service.name} and all of its data were deleted."
