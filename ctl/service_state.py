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
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from ctl.actions import docker_argv
from ctl.registry import Service
from ctl.runtime import RuntimePaths


def tailscale_status(run=subprocess.run) -> dict[str, object]:
    """Return a minimal, sanitized projection of this node's Tailscale status."""
    try:
        proc = run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {
            "state": "unavailable",
            "backend_state": "",
            "online": False,
            "dns_name": "",
            "detail": "Tailscale status is unavailable.",
        }
    if proc.returncode != 0:
        return {
            "state": "disconnected",
            "backend_state": "",
            "online": False,
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
            "state": "unavailable",
            "backend_state": "",
            "online": False,
            "dns_name": "",
            "detail": "Tailscale status is unavailable.",
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
        "state": state,
        "backend_state": backend_state,
        "online": online,
        "dns_name": dns_name,
        "detail": detail,
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
        request = urllib.request.Request(url, headers=health.get("headers", {}))
        with urllib.request.urlopen(request, timeout=3) as response:
            return 200 <= response.status < 400, f"HTTP {response.status}"
    except (urllib.error.URLError, OSError) as exc:
        return False, f"unreachable: {exc.reason if isinstance(exc, urllib.error.URLError) else exc}"


ROUTE_PROBE_TTL = 60.0
# A failed probe is repeated sooner, so a blip is confirmed or cleared quickly.
ROUTE_FAILURE_TTL = 30.0
_route_probes: dict[str, tuple[float, bool, float]] = {}


def route_probe(url: str, now=time.monotonic, clock=time.time) -> tuple[bool, float]:
    """Whether the app's private HTTPS address really answers, and when that was checked.

    A published Tailscale port only shows the route is configured. This
    fetches it through Tailscale and Caddy with certificate checks on. A
    redirect to sign in, or an app's own "please log in", still counts as
    answering; 404 (for example an app the sign-in gate does not know) and
    server errors do not. Results are reused briefly; a reused result keeps
    its original check time so it is never counted as a second observation.
    """
    cached = _route_probes.get(url)
    if cached and now() - cached[0] < (ROUTE_PROBE_TTL if cached[1] else ROUTE_FAILURE_TTL):
        return cached[1], cached[2]

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    try:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(urllib.request.Request(url, method="GET"), timeout=4) as response:
            answered = response.status < 400
    except urllib.error.HTTPError as exc:
        answered = exc.code in {301, 302, 303, 307, 308, 401, 403}
        exc.close()
    except (urllib.error.URLError, OSError, ValueError):
        answered = False
    checked_at = clock()
    _route_probes[url] = (now(), answered, checked_at)
    return answered, checked_at


def route_answers(url: str, now=time.monotonic) -> bool:
    return route_probe(url, now=now)[0]


def _tailnet_route_present(port: int) -> bool | None:
    """Check only the local Tailscale Serve configuration; None when it cannot be read."""
    try:
        proc = subprocess.run(["tailscale", "serve", "status"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return port in serve_ports(proc.stdout)


def tailnet_serve_status(run=subprocess.run) -> dict[str, object]:
    """Return whether local Serve status is readable and its HTTPS ports."""
    try:
        proc = run(["tailscale", "serve", "status"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {"state": "unavailable", "ports": []}
    if proc.returncode != 0:
        return {"state": "unavailable", "ports": []}
    return {"state": "available", "ports": sorted(serve_ports(proc.stdout))}


def serve_ports(output: str) -> set[int]:
    """HTTPS listener ports in `tailscale serve status` text output.

    A listener on the default port prints as ``https://host`` with no port, so
    a missing port means 443. Proxy targets (``http://127.0.0.1:19461``) are
    backends, not listeners, and are ignored.
    """
    ports = {int(value) for value in re.findall(r"https=(\d{2,5})", output)}
    for token in output.split():
        if not token.startswith("https://"):
            continue
        try:
            ports.add(urlsplit(token).port or 443)
        except ValueError:
            continue
    return ports


def tailnet_serve_ports() -> set[int]:
    """Read Tailscale Serve once and return configured HTTPS ports."""
    ports = tailnet_serve_status()["ports"]
    return {int(port) for port in ports} if isinstance(ports, list) else set()


def _compose_state(compose_file: Path, run=subprocess.run) -> str:
    """Read containers by Compose labels without evaluating private env files."""
    try:
        proc = run(
            docker_argv(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--filter",
                    f"label=com.docker.compose.project.working_dir={compose_file.parent.resolve()}",
                    "--format",
                    "{{json .}}",
                ]
            ),
            capture_output=True,
            text=True,
            timeout=8,
        )
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
        long_lived = [
            row
            for row in rows
            if not (
                str(row.get("State", "")).lower() == "exited" and "exited (0)" in str(row.get("Status", "")).lower()
            )
        ]
        states = {str(row.get("State", "")).lower() for row in long_lived}
    except (ValueError, TypeError):
        return "unknown"
    if states and states == {"running"}:
        return "running"
    return "stopped"


def compose_states(run=subprocess.run) -> dict[str, str]:
    """Inspect all Compose working directories with one bounded Docker call."""
    try:
        proc = run(
            docker_argv(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--format",
                    '{{.Label "com.docker.compose.project.working_dir"}}\t{{.State}}',
                ]
            ),
            capture_output=True,
            text=True,
            timeout=8,
        )
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
    return {
        directory: ("running" if states and set(states) == {"running"} else "stopped")
        for directory, states in collected.items()
    }


_MEM_UNITS = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4}
_MEMORY_TTL = 15.0
_memory_cache: tuple[float, dict[str, int]] = (0.0, {})


def _memory_bytes(text: str) -> int | None:
    """Parse the "used" half of Docker's "3.2GiB / 15GiB" memory column."""
    match = re.match(r"\s*([\d.]+)\s*([KMGT]?i?B)\b", text, re.IGNORECASE)
    if not match:
        return None
    return int(float(match.group(1)) * _MEM_UNITS.get(match.group(2).lower(), 1))


def container_memory(run=subprocess.run, now=time.monotonic) -> dict[str, int]:
    """Memory in bytes used by each running container, keyed by container name.

    ``docker stats`` samples for about a second and the dashboard polls every
    few seconds, so the answer is cached briefly.
    """
    global _memory_cache
    stamp, cached = _memory_cache
    if stamp and now() - stamp < _MEMORY_TTL:
        return cached
    try:
        proc = run(
            docker_argv(["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"]),
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return cached
    if proc.returncode != 0:
        return cached
    usage: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        name, separator, used = line.partition("\t")
        amount = _memory_bytes(used) if separator else None
        if amount is not None:
            usage[name] = amount
    _memory_cache = (now(), usage)
    return usage


def compose_snapshot(run=subprocess.run) -> tuple[dict[str, str], dict[str, list[dict[str, str]]]]:
    """Inspect all Compose projects and safe container fields in one Docker call."""
    try:
        proc = run(
            docker_argv(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--format",
                    '{{.Label "com.docker.compose.project.working_dir"}}\t'
                    '{{.Label "com.docker.compose.service"}}\t{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Image}}',
                ]
            ),
            capture_output=True,
            text=True,
            timeout=8,
        )
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
        containers.setdefault(directory, []).append(
            {
                "service": parts[1],
                "name": parts[2],
                "state": state,
                "status": parts[4],
                "health": health,
                "image": parts[5],
            }
        )
    # Compose projects commonly include a migration container. A successful
    # one-shot exit is evidence of completion, not a stopped application.
    project_states = {}
    for directory in states:
        rows = containers.get(directory, [])
        long_lived = [row["state"] for row in rows if not (row["state"] == "exited" and "Exited (0)" in row["status"])]
        project_states[directory] = "running" if long_lived and set(long_lived) == {"running"} else "stopped"
    return project_states, containers


def status(
    service: Service,
    dns_name: str,
    root: Path,
    route_ports: set[int] | None = None,
    project_states: dict[str, str] | None = None,
    *,
    serve_readable: bool = True,
    previous: dict | None = None,
    now: float | None = None,
) -> dict:
    """Return browser-safe service state without starting, stopping, or logging in.

    ``previous`` is this service's last observation; its checks carry route
    history (last success, consecutive failures) into this cycle.
    """
    from ctl.status.checks import Check, process_check, route_check

    now = time.time() if now is None else now
    runtime_file = RuntimePaths().projects / service.id / "docker-compose.yml"
    compose_file = runtime_file if runtime_file.is_file() else service.compose_path(root) / "docker-compose.yml"
    if not compose_file.is_file():
        lifecycle_state, detail = "planned", "This curated stack is not installed yet."
    else:
        compose_state = (
            (project_states or {}).get(str(compose_file.parent.resolve()), "absent")
            if project_states is not None
            else _compose_state(compose_file)
        )
        source_file = service.compose_path(root) / "docker-compose.yml"
        if compose_state == "absent" and source_file != compose_file and source_file.is_file():
            # Older installers started some foundation stacks from the checkout, not the rendered project.
            compose_state = (
                (project_states or {}).get(str(source_file.parent.resolve()), "absent")
                if project_states is not None
                else _compose_state(source_file)
            )
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
    prior_checks = (previous or {}).get("checks") or {}
    process = process_check(Check.load(prior_checks.get("process")), lifecycle_state, detail, now)
    route_port = service.private_https_port
    route_required = route_port is not None
    route_configured: bool | None
    if route_port is None or service.route == "ready":
        route_configured = True
    elif route_ports is not None:
        route_configured = (route_port in route_ports) if serve_readable else None
    else:
        route_configured = _tailnet_route_present(route_port)
    healthy = lifecycle_state == "ready"
    health_state = "healthy" if healthy else ("starting" if lifecycle_state == "starting" else "unknown")
    probe = (
        route_probe(f"https://{dns_name}:{route_port}{service.ui.get('path', '') or '/'}")
        if route_required and healthy and dns_name and route_configured is not False
        else None
    )
    route = route_check(
        Check.load(prior_checks.get("route")),
        required=route_required,
        configured=route_configured,
        healthy=healthy,
        dns_known=bool(dns_name),
        probe=probe,
        now=now,
    )
    # "verified" means a real request through the private address succeeded
    # (or a working route is inside its short grace period after one miss);
    # a published port alone is only "configured".
    route_state = (
        "not_required"
        if not route_required
        else "verified"
        if route.satisfied
        else "configured"
        if route_configured is not False and healthy
        else service.route
    )
    route_ready = route_required and route.satisfied
    # A healthy process is not yet a usable app unless its declared private
    # route has also been verified. Keep that distinction visible so the UI
    # cannot call a merely-installed service "ready".
    if healthy and route_required and not route_ready:
        lifecycle_state = "needs_setup"
        detail = route.detail or "The app is healthy, but its required private HTTPS route has not passed verification."
    setup_state = (
        "configured" if lifecycle_state == "ready" else ("blocked" if lifecycle_state == "blocked" else "needs_setup")
    )
    ui = dict(service.ui)
    ui_available = bool(ui.get("available", False))
    url = public_url(service, dns_name) if route_ready else ""
    if route_ready and ui_available and not url and dns_name:
        port = service.private_https_port or service.https_port
        suffix = "" if port == 443 else f":{port}"
        path = str(ui.get("path", ""))
        url = f"https://{dns_name}{suffix}{path}"
    ui_state = "unavailable" if not ui_available else "ready" if route_ready and bool(url) else "route_pending"
    ui_reason = (
        str(ui.get("unavailable_reason", ""))
        if not ui_available
        else "Private UI route is not ready yet."
        if ui_state == "route_pending"
        else ""
    )
    from ctl.lifecycle import app_releases

    approved = str(service.update.get("approved_version", ""))
    release = (
        app_releases.status(service, root)
        if service.stage == "optional"
        # Core services are pinned and move only together with Mu3Lab itself.
        else {"installed_version": approved, "approved_version": approved, "update_available": False}
    )
    return {
        **service.public(),
        "update": {"repository": str(service.update.get("repository", "")), "supporting_only": False, **release},
        "state": lifecycle_state,
        "detail": detail,
        "lifecycle_state": lifecycle_state,
        "health_state": health_state,
        "setup_state": setup_state,
        "route_state": route_state,
        "identity_mode": service.auth,
        "backup_state": "declared",
        "last_job_id": "",
        "last_error": detail if lifecycle_state == "needs_attention" else "",
        "user_action": (
            "Review health diagnostics"
            if lifecycle_state == "needs_attention"
            else "Start this service"
            if lifecycle_state == "stopped"
            else "Wait for the health check"
            if lifecycle_state == "starting"
            else "Open securely"
            if route_ready
            else "Private HTTPS route pending"
            if healthy
            else service.setup_action or detail
        ),
        "url": url,
        "route_ready": route_ready,
        "ui": {
            "state": ui_state,
            "url": url or None,
            "label": "Open securely",
            "launch_label": str(ui.get("label", "Open")),
            "authentication": str(ui.get("authentication", service.auth)),
            "reason": ui_reason,
        },
        "compose_present": (runtime_file.is_file() if service.stage == "optional" else compose_file.is_file()),
        "checks": {"process": process.public(), "route": route.public()},
        "blocking_check": "route" if healthy and route_required and not route_ready else None,
    }
