"""Mu3Lab :: ctl/runtime.py

WHAT: Defines the persistent runtime layout outside the source checkout.
WHY: App data, backup state, and secrets must never be mixed with Git-managed
     source files. This module only computes paths; creation is an explicit
     privileged bootstrap action.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PRODUCTION_RUNTIME_ROOT = Path("/srv/mu3lab")
DEFAULT_RUNTIME_ROOT = Path(os.environ.get("MU3LAB_RUNTIME_ROOT") or PRODUCTION_RUNTIME_ROOT)


@dataclass(frozen=True)
class RuntimePaths:
    """Canonical locations for persistent Mu3Lab state; never creates them."""

    root: Path = DEFAULT_RUNTIME_ROOT

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def media(self) -> Path:
        """Large libraries people add themselves (audiobooks, games); never deleted by uninstall."""
        return self.root / "media"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def runtime(self) -> Path:
        return self.state

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    def as_dict(self) -> dict[str, str]:
        """Return UI-safe path labels, never filesystem metadata or secrets."""
        return {
            "root": str(self.root),
            "data": str(self.data),
            "media": str(self.media),
            "backups": str(self.backups),
            "runtime": str(self.runtime),
            "projects": str(self.projects),
        }
