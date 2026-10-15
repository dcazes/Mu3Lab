"""Free the disk that removed apps and old app versions still hold.

Only what Mu3Lab's own records say it put there is ever removed:

- **Removed apps:** Compose projects under Mu3Lab's projects folder whose app
  is no longer in the catalog. Their containers and images go automatically;
  their data and project folders only when an administrator confirms.
- **Old versions:** images of releases an app ran before, and images the
  catalog declares that nothing here runs (another compute variant, an app
  that was never finished). Each app's current release and the one before it
  stay, so an update can still be undone without downloading again.

Images Mu3Lab never recorded (anything the owner pulled or runs themselves)
and the helper images Mu3Lab runs one-off commands in are never touched.
Nothing is removed while a job runs or an app is downloading.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ctl import process
from ctl.actions import docker_argv
from ctl.runtime import RuntimePaths

SERVICE_ID = "system:cleanup"
Log = Callable[[str], None]


def identity(reference: str) -> str:
    """An image's digest when pinned (Docker's image ID), else its normalised name."""
    name, _, digest = reference.partition("@")
    for prefix in ("docker.io/", "library/"):
        name = name.removeprefix(prefix)
    return digest or name


def _images_in(folder: Path) -> set[str]:
    from ctl.lifecycle.app_releases import compose_images

    return {identity(image) for path in folder.glob("docker-compose*.yml") for image in compose_images(path).values()}


def removed_projects(registry, paths: RuntimePaths) -> list[Path]:
    """Project folders of apps (or connectors) no longer in the catalog."""
    from ctl.mcp_catalog import load as load_connectors
    from ctl.resource_usage import PLATFORM_PROJECTS

    known = {service.id for service in registry.services} | PLATFORM_PROJECTS
    known |= {f"mcp-{server.id}" for server in load_connectors(registry)}
    if not paths.projects.is_dir():
        return []
    return sorted(folder for folder in paths.projects.iterdir() if folder.is_dir() and folder.name not in known)


def _helpers() -> set[str]:
    """Images Mu3Lab runs one-off commands in (backups, dashboard builds)."""
    from ctl.backups import KOPIA_IMAGE
    from ctl.dashboard_build import NODE_IMAGE

    return {identity(KOPIA_IMAGE), identity(NODE_IMAGE)}


def _declared(root: Path) -> set[str]:
    """Every image the checkout declares, for every app and compute variant."""
    found: set[str] = set()
    for base in ("apps", "platform"):
        for folder in {path.parent for path in root.glob(f"{base}/**/docker-compose*.yml")}:
            found |= _images_in(folder)
    return found


def _releases(registry, root: Path, paths: RuntimePaths) -> tuple[set[str], set[str]]:
    """Images of every release apps ran here, and of the current and previous release of each."""
    from ctl.lifecycle import app_releases
    from ctl.store import records

    ran: set[str] = set()
    keep: set[str] = set()
    for service in registry.services:
        stored = records.get("app-releases", service.id, paths)
        history = [app_releases.Release.from_dict(value) for value in (stored.get("releases") or {}).values()]
        ran |= {identity(image) for release in history for image in release.images.values()}
        current = app_releases.installed(service, root, paths)
        # History keeps the order releases were recorded in; the newest one before the current stays.
        earlier = [release for release in history if not current or release.id != current.id]
        for release in ([current] if current else []) + earlier[-1:]:
            keep |= {identity(image) for image in release.images.values()}
    return ran, keep


def reclaimable(registry, root: Path, paths: RuntimePaths) -> set[str]:
    """Identities of Mu3Lab's images it may remove once no container uses them.

    Mu3Lab's images are those the checkout declares, those its apps ran, and
    those of removed apps. Each installed app's current and previous release,
    and the helper images, always stay.
    """
    ran, keep = _releases(registry, root, paths)
    known = _declared(root) | ran
    for folder in removed_projects(registry, paths):
        known |= _images_in(folder)
    return known - keep - _helpers()


def _docker(argv: list[str], run, timeout: int = 300) -> tuple[int, str]:
    try:
        proc = run(docker_argv(argv), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _removed_containers(registry, paths: RuntimePaths, run) -> list[str]:
    folders = {str(folder.resolve()) for folder in removed_projects(registry, paths)}
    rc, output = _docker(
        [
            "docker",
            "ps",
            "--all",
            "--no-trunc",
            "--format",
            '{{.ID}}\t{{.Label "com.docker.compose.project.working_dir"}}',
        ],
        run,
        30,
    )
    if rc:
        return []
    found = []
    for line in output.splitlines():
        container, _, working_dir = line.partition("\t")
        if working_dir and str(Path(working_dir).resolve()) in folders:
            found.append(container)
    return found


def _unused_images(candidates: set[str], run) -> list[str]:
    """IDs of images in ``candidates`` that no container, running or stopped, uses."""
    rc, output = _docker(
        ["docker", "images", "--no-trunc", "--format", "{{.ID}}\t{{.Repository}}:{{.Tag}}"],
        run,
        30,
    )
    if rc:
        return []
    rc, used = _docker(["docker", "ps", "--all", "--no-trunc", "--format", "{{.Image}}"], run, 30)
    if rc:
        return []
    in_use = {identity(line) for line in used.splitlines() if line}
    found = []
    for line in output.splitlines():
        image_id, _, name = line.partition("\t")
        if image_id in in_use or identity(name) in in_use:
            continue
        if image_id in candidates or identity(name) in candidates:
            found.append(image_id)
    return found


def removed_data(registry, root: Path, paths: RuntimePaths) -> list[Path]:
    """Data folders no catalog app mounts: what removed apps left behind."""
    from ctl.resource_usage import folder_owners

    owned = folder_owners(registry, root, paths)
    if not paths.data.is_dir():
        return []
    return sorted(folder for folder in paths.data.iterdir() if f"data/{folder.name}" not in owned)


def free_up(
    registry,
    root: Path,
    log: Log,
    *,
    delete_data: bool = False,
    paths: RuntimePaths | None = None,
    run=process.completed,
) -> dict[str, Any]:
    """Remove removed apps' containers and every reclaimable unused image; with ``delete_data``, their folders too."""
    from ctl.lifecycle.uninstall import delete_data as delete_folders

    paths = paths or RuntimePaths()
    candidates = reclaimable(registry, root, paths)
    containers = _removed_containers(registry, paths, run)
    if containers:
        log(f"Removing {len(containers)} container(s) of apps Mu3Lab no longer offers.")
        _docker(["docker", "rm", "--force", *containers], run)
    images = _unused_images(candidates, run)
    removed_images = 0
    for image in images:
        # One at a time, never forced: an image something still needs stays.
        rc, output = _docker(["docker", "rmi", image], run)
        removed_images += rc == 0
        if rc:
            log(f"Kept image {image[:19]}: {output.strip()[-200:]}")
    folders: list[str] = []
    if delete_data:
        data = removed_data(registry, root, paths)
        if data:
            ok, detail = delete_folders(data, root, log)
            if not ok:
                raise RuntimeError(detail)
            folders = [folder.name for folder in data]
        for project in removed_projects(registry, paths):
            shutil.rmtree(project)
            folders.append(f"projects/{project.name}")
    return {"containers": len(containers), "images": removed_images, "folders": folders}


