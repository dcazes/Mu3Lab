"""Every installed app's addresses and ports, for Settings > Ports (administrators only).

Read from each app's manifest (routes and published ports) and its Compose file (the port
inside the container, when it differs). Nothing here names an app.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ctl.control_state import ControlState
from ctl.manifest.catalog import cached
from ctl.service_state import home_address, tailnet_dns_name, tailnet_ip

# "127.0.0.1:8479:8479", "${MU3LAB_HOME_IP:?...}:8096:8096/tcp", "7881:7881/udp": the published port, then the target.
_PORT = re.compile(r"(\d+):(\d+)(?:/(tcp|udp))?$")


def _container_ports(compose: Path) -> dict[tuple[int, str], int]:
    """Published port and protocol -> the port inside the container, from the app's Compose file."""
    try:
        document = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    found: dict[tuple[int, str], int] = {}
    for spec in (document.get("services") or {}).values():
        for entry in (spec or {}).get("ports") or []:
            if isinstance(entry, dict):
                published, target = entry.get("published"), entry.get("target")
                if published and target:
                    found[(int(published), str(entry.get("protocol") or "tcp"))] = int(target)
            elif isinstance(entry, str | int) and (match := _PORT.search(str(entry).strip().strip('"'))):
                found[(int(match.group(1)), match.group(3) or "tcp")] = int(match.group(2))
    return found


def _https(host: str, port: int) -> str:
    return f"https://{host}" if port == 443 else f"https://{host}:{port}"


def _installed(state: ControlState | None, app_id: str) -> bool:
    if state is None:
        return False
    installation = state.app_installation(app_id)
    return bool(installation and installation.get("state") not in {None, "not_installed"})


def _entry(
    inside: dict[tuple[int, str], int], kind: str, label: str, address: str, port: int, protocol: str = "tcp"
) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "address": address,
        "port": port,
        "protocol": protocol,
        "container_port": inside.get((port, protocol)),
        "note": "",
    }


def report(root: Path) -> dict[str, Any]:
    """Each installed app with every address people open and every port it holds on this server."""
    host = tailnet_dns_name()
    tailnet = tailnet_ip()
    home = home_address()
    home_ip = home.ip if home else ""
    state = ControlState.runtime()
    apps: list[dict[str, Any]] = []
    for app in cached().apps:
        if not _installed(state, app.id):
            continue
        manifest = app.manifest
        inside = _container_ports(root / "apps" / app.id / "docker-compose.yml")
        entries: list[dict[str, Any]] = []
        route = manifest.route
        if route is not None:
            address = _https(host, route.https_port) if host else ""
            entries.append(_entry(inside, "tailnet", "Main address", address, route.https_port))
        for extra_route in manifest.extra_routes:
            address = _https(host, extra_route.https_port) if host else ""
            entries.append(_entry(inside, "tailnet", extra_route.label, address, extra_route.https_port))
        service = manifest.service
        if service.home_port is not None:
            address = f"http://{home_ip}:{service.home_port}" if home_ip else ""
            entries.append(_entry(inside, "home", "Main address", address, service.home_port))
        address = f"http://127.0.0.1:{service.local_port}"
        entries.append(_entry(inside, "loopback", "App", address, service.local_port))
        for extra_port in service.extra_ports:
            if extra_port.listen == "home" and extra_port.port == service.home_port and extra_port.protocol == "tcp":
                continue  # already listed as the home-network address
            ip = {"loopback": "127.0.0.1", "tailnet": tailnet, "home": home_ip}[extra_port.listen]
            address = f"{ip}:{extra_port.port}" if ip else ""
            entries.append(
                _entry(inside, extra_port.listen, extra_port.purpose, address, extra_port.port, extra_port.protocol)
            )
        # Caddy's own route proxies to itself: its loopback port is already listed.
        if route is not None and route.caddy_port != service.local_port:
            address = f"127.0.0.1:{route.caddy_port}"
            entries.append(_entry(inside, "proxy", "Sign-in (Caddy)", address, route.caddy_port))
        for extra_route in manifest.extra_routes:
            # Its own loopback port is one of service.extra_ports, listed above.
            label = f"Sign-in (Caddy) for {extra_route.label}"
            entries.append(
                _entry(inside, "proxy", label, f"127.0.0.1:{extra_route.caddy_port}", extra_route.caddy_port)
            )
        apps.append({"id": app.id, "name": manifest.name, "entries": entries})
    apps.sort(key=lambda item: str(item["name"]).lower())
    return {"ok": True, "tailnet_dns_name": host, "home_ip": home_ip, "apps": apps}
