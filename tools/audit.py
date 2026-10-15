"""Dependency and secret audits that need the network (review R30).

- npm advisories for the dashboard and every connector with a package lock,
  run in the same pinned Node container as the dashboard build;
- committed secrets anywhere in git history, with the pinned gitleaks image.
  Reviewed placeholders are listed in ``.gitleaksignore``.

Python packages are audited by ``make verify-security`` with pip-audit.
Exit status is non-zero when any audit finds something.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ctl.dashboard_build import NODE_IMAGE

ROOT = Path(__file__).resolve().parents[1]
GITLEAKS_IMAGE = (
    "ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
)


def _npm_audit(folder: Path, *, include_dev: bool) -> int:
    script = "npm audit --package-lock-only --audit-level=moderate" + ("" if include_dev else " --omit=dev")
    print(f"== npm audit: {folder.relative_to(ROOT)}", flush=True)
    return subprocess.run(
        [
            "docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp", "-e", "npm_config_cache=/tmp/npm-cache", "-e", "npm_config_update_notifier=false",
            "-v", f"{folder}:/src:ro", "-w", "/src", NODE_IMAGE, "sh", "-c", script,
        ],
        check=False,
    ).returncode  # fmt: skip


def _gitleaks() -> int:
    print("== gitleaks: git history", flush=True)
    return subprocess.run(
        ["docker", "run", "--rm", "-v", f"{ROOT}:/repo:ro", "-w", "/repo", GITLEAKS_IMAGE,
         "git", "--no-banner", "--redact", "/repo"],
        check=False,
    ).returncode  # fmt: skip


def main() -> int:
    failed = []
    # The dashboard ships its development tooling's output, so its dev dependencies count too.
    if _npm_audit(ROOT / "dashboard", include_dev=True):
        failed.append("dashboard npm audit")
    for lock in sorted(ROOT.glob("apps/*/connectors/*/package-lock.json")):
        if _npm_audit(lock.parent, include_dev=False):
            failed.append(f"{lock.parent.name} npm audit")
    if _gitleaks():
        failed.append("gitleaks")
    if failed:
        print("Failed: " + ", ".join(failed))
        return 1
    print("No advisories or committed secrets found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
