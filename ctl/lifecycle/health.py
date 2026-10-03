"""Application health probes declared in each app manifest."""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request

from ctl.registry import Service


def wait_healthy(service: Service, timeout: int = 180) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last = "health check did not run"
    while time.monotonic() < deadline:
        try:
            if service.health["kind"] == "http":
                with urllib.request.urlopen(str(service.health["url"]), timeout=5) as response:
                    if 200 <= response.status < 400:
                        return True, f"HTTP {response.status}"
                    last = f"HTTP {response.status}"
            else:
                with socket.create_connection(("127.0.0.1", int(service.health["port"])), timeout=5):
                    return True, "TCP ready"
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(2)
    return False, last
