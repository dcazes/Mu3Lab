"""Mu3Lab :: ctl/service_state.py

WHAT: Read-only health and reachability projections for curated services.
WHY: The browser must never infer service state from a hard-coded card or a
     user-provided URL. Lifecycle mutations belong to authenticated jobs.
"""

from __future__ import annotations

import json
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from ctl.registry import Service


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
    """Build the only user-facing route: tailnet HTTPS, never localhost."""
    if not dns_name:
        return ""
    suffix = "" if service.https_port == 443 else f":{service.https_port}"
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


def status(service: Service, dns_name: str, root: Path) -> dict:
    """Return browser-safe service state without starting, stopping, or logging in."""
    compose_file = service.compose_path(root) / "docker-compose.yml"
    if service.is_blocked:
        state, detail = "blocked", service.blocked_reason
    elif not compose_file.is_file():
        state, detail = "not_installed", "Curated stack has not been added to this checkout yet."
    else:
        ok, detail = _healthy(service)
        state = "healthy" if ok else "stopped_or_unhealthy"
    return {**service.public(), "state": state, "detail": detail,
            "url": public_url(service, dns_name),
            "compose_present": compose_file.is_file()}
