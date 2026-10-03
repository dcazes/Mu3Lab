"""Facts about this computer that generated app settings follow."""

from __future__ import annotations

import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "UTC"


def _valid(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def timezone(etc: Path = Path("/etc"), run=subprocess.run) -> str:
    """The computer's IANA timezone (for example ``Europe/Paris``), else UTC."""
    candidates: list[str] = []
    try:
        candidates.append((etc / "timezone").read_text(encoding="utf-8").strip())
    except OSError:
        pass
    try:
        target = (etc / "localtime").resolve()
        if "zoneinfo" in target.parts:
            candidates.append("/".join(target.parts[target.parts.index("zoneinfo") + 1 :]))
    except OSError:
        pass
    try:
        proc = run(["timedatectl", "show", "-p", "Timezone", "--value"], capture_output=True, text=True, timeout=5)
        candidates.append(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return next((name for name in candidates if name and _valid(name)), DEFAULT_TIMEZONE)