def busy(store, batches=None) -> bool:
    """Something is installing, downloading, updating or backing up: leave every image alone."""
    return bool((store and store.active_jobs()) or (batches and batches.download_queue()))


def execute_claimed(store, job: dict, worker_id: str, root: Path) -> None:
    """The "Free up space" job."""
    from ctl import resource_usage
    from ctl.jobs import job_params, redact
    from ctl.registry import load

    job_id = str(job["id"])
    actor = str(job.get("actor") or worker_id)
    store.transition(job_id, "running", actor=actor, detail="Freeing up space.", step_id="cleanup")
    try:
        done = free_up(
            load(),
            root,
            lambda line: store.append_event(job_id, "log", line),
            delete_data=job_params(job).get("delete_data") == "true",
        )
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        store.transition(job_id, "failed", actor=actor, detail=redact(str(exc)), error_code="cleanup_failed")
        return
    resource_usage.disk_stale.set()
    parts = [f"{done['images']} old program version(s)"]
    if done["containers"]:
        parts.append(f"{done['containers']} container(s) of removed apps")
    if done["folders"]:
        parts.append(f"folders {', '.join(done['folders'])}")
    store.transition(job_id, "succeeded", actor=actor, detail="Removed " + "; ".join(parts) + ".", step_id="complete")
