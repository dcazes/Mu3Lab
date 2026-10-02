"""Docker daemon network address pools.

Docker's built-in pool hands out about 31 networks (fifteen /16s in
172.17-172.31, then sixteen /20s in 192.168). Mu3Lab needs four shared
networks plus one to three per app and MCP connector, so a full catalog uses
them all up and the next install fails with "all predefined address pools have
been fully subnetted". The installer therefore gives Docker a larger pool.
Existing networks keep their addresses; only new ones draw from this pool.
"""

from __future__ import annotations

import json

DAEMON_JSON = "/etc/docker/daemon.json"
POOL_KEY = "default-address-pools"
POOL = {"base": "10.210.0.0/16", "size": 24}
MIN_NETWORKS = 128


def _capacity(pools: object) -> int:
    total = 0
    if not isinstance(pools, list):
        return 0
    for pool in pools:
        try:
            prefix = int(str(pool["base"]).rsplit("/", 1)[1])
            size = int(pool["size"])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if size >= prefix:
            total += 2 ** (size - prefix)
    return total


def _parse(text: str) -> dict:
    if not text.strip():
        return {}
    document = json.loads(text)
    if not isinstance(document, dict):
        raise ValueError("daemon.json is not a JSON object")
    return document


def pools_sufficient(text: str) -> bool:
    """True when daemon.json already allows enough networks (ours or the owner's own)."""
    try:
        return _capacity(_parse(text).get(POOL_KEY)) >= MIN_NETWORKS
    except ValueError:
        return False


def with_pools(text: str) -> str:
    """daemon.json text with the pool added and every other setting (e.g. the GPU runtime) kept."""
    document = _parse(text)
    document[POOL_KEY] = [dict(POOL)]
    return json.dumps(document, indent=4) + "\n"
