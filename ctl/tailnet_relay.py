"""Let app containers reach this server's private HTTPS address (see platform/tailnet-relay).

Apps that sign in through Authentik fetch its sign-in settings from the
server's private name from inside their containers. Tailscale (1.104 and
later) accepts that connection only over the tailnet or from the host itself,
so it refuses containers. Each such app maps the name to Docker's host
address; the relay listens there, on port 443 only, and forwards the still
encrypted connection to Tailscale.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from collections.abc import Callable
from pathlib import Path

from ctl import actions
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "platform" / "tailnet-relay"
PORT = 443

Log = Callable[[str], None]


def config_path(paths: RuntimePaths | None = None) -> Path:
    return (paths or RuntimePaths()).projects / "tailnet-relay" / "nginx.conf"


def host_address(log: Log) -> str:
    """The address Docker gives containers for ``host-gateway``: the default bridge's gateway."""
    rc, out = actions.docker_cmd(
        ["docker", "network", "inspect", "bridge", "--format", "{{json .IPAM.Config}}"], log, timeout=30
    )
    if rc != 0:
        return ""
    try:
        configs = json.loads(out.strip().splitlines()[-1])
        for config in configs or []:
            gateway = ipaddress.ip_address(str(config.get("Gateway", "")))
            if gateway.version == 4 and gateway.is_private:
                return str(gateway)
    except (ValueError, IndexError, AttributeError, TypeError):
        pass
    return ""


def render(host: str, address: str) -> str:
    """nginx stream configuration: TCP pass-through from ``address:443`` to ``host:443``."""
    ipaddress.IPv4Address(address)
    if not host or any(char.isspace() or char in ";{}'\"" for char in host):
        raise ValueError("This server's private name is not valid.")
    return f"""# Written by Mu3Lab (ctl/tailnet_relay.py); changes are replaced.
user nginx;
worker_processes 1;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events {{ worker_connections 512; }}
stream {{
  server {{
    listen {address}:{PORT};
    # Only containers on this computer; Docker's networks use private addresses.
    allow 10.0.0.0/8;
    allow 172.16.0.0/12;
    allow 192.168.0.0/16;
    deny all;
    proxy_connect_timeout 10s;
    proxy_pass {host}:{PORT};
  }}
}}
"""


def _listening(address: str) -> bool:
    try:
        with socket.create_connection((address, PORT), timeout=3):
            return True
    except OSError:
        return False


def status(host: str, log: Log, paths: RuntimePaths | None = None) -> dict[str, str]:
    """``ready`` when the relay runs with the configuration for this server; otherwise ``missing``."""
    if not host:
        return {"state": "missing", "detail": "This server's private address is not known yet."}
    address = host_address(log)
    if not address:
        return {"state": "missing", "detail": "Docker's host address is not known yet."}
    try:
        current = config_path(paths).read_text(encoding="utf-8")
    except OSError:
        current = ""
    if current != render(host, address):
        return {"state": "missing", "detail": "Apps cannot reach this server's private address yet."}
    if not _listening(address):
        return {"state": "missing", "detail": "The relay for apps' sign-in is not running."}
    return {"state": "ready", "detail": "Apps can reach this server's private address."}


def apply(host: str, log: Log, paths: RuntimePaths | None = None) -> dict:
    address = host_address(log)
    if not host or not address:
        return {"ok": False, "error": "This server's private address or Docker's host address is not known yet."}
    target = config_path(paths)
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    target.write_text(render(host, address), encoding="utf-8")
    target.chmod(0o644)  # read by nginx inside the container; holds no secrets
    rc, out = actions.compose_up(SOURCE, log, timeout=300, env={"MU3LAB_RELAY_CONFIG": str(target)}, recreate=True)
    log(out or f"(exit {rc})")
    if rc != 0:
        return {"ok": False, "error": "The relay for apps' sign-in could not start (see log)."}
    for _ in range(15):
        if _listening(address):
            return {"ok": True}
        time.sleep(1)
    return {"ok": False, "error": f"The relay started but nothing answers on {address}:{PORT}."}
