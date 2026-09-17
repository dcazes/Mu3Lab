"""Mu3Lab :: ctl/backups.py

WHAT: Local Restic backup policy and read-only readiness inspection.
WHY: Git is not a database or personal-data backup system. Backups remain
     encrypted, local, service-scoped, and independently restorable.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ctl.runtime import RuntimePaths


@dataclass(frozen=True)
class Retention:
    """The conservative default local snapshot policy chosen for Mu3Lab."""

    daily: int = 7
    weekly: int = 4
    monthly: int = 12

    def restic_args(self) -> list[str]:
        """Return explicit retention flags for a future authenticated backup job."""
        return ["--keep-daily", str(self.daily), "--keep-weekly", str(self.weekly),
                "--keep-monthly", str(self.monthly)]


def readiness(paths: RuntimePaths = RuntimePaths(), retention: Retention = Retention()) -> dict:
    """Report non-mutating local backup readiness; never reveals repository secrets."""
    repository = paths.backups
    return {"engine": "restic", "available": shutil.which("restic") is not None,
            "repository_path": str(repository), "repository_present": repository.is_dir(),
            "retention": {"daily": retention.daily, "weekly": retention.weekly,
                          "monthly": retention.monthly},
            "state": "ready" if repository.is_dir() and shutil.which("restic") else "not_configured"}
