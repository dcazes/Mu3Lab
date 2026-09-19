"""Read-only upstream stable-release discovery for curated services."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SECONDS = 900
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def latest(repository: str, current_version: str = "") -> dict[str, Any]:
    """Return GitHub's latest stable release without accepting arbitrary URLs."""
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("invalid curated GitHub repository")
    cached = _CACHE.get(repository)
    now = time.monotonic()
    if cached and cached[0] > now:
        result = dict(cached[1])
    else:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/releases/latest",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "Mu3Lab-control-plane"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.load(response)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise RuntimeError("upstream release information is unavailable") from exc
        tag = str(payload.get("tag_name", ""))[:80]
        url = str(payload.get("html_url", ""))[:500]
        if not tag or not url.startswith(f"https://github.com/{repository}/releases/"):
            raise RuntimeError("upstream returned invalid release metadata")
        result = {"repository": repository, "latest_version": tag,
                  "release_url": url,
                  "published_at": str(payload.get("published_at", ""))[:40],
                  "release_name": str(payload.get("name") or tag)[:160],
                  "notes": str(payload.get("body") or "")[:2000]}
        _CACHE[repository] = (now + _TTL_SECONDS, result)
    normalized_current = current_version.removeprefix("v")
    normalized_latest = str(result["latest_version"]).removeprefix("v")
    return {**result, "current_version": current_version,
            "update_available": bool(normalized_current and normalized_current != normalized_latest),
            "checked_at": int(time.time())}
