"""Read-only upstream stable-release discovery for curated services."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SECONDS = 900
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CANDIDATE = re.compile(r"(rc|alpha|beta|canary|nightly|preview|dev)", re.I)


def _fetch(url: str) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Mu3Lab-control-plane"}
    # Without a token GitHub allows 60 requests an hour, fewer than one check of every app.
    if token := os.environ.get("GITHUB_TOKEN", "").strip():
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _newest_final(payload: Any) -> dict[str, Any]:
    for release in payload if isinstance(payload, list) else []:
        if (
            isinstance(release, dict)
            and not release.get("draft")
            and not _CANDIDATE.search(str(release.get("tag_name", "")))
        ):
            return release
    raise RuntimeError("upstream has no final release")


def latest(repository: str, current_version: str = "") -> dict[str, Any]:
    """Return GitHub's latest stable release without accepting arbitrary URLs."""
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("invalid curated GitHub repository")
    cached = _CACHE.get(repository)
    now = time.monotonic()
    if cached and cached[0] > now:
        result = dict(cached[1])
    else:
        try:
            try:
                payload = _fetch(f"https://api.github.com/repos/{repository}/releases/latest")
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                # Some projects (LibreChat) mark every release a pre-release, so GitHub names none
                # "latest". Their newest release whose tag is not a candidate build stands in.
                payload = _newest_final(_fetch(f"https://api.github.com/repos/{repository}/releases?per_page=30"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("upstream release information is unavailable") from exc
        tag = str(payload.get("tag_name", ""))[:80]
        url = str(payload.get("html_url", ""))[:500]
        # GitHub answers with the repository's own spelling, which may differ in case from ours.
        if not tag or not url.lower().startswith(f"https://github.com/{repository}/releases/".lower()):
            raise RuntimeError("upstream returned invalid release metadata")
        result = {
            "repository": repository,
            "latest_version": tag,
            "release_url": url,
            "published_at": str(payload.get("published_at", ""))[:40],
            "release_name": str(payload.get("name") or tag)[:160],
            "notes": str(payload.get("body") or "")[:2000],
        }
        _CACHE[repository] = (now + _TTL_SECONDS, result)
    normalized_current = current_version.removeprefix("v")
    normalized_latest = str(result["latest_version"]).removeprefix("v")
    return {
        **result,
        "current_version": current_version,
        "update_available": bool(normalized_current and normalized_current != normalized_latest),
        "checked_at": int(time.time()),
    }
