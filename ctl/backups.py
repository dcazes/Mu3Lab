"""Mu3Lab :: ctl/backups.py

WHAT: Local Restic backup policy and read-only readiness inspection.
WHY: Git is not a database or personal-data backup system. Backups remain
     encrypted, local, service-scoped, and independently restorable.
"""

from __future__ import annotations

import json
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
    """Report verified backup facts, never infer protection from a directory.

    Restic creates a ``config`` file only after repository initialization.
    Mu3Lab writes verification metadata only after a snapshot and ``restic
    check`` succeed.  Merely installing Restic or creating the parent folder
    therefore remains ``not_configured``.
    """
    repository = paths.backups
    engine_available = shutil.which("restic") is not None
    repository_present = (repository / "config").is_file()
    verification_path = paths.runtime / "backup-verification.json"
    verification: dict = {}
    try:
        loaded = json.loads(verification_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            verification = loaded
    except (OSError, ValueError):
        pass
    snapshot_ok = bool(verification.get("snapshot_id"))
    integrity_ok = bool(verification.get("integrity_checked_at"))
    if not engine_available or not repository_present:
        state = "not_configured"
    elif snapshot_ok and integrity_ok:
        state = "verified"
    else:
        state = "local_only"
    return {"engine": "restic", "available": engine_available,
            "repository_path": str(repository), "repository_present": repository_present,
            "retention": {"daily": retention.daily, "weekly": retention.weekly,
                          "monthly": retention.monthly},
            "snapshot_present": snapshot_ok, "integrity_verified": integrity_ok,
            "last_verified_at": str(verification.get("integrity_checked_at", "")),
            "off_device": False, "state": state}
