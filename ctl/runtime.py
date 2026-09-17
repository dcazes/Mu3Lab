"""Mu3Lab :: ctl/runtime.py

WHAT: Defines the persistent runtime layout outside the source checkout.
WHY: App data, backup state, and secrets must never be mixed with Git-managed
     source files. This module only computes paths; creation is an explicit
     privileged bootstrap action.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_RUNTIME_ROOT = Path("/srv/mu3lab")


@dataclass(frozen=True)
class RuntimePaths:
    """Canonical locations for persistent Mu3Lab state; never creates them."""

    root: Path = DEFAULT_RUNTIME_ROOT

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    def as_dict(self) -> dict[str, str]:
        """Return UI-safe path labels, never filesystem metadata or secrets."""
        return {"root": str(self.root), "data": str(self.data),
                "backups": str(self.backups), "runtime": str(self.runtime),
                "projects": str(self.projects)}
