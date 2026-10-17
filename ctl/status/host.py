"""Host observations collected only by the background worker."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import psutil

from ctl import process, resource_usage
from ctl.actions import docker_argv
from ctl.backups import readiness as backup_readiness
from ctl.runtime import RuntimePaths
from ctl.service_state import tailnet_serve_status, tailscale_status


def _worker_state() -> str:
    """systemd's word for the background worker ("active", "failed", ...), or "unknown"."""
    try:
        proc = process.completed(
            ["systemctl", "--user", "is-active", "mu3lab-worker.service"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    state = proc.stdout.strip()
    # No user session bus (e.g. a development run) prints nothing useful.
    return state if state in {"active", "activating", "deactivating", "inactive", "failed"} else "unknown"


def observe_system(registry=None, root: Path | None = None) -> dict:
    """Read-only host capacity and private-network status for the dashboard."""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(RuntimePaths().root.parent))
    try:
        docker = (
            process.completed(docker_argv(["docker", "info"]), capture_output=True, text=True, timeout=5).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        docker = False
    by_name, by_owner, core_parts = (
        resource_usage.memory(registry, root) if docker and registry is not None and root else ({}, {}, {})
    )
    tailscale = tailscale_status()
    tailscale["serve"] = tailnet_serve_status()
    return {
        "ok": True,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "uptime_seconds": max(0, int(time.time() - psutil.boot_time())),
        "memory": {"total": memory.total, "used": memory.used, "percent": memory.percent},
        "disk": {"total": disk.total, "used": disk.used, "percent": disk.percent},
        "docker_ready": docker,
        "container_memory": by_name,
        "memory_by_owner": by_owner,
        "memory_by_core_part": core_parts,
        "disk_usage": resource_usage.stored_disk(),
        "worker_state": _worker_state(),
        "tailnet_dns_name": tailscale["dns_name"],
        "tailscale": tailscale,
        "runtime_root": str(RuntimePaths().root),
        "backup": backup_readiness(),
    }
