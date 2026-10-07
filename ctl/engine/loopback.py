"""JSON requests to an app's own loopback port, for setup steps that use its API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class LoopbackError(ValueError):
    """An app's setup API refused or did not answer; the message never contains credentials."""


def request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    data: Any = None,
    token: str = "",
    json_reply: bool = True,
    timeout: float = 20,
) -> Any:
    """Send one request; return the parsed JSON reply, or None when ``json_reply`` is False (plain "OK")."""
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024)
            return json.loads(raw) if raw and json_reply else None
    except urllib.error.HTTPError as exc:
        raise LoopbackError(f"Setup request {path} failed (HTTP {exc.code}).") from None
    except (OSError, ValueError) as exc:
        raise LoopbackError(f"Setup request {path} did not complete.") from exc
