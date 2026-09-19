"""One host-wide accelerator choice for services that opt into compute."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ctl.control_state import ControlState


def detect() -> str:
    try:
        if subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True,
                          timeout=4).returncode == 0:
            return "nvidia"
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        pci = subprocess.run(["lspci"], capture_output=True, text=True, timeout=4).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        pci = ""
    return "amd" if "amd" in pci or "advanced micro devices" in pci else "cpu"


def resolved_mode() -> str:
    state = ControlState.runtime()
    selected = str((state.system_config() if state else {}).get("compute_mode", "auto"))
    return detect() if selected == "auto" else selected


def compose_overrides(service_id: str, project: Path) -> list[Path]:
    """Return only checked-in/runtime-materialized overrides, never browser paths."""
    mode = resolved_mode()
    candidate = project / f"docker-compose.{mode}.yml"
    return [candidate] if mode != "cpu" and candidate.is_file() else []
