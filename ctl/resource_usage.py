"""How much memory and disk each app uses, for the charts on Home and System.

Memory is read from each container's cgroup: the same figure ``docker stats``
shows (usage less reclaimable page cache), without its one-second sample, so
the status loop reads it every few seconds for next to nothing. Graphics memory
(NVIDIA) comes from ``nvidia-smi``: each process on the GPU is matched to its
container through its cgroup.

Disk is slower to measure, so the worker measures it once a day and right after
an app is installed, updated or removed. An app's disk is its data (its
folders under the data and media roots, plus its Docker volumes) and its
program (its Docker images and containers). A layer two images share is
counted once, split between the apps by how much of it each one uses.

Usage belongs to an owner: an app's id, ``core`` (sign-in, the proxy, the
password vault, the private-network relay and Mu3Lab's own
services) or ``removed`` (projects of apps no longer in the catalog). Images no
container uses are ``unused``. Core is also reported part by part (``core_parts``):
each platform app by its id, ``tailnet-relay``, and ``mu3lab`` for Mu3Lab's own
services, state and projects.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Set
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl import process
from ctl.actions import docker_argv
from ctl.runtime import RuntimePaths

CORE = "core"
REMOVED = "removed"
UNUSED = "unused"
# Platform pieces that are not catalog apps: the private-network relay and the calendar tools.
PLATFORM_PROJECTS = {"tailnet-relay", "calendar-tools"}
# Measured once a day, and again right after an install, update or removal.
DISK_EVERY = 24 * 3600
DISK_RECORD = ("status", "disk-usage")
# Mu3Lab's own part of core: the dashboard and worker, their state and the project files.
OWN_PART = "mu3lab"
CGROUP_ROOT = Path("/sys/fs/cgroup")

log = logging.getLogger(__name__)
# Set after a job that adds or removes an app; the disk loop measures again soon.
disk_stale = threading.Event()


# ---------------------------------------------------------------- owners


def core_part(owner: str) -> str:
    """The core part a ``split_core`` owner names ("core/authentik" -> "authentik"), else ""."""
    return owner.partition("/")[2] if owner.startswith(f"{CORE}/") else ""


def _fold(owner: str) -> str:
    return CORE if core_part(owner) else owner


def owners(
    registry, root: Path, paths: RuntimePaths | None = None, *, split_core: bool = False
) -> Callable[[str], str]:
    """Who a Compose project belongs to, from its working directory; "" when it is not Mu3Lab's.

    With ``split_core``, core projects are "core/<part>" instead of "core".
    """
    from ctl.mcp_catalog import load as load_connectors

    stages = {service.id: service.stage for service in registry.services}
    connectors = {f"mcp-{server.id}": server.service_id for server in load_connectors(registry)}
    # Mu3Lab's projects live in its projects folder, or (core pieces) in the checkout.
    homes = [(paths or RuntimePaths()).projects.resolve(), root.resolve()]

    def owner(working_dir: str) -> str:
        if not working_dir:
            return ""
        folder = Path(working_dir).resolve()
        if not any(home == folder or home in folder.parents for home in homes):
            return ""
        name = folder.name
        if name in PLATFORM_PROJECTS or stages.get(name) == "foundation":
            return f"{CORE}/{name}" if split_core else CORE
        if name in stages:
            return name
        if name in connectors:
            return connectors[name]
        return REMOVED

    return owner


# ---------------------------------------------------------------- memory


def _containers(run=process.completed) -> list[tuple[str, str, str]]:
    """(id, name, Compose working directory) of each running container."""
    argv = ["docker", "ps", "--no-trunc", "--format"]
    argv.append('{{.ID}}\t{{.Names}}\t{{.Label "com.docker.compose.project.working_dir"}}')
    try:
        proc = run(docker_argv(argv), capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    found = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0]:
            found.append((parts[0], parts[1], parts[2]))
    return found


def _cgroup_memory(folder: Path) -> int | None:
    """Usage less inactive page cache, as ``docker stats`` reports it (cgroup v2)."""
    try:
        used = int((folder / "memory.current").read_text())
        stat = (folder / "memory.stat").read_text()
    except (OSError, ValueError):
        return None
    match = re.search(r"^inactive_file (\d+)$", stat, re.MULTILINE)
    return max(0, used - (int(match.group(1)) if match else 0))


def _container_cgroup(container_id: str, base: Path = CGROUP_ROOT) -> Path | None:
    # systemd's cgroup driver (the default) first, then Docker's own cgroupfs layout.
    for folder in (base / "system.slice" / f"docker-{container_id}.scope", base / "docker" / container_id):
        if folder.is_dir():
            return folder
    return None


def _own_services(base: Path = CGROUP_ROOT) -> int:
    """Memory of Mu3Lab's own services (dashboard and worker), siblings of this process's cgroup."""
    try:
        line = Path("/proc/self/cgroup").read_text().strip().splitlines()[-1]
    except (OSError, IndexError):
        return 0
    own = base / line.partition("::")[2].lstrip("/")
    if not own.name.startswith("mu3lab-"):
        return 0
    return sum(_cgroup_memory(folder) or 0 for folder in own.parent.glob("mu3lab-*.service"))


def memory(
    registry, root: Path, run=process.completed, base: Path = CGROUP_ROOT, paths: RuntimePaths | None = None
) -> tuple[dict, dict, dict]:
    """Memory per container name, per owner (apps, core, removed) and per core part.

    Containers that are not Mu3Lab's are left out.
    """
    owner = owners(registry, root, paths, split_core=True)
    by_name: dict[str, int] = {}
    by_owner: dict[str, int] = defaultdict(int)
    parts: dict[str, int] = defaultdict(int)
    for container_id, name, working_dir in _containers(run):
        folder = _container_cgroup(container_id, base)
        used = _cgroup_memory(folder) if folder else None
        if used is None:
            continue
        by_name[name] = used
        who = owner(working_dir)
        if core_part(who):
            parts[core_part(who)] += used
        if who:
            by_owner[_fold(who)] += used
    own = _own_services(base)
    by_owner[CORE] += own
    parts[OWN_PART] += own
    nonzero = lambda values: {key: value for key, value in values.items() if value}  # noqa: E731
    return by_name, nonzero(by_owner), nonzero(parts)


# ---------------------------------------------------------------- graphics memory

_DOCKER_SCOPE = re.compile(r"docker-([0-9a-f]{64})\.scope|/docker/([0-9a-f]{64})")


def _nvidia(argv: list[str], run) -> list[list[str]]:
    """Rows of an nvidia-smi CSV query (no header, no units); [] when it cannot answer."""
    try:
        proc = run(["nvidia-smi", *argv, "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [[cell.strip() for cell in line.split(",")] for line in proc.stdout.splitlines() if line.strip()]


def _pid_container(pid: str, proc_root: Path = Path("/proc")) -> str:
    """The id of the container a host process runs in, from its cgroup; "" when it is not in one."""
    try:
        text = (proc_root / pid / "cgroup").read_text()
    except OSError:
        return ""
    match = _DOCKER_SCOPE.search(text)
    return (match.group(1) or match.group(2)) if match else ""


def gpu_memory(
    registry, root: Path, run=process.completed, paths: RuntimePaths | None = None, proc_root: Path = Path("/proc")
) -> dict[str, Any] | None:
    """Graphics memory of NVIDIA GPUs (all cards added up), in bytes, and how much each owner uses.

    None without an NVIDIA GPU. Use that is not a Mu3Lab container (the desktop, other programs)
    is left out of ``by_owner``, as for memory.
    """
    cards = _nvidia(["--query-gpu=name,memory.total,memory.used"], run)
    sizes = [row for row in cards if len(row) == 3 and row[1].isdigit() and row[2].isdigit()]
    if not sizes:
        return None
    mib = 1024**2
    by_owner: dict[str, int] = defaultdict(int)
    parts: dict[str, int] = defaultdict(int)
    users = [row for row in _nvidia(["--query-compute-apps=pid,used_memory"], run) if len(row) == 2]
    if users:
        owner = owners(registry, root, paths, split_core=True)
        folders = {container_id: working_dir for container_id, _name, working_dir in _containers(run)}
        for pid, used in users:
            container_id = _pid_container(pid, proc_root)
            who = owner(folders.get(container_id, "")) if container_id else ""
            if not who or not used.isdigit():
                continue
            by_owner[_fold(who)] += int(used) * mib
            if core_part(who):
                parts[core_part(who)] += int(used) * mib
    return {
        "name": ", ".join(dict.fromkeys(row[0] for row in sizes)),
        "total": sum(int(row[1]) for row in sizes) * mib,
        "used": sum(int(row[2]) for row in sizes) * mib,
        "by_owner": dict(by_owner),
        "by_core_part": dict(parts),
    }


# ---------------------------------------------------------------- disk

_SIZE = re.compile(r"^\s*([\d.]+)\s*([kKMGTP]?B)\s*$")
_SIZE_UNITS = {"B": 1, "kB": 1000, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4, "PB": 1000**5}


def _size(text: object) -> int:
    """Docker's "1.5GB" (decimal units) in bytes; 0 for anything else."""
    match = _SIZE.match(str(text or ""))
    return int(float(match.group(1)) * _SIZE_UNITS.get(match.group(2), 1)) if match else 0


