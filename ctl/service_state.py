"""Mu3Lab :: ctl/service_state.py

WHAT: Read-only health and reachability projections for curated services.
WHY: The browser must never infer service state from a hard-coded card or a
     user-provided URL. Lifecycle mutations belong to authenticated jobs.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from ctl.registry import Service
from ctl.runtime import RuntimePaths


def tailnet_dns_name(run=subprocess.run) -> str:
    """Return this node's MagicDNS name without exposing local fallback URLs."""
    try:
        proc = run(["tailscale", "status", "--json"], capture_output=True,
                   text=True, timeout=5)
        if proc.returncode != 0:
            return ""
        data = json.loads(proc.stdout)
        return str(data.get("Self", {}).get("DNSName", "")).rstrip(".")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return ""


def public_url(service: Service, dns_name: str) -> str:
    """Build a tailnet URL only after the manifest declares a verified route."""
    if not dns_name or service.route != "ready":
        return ""
    port = service.private_https_port or service.https_port
    suffix = "" if port == 443 else f":{port}"
    return f"https://{dns_name}{suffix}"


def _tcp_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def _healthy(service: Service) -> tuple[bool, str]:
    """Probe declared loopback health targets; never raises to the API."""
    health = service.health
    if health["kind"] == "tcp":
        port = int(health["port"])
        return _tcp_open(port), f"TCP port {port}"
    url = str(health["url"])
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return 200 <= response.status < 400, f"HTTP {response.status}"
    except (urllib.error.URLError, OSError) as exc:
        return False, f"unreachable: {exc.reason if isinstance(exc, urllib.error.URLError) else exc}"


def _tailnet_route_present(port: int) -> bool:
    """Check only the local Tailscale Serve configuration, never a URL input."""
    try:
        proc = subprocess.run(["tailscale", "serve", "status"], capture_output=True,
                              text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    output = proc.stdout
    return f":{port}" in output or f"https={port}" in output


def tailnet_serve_ports() -> set[int]:
    """Read Tailscale Serve once and return configured HTTPS ports."""
    try:
        proc = subprocess.run(["tailscale", "serve", "status"], capture_output=True,
                              text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0:
        return set()
    return {int(value) for value in re.findall(
        r"(?::|https=)(\d{2,5})", proc.stdout)}


def _compose_state(compose_file: Path, run=subprocess.run) -> str:
    """Read containers by Compose labels without evaluating private env files."""
    try:
        proc = run([
            "docker", "ps", "--all",
            "--filter", f"label=com.docker.compose.project.working_dir={compose_file.parent.resolve()}",
            "--format", "{{json .}}",
        ], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if proc.returncode != 0 or not proc.stdout.strip():
        return "absent"
    try:
        rows = []
        for line in proc.stdout.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        states = {str(row.get("State", "")).lower() for row in rows}
    except (ValueError, TypeError):
        return "unknown"
    if states and states == {"running"}:
        return "running"
    return "stopped"


def compose_states(run=subprocess.run) -> dict[str, str]:
    """Inspect all Compose working directories with one bounded Docker call."""
    try:
        proc = run([
            "docker", "ps", "--all",
            "--format", '{{.Label "com.docker.compose.project.working_dir"}}\t{{.State}}',
        ], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    collected: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        directory, separator, state = line.partition("\t")
        if not separator or not directory:
            continue
        collected.setdefault(str(Path(directory).resolve()), []).append(state.lower())
    return {directory: ("running" if states and set(states) == {"running"} else "stopped")
            for directory, states in collected.items()}


def status(service: Service, dns_name: str, root: Path,
           route_ports: set[int] | None = None,
           project_states: dict[str, str] | None = None) -> dict:
    """Return browser-safe service state without starting, stopping, or logging in."""
    runtime_file = RuntimePaths().projects / service.id / "docker-compose.yml"
    compose_file = (runtime_file if service.stage == "optional" and runtime_file.is_file()
                    else service.compose_path(root) / "docker-compose.yml")
    if service.is_blocked:
        lifecycle_state, detail = "blocked", service.blocked_reason
    elif not compose_file.is_file():
        lifecycle_state, detail = "planned", "This curated stack is not installed yet."
    else:
        compose_state = ((project_states or {}).get(str(compose_file.parent.resolve()), "absent")
                         if project_states is not None else _compose_state(compose_file))
        ok, detail = _healthy(service)
        if ok and compose_state == "running":
            lifecycle_state = "ready"
        elif compose_state == "absent":
            lifecycle_state = "planned"
            detail = "This curated stack is not installed yet."
        elif compose_state == "stopped":
            lifecycle_state = "stopped"
            detail = "Compose project is installed but not running."
        elif compose_state == "running":
            lifecycle_state = "starting"
        else:
            lifecycle_state = "needs_attention"
    route_required = service.private_https_port is not None
    route_verified = not route_required or service.route == "ready" or (
        route_required and
        (service.private_https_port in route_ports if route_ports is not None
         else _tailnet_route_present(service.private_https_port)))
    healthy = lifecycle_state == "ready"
    health_state = "healthy" if healthy else ("starting" if lifecycle_state == "starting" else "unknown")
    route_state = ("not_required" if not route_required else
                   "verified" if route_verified and healthy else service.route)
    route_ready = route_required and route_state == "verified"
    # A healthy process is not yet a usable app unless its declared private
    # route has also been verified. Keep that distinction visible so the UI
    # cannot call a merely-installed service "ready".
    if healthy and route_required and not route_ready:
        lifecycle_state = "needs_setup"
    setup_state = "configured" if lifecycle_state == "ready" else ("blocked" if lifecycle_state == "blocked" else "needs_setup")
    url = public_url(service, dns_name)
    if route_ready and not url and dns_name:
        port = service.private_https_port or service.https_port
        url = f"https://{dns_name}" + ("" if port == 443 else f":{port}")
    return {**service.public(), "state": lifecycle_state, "detail": detail,
            "lifecycle_state": lifecycle_state, "health_state": health_state,
            "setup_state": setup_state, "route_state": route_state,
            "identity_mode": service.auth, "backup_state": "declared" if service.backup else "not_declared",
            "last_job_id": "", "last_error": detail if lifecycle_state == "needs_attention" else "",
            "user_action": ("Review health diagnostics" if lifecycle_state == "needs_attention" else
                            "Start this service" if lifecycle_state == "stopped" else
                            "Wait for the health check" if lifecycle_state == "starting" else
                            "Open securely" if route_ready else
                            "Private HTTPS route pending" if healthy else
                            service.setup_action or detail),
            "url": url,
            "route_ready": route_ready,
            "compose_present": (runtime_file.is_file() if service.stage == "optional"
                                else compose_file.is_file())}
