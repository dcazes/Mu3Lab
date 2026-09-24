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


def tailscale_status(run=subprocess.run) -> dict[str, object]:
    """Return a minimal, sanitized projection of this node's Tailscale status."""
    try:
        proc = run(["tailscale", "status", "--json"], capture_output=True,
                   text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {
            "state": "unavailable", "backend_state": "", "online": False,
            "dns_name": "", "detail": "Tailscale status is unavailable.",
        }
    if proc.returncode != 0:
        return {
            "state": "disconnected", "backend_state": "", "online": False,
            "dns_name": "",
            "detail": "Tailscale is installed but its status could not be read.",
        }
    try:
        data = json.loads(proc.stdout)
        if not isinstance(data, dict):
            raise ValueError("Tailscale status JSON is not an object")
        backend_value = data.get("BackendState", "")
        backend_state = str(backend_value) if backend_value is not None else ""
        self_data = data.get("Self")
        if not isinstance(self_data, dict):
            self_data = {}
        online = self_data.get("Online") is True
        dns_value = self_data.get("DNSName", "")
        dns_name = str(dns_value).rstrip(".") if dns_value is not None else ""
    except (TypeError, ValueError, AttributeError):
        return {
            "state": "unavailable", "backend_state": "", "online": False,
            "dns_name": "", "detail": "Tailscale status is unavailable.",
        }

    if backend_state.casefold() == "running" and online:
        state = "connected"
        detail = "Connected to the tailnet."
    else:
        state = "disconnected"
        if backend_state.casefold() == "needslogin":
            detail = "Tailscale needs sign-in."
        elif backend_state.casefold() == "running" and not online:
            detail = "Tailscale is running, but this device is offline."
        else:
            detail = "Tailscale is not connected."
    return {
        "state": state, "backend_state": backend_state, "online": online,
        "dns_name": dns_name, "detail": detail,
    }


def tailnet_dns_name(run=subprocess.run) -> str:
    """Return this node's MagicDNS name without exposing local fallback URLs."""
    return str(tailscale_status(run=run)["dns_name"])


def public_url(service: Service, dns_name: str) -> str:
    """Build a browser URL only for a manifest-declared UI contract."""
    if not dns_name or service.route != "ready" or not bool(service.ui.get("available", False)):
        return ""
    port = service.private_https_port or service.https_port
    suffix = "" if port == 443 else f":{port}"
    path = str(service.ui.get("path", ""))
    if path and not path.startswith("/"):
        path = "/" + path
    return f"https://{dns_name}{suffix}{path}"


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


def tailnet_serve_status(run=subprocess.run) -> dict[str, object]:
    """Return whether local Serve status is readable and its HTTPS ports."""
    try:
        proc = run(["tailscale", "serve", "status"], capture_output=True,
                   text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {"state": "unavailable", "ports": []}
    if proc.returncode != 0:
        return {"state": "unavailable", "ports": []}
    ports = sorted({int(value) for value in re.findall(
        r"(?::|https=)(\d{2,5})", proc.stdout)})
    return {"state": "available", "ports": ports}


def tailnet_serve_ports() -> set[int]:
    """Read Tailscale Serve once and return configured HTTPS ports."""
    return set(tailnet_serve_status()["ports"])


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
        # One-shot migration containers commonly remain as Exited (0) after
        # a successful deployment. They must not make the whole multi-service
        # application look stopped when the long-lived containers are running.
        long_lived = [row for row in rows
                      if not (str(row.get("State", "")).lower() == "exited"
                              and "exited (0)" in str(row.get("Status", "")).lower())]
        states = {str(row.get("State", "")).lower() for row in long_lived}
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


def compose_snapshot(run=subprocess.run) -> tuple[dict[str, str], dict[str, list[dict[str, str]]]]:
    """Inspect all Compose projects and safe container fields in one Docker call."""
    try:
        proc = run([
            "docker", "ps", "--all", "--format",
            '{{.Label "com.docker.compose.project.working_dir"}}\t'
            '{{.Label "com.docker.compose.service"}}\t{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Image}}',
        ], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return {}, {}
    if proc.returncode != 0:
        return {}, {}
    containers: dict[str, list[dict[str, str]]] = {}
    states: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 6 or not parts[0]:
            continue
        directory = str(Path(parts[0]).resolve())
        state = parts[3].lower()
        states.setdefault(directory, []).append(state)
        health = "unknown"
        match = re.search(r"\((healthy|unhealthy|health: starting)\)", parts[4].lower())
        if match:
            health = match.group(1).replace("health: ", "")
        containers.setdefault(directory, []).append({
            "service": parts[1], "name": parts[2], "state": state,
            "status": parts[4], "health": health, "image": parts[5],
        })
    # Compose projects commonly include a migration container. A successful
    # one-shot exit is evidence of completion, not a stopped application.
    project_states = {}
    for directory, values in states.items():
        rows = containers.get(directory, [])
        long_lived = [row["state"] for row in rows
                      if not (row["state"] == "exited" and "Exited (0)" in row["status"])]
        project_states[directory] = "running" if long_lived and set(long_lived) == {"running"} else "stopped"
    return project_states, containers


def status(service: Service, dns_name: str, root: Path,
           route_ports: set[int] | None = None,
           project_states: dict[str, str] | None = None) -> dict:
    """Return browser-safe service state without starting, stopping, or logging in."""
    runtime_file = RuntimePaths().projects / service.id / "docker-compose.yml"
    compose_file = (runtime_file if (service.stage == "optional" or service.id == "lobehub")
                    and runtime_file.is_file()
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
    ui = dict(service.ui)
    ui_available = bool(ui.get("available", False))
    url = public_url(service, dns_name)
    if route_ready and ui_available and not url and dns_name:
        port = service.private_https_port or service.https_port
        suffix = "" if port == 443 else f":{port}"
        path = str(ui.get("path", ""))
        url = f"https://{dns_name}{suffix}{path}"
    ui_state = ("unavailable" if not ui_available else
                "ready" if route_ready and bool(url) else "route_pending")
    ui_reason = (str(ui.get("unavailable_reason", "")) if not ui_available else
                 "Private UI route is not ready yet." if ui_state == "route_pending" else "")
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
            "ui": {"state": ui_state, "url": url or None,
                   "label": "Open securely", "authentication": str(ui.get("authentication", service.auth)),
                   "reason": ui_reason},
            "compose_present": (runtime_file.is_file() if service.stage == "optional"
                                else compose_file.is_file())}