def _docker_json(argv: list[str], run, timeout: int) -> list[dict]:
    try:
        proc = run(docker_argv(argv), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    rows = []
    for line in proc.stdout.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


_WORKING_DIR = re.compile(r"(?:^|,)com\.docker\.compose\.project\.working_dir=([^,]*)")
_PROJECT = re.compile(r"(?:^|,)com\.docker\.compose\.project=([^,]*)")


def _label(pattern: re.Pattern, labels: Any) -> str:
    match = pattern.search(str(labels or ""))
    return match.group(1) if match else ""


def _name(reference: str) -> str:
    """``docker.io/library/redis:7`` and ``redis:7`` are the same image."""
    for prefix in ("docker.io/", "library/"):
        reference = reference.removeprefix(prefix)
    return reference


def docker_usage(
    owner: Callable[[str], str], reclaimable: Set[str] = frozenset(), run=process.completed
) -> dict[str, dict[str, int]]:
    """Program bytes (images, container layers) and volume bytes per owner.

    A layer several images share is counted once overall: Docker's total for
    images, less every image's own unique bytes, is split by each owner's share.
    An image no container uses is ``unused`` when Mu3Lab may remove it, and
    otherwise not Mu3Lab's to count. Owners "" (not Mu3Lab's) are left out.
    """
    summary = _docker_json(["docker", "system", "df", "--format", "{{json .}}"], run, 60)
    verbose = _docker_json(["docker", "system", "df", "-v", "--format", "{{json .}}"], run, 120)
    if not verbose:
        return {}
    detail = verbose[0]
    containers = detail.get("Containers") or []
    # Which owners run each image (by the reference Compose used), and own each project's volumes.
    users: dict[str, set[str]] = defaultdict(set)
    projects: dict[str, str] = {}
    program: dict[str, float] = defaultdict(float)
    for container in containers:
        who = owner(_label(_WORKING_DIR, container.get("Labels")))
        reference, _, digest = str(container.get("Image") or "").partition("@")
        users[digest or _name(reference)].add(who)
        projects[_label(_PROJECT, container.get("Labels"))] = who
        program[who] += _size(str(container.get("Size") or "").split(" ")[0])
    shared_by: dict[str, float] = defaultdict(float)
    unique_total = 0
    for image in detail.get("Images") or []:
        unique, shared = _size(image.get("UniqueSize")), _size(image.get("SharedSize"))
        unique_total += unique
        # Mu3Lab pins images by digest, which Docker uses as the image's ID; others go by name.
        image_id, image_name = str(image.get("ID")), _name(f"{image.get('Repository')}:{image.get('Tag')}")
        runners = sorted(users.get(image_id, set()) | users.get(image_name, set()))
        if not runners:
            runners = [UNUSED if image_id in reclaimable or image_name in reclaimable else ""]
        for name in runners:
            program[name] += unique / len(runners)
            shared_by[name] += shared / len(runners)
    images_total = next((_size(row.get("Size")) for row in summary if row.get("Type") == "Images"), 0)
    shared_actual = max(0, images_total - unique_total)
    weight = sum(shared_by.values())
    for name, amount in shared_by.items():
        program[name] += shared_actual * amount / weight if weight else 0
    data: dict[str, int] = defaultdict(int)
    for volume in detail.get("Volumes") or []:
        project = _label(_PROJECT, volume.get("Labels"))
        if project in projects:
            data[projects[project]] += _size(volume.get("Size"))
    return {
        name: {"data": data.get(name, 0), "program": int(program.get(name, 0))}
        for name in set(program) | set(data)
        if name
    }


def folder_owners(registry, root: Path, paths: RuntimePaths, *, split_core: bool = False) -> dict[str, str]:
    """Which owner each measured folder (relative to the runtime root) belongs to.

    With ``split_core``, core folders are "core/<part>" instead of "core".
    """
    from ctl import backup_service, duplex_voice
    from ctl.lifecycle.uninstall import data_directories

    own = f"{CORE}/{OWN_PART}" if split_core else CORE
    result = {"state": own, "projects": own}
    for service in registry.services:
        core = f"{CORE}/{service.id}" if split_core else CORE
        name = core if service.stage == "foundation" else service.id
        for folder in data_directories(service, root, paths=paths):
            result[f"data/{folder.name}"] = name
        for media in service.manifest.media:
            result[f"media/{media.name}"] = name
    try:
        # The voice assistant's full-duplex model: its Compose file names the folder through .env,
        # so no mount above claims it.
        result[str(duplex_voice.models_dir(paths).parent.relative_to(paths.root))] = duplex_voice.voice_app().id
    except ValueError:
        pass
    try:
        # Kopia's repository is the backup app's data.
        result["backups"] = backup_service.service_id()
    except ValueError:
        result["backups"] = own
    return result


def folders(paths: RuntimePaths, image: str, run=process.completed) -> dict[str, int]:
    """Bytes of each folder under the data and media roots, plus backups, state and projects.

    App data belongs to container users (postgres, mysql), so ``du`` runs as root in a
    short-lived container that sees the runtime root read-only, with networking off.
    """
    command = "cd /r && du -sk data/* media/* backups state projects 2>/dev/null; true"
    argv = ["docker", "run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "sh"]
    argv += ["-v", f"{paths.root}:/r:ro", image, "-c", command]
    try:
        proc = run(docker_argv(argv), capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError):
        return {}
    result = {}
    for line in proc.stdout.splitlines():
        size, _, name = line.partition("\t")
        if size.isdigit() and name:
            result[name.strip()] = int(size) * 1024
    return result


def measure_disk(registry, root: Path, paths: RuntimePaths, run=process.completed) -> dict[str, Any]:
    """Data and program bytes per owner, and when they were measured."""
    from ctl.disk_cleanup import reclaimable, removed_data
    from ctl.lifecycle.uninstall import _cleanup_image

    owner = owners(registry, root, paths, split_core=True)
    split: dict[str, dict[str, int]] = defaultdict(lambda: {"data": 0, "program": 0})
    mapping = folder_owners(registry, root, paths, split_core=True)
    for name, size in folders(paths, _cleanup_image(root), run).items():
        split[mapping.get(name, REMOVED if name.startswith("data/") else f"{CORE}/{OWN_PART}")]["data"] += size
    for name, sizes in docker_usage(owner, reclaimable(registry, root, paths), run).items():
        split[name]["data"] += sizes["data"]
        split[name]["program"] += sizes["program"]
    usage: dict[str, dict[str, int]] = defaultdict(lambda: {"data": 0, "program": 0})
    parts: dict[str, dict[str, int]] = {}
    for name, value in split.items():
        if core_part(name):
            parts[core_part(name)] = value
        usage[_fold(name)]["data"] += value["data"]
        usage[_fold(name)]["program"] += value["program"]
    used = lambda values: {name: value for name, value in values.items() if value["data"] or value["program"]}  # noqa: E731
    return {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "owners": used(usage),
        "core_parts": used(parts),
        "removed_folders": [folder.name for folder in removed_data(registry, root, paths)],
    }


def stored_disk(paths: RuntimePaths | None = None) -> dict[str, Any] | None:
    from ctl.store import records

    value = records.get(*DISK_RECORD, paths or RuntimePaths())
    return value or None


def start(stopping: threading.Event, root: Path, info: Callable[[str], None]) -> threading.Thread:
    """Once a day, and soon after an app is added or removed: free what Mu3Lab may, then measure."""

    def loop() -> None:
        from ctl import disk_cleanup
        from ctl.install_batches import InstallBatchStore
        from ctl.jobs import JobStore
        from ctl.registry import load
        from ctl.store import records

        paths = RuntimePaths()
        previous = stored_disk(paths)
        last = 0.0
        if previous:
            try:
                age = time.time() - datetime.fromisoformat(previous["measured_at"]).timestamp()
                last = time.monotonic() - max(0.0, age)
            except (KeyError, ValueError):
                pass
        while not stopping.is_set():
            if disk_stale.is_set() or not last or time.monotonic() - last >= DISK_EVERY:
                disk_stale.clear()
                try:
                    registry = load()
                    # Removed apps' containers and old versions' images; data only when someone confirms.
                    if not disk_cleanup.busy(JobStore.runtime(), InstallBatchStore.runtime()):
                        disk_cleanup.free_up(registry, root, info, paths=paths)
                    records.put(*DISK_RECORD, measure_disk(registry, root, paths), paths)
                except Exception as exc:  # informational; the next pass tries again
                    info(f"Mu3Lab disk measurement deferred: {exc}")
                last = time.monotonic()
            # Wait a little after an install so a burst of jobs is measured once.
            if disk_stale.wait(timeout=300):
                stopping.wait(60)

    thread = threading.Thread(target=loop, name="mu3lab-disk-usage", daemon=True)
    thread.start()
    return thread


# Jobs after which an app's images or data may have come or gone.
DISK_ACTIONS = frozenset({"install", "uninstall", "uninstall_delete_data", "update", "restore", "switch_compute"})


def touches_disk(job: dict) -> bool:
    return str(job.get("action") or "") in DISK_ACTIONS
