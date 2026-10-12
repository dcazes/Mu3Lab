"""One host-wide accelerator choice for services that opt into compute."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from ctl import process
from ctl.control_state import ControlState


def detect() -> str:
    try:
        if process.completed(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=4).returncode == 0:
            return "nvidia"
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        pci = process.completed(["lspci"], capture_output=True, text=True, timeout=4).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        pci = ""
    return "amd" if "amd" in pci or "advanced micro devices" in pci else "cpu"


def resolved_mode() -> str:
    state = ControlState.runtime()
    selected = str((state.system_config() if state else {}).get("compute_mode", "auto"))
    return detect() if selected == "auto" else selected


def recorded_mode(project: Path) -> str:
    """The build (cpu, nvidia, amd) an installed app's release record names; empty when unknown."""
    try:
        document = yaml.safe_load((project / "docker-compose.digest.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ""
    return str((document.get("x-mu3lab-release") or {}).get("variant") or "")


def app_mode(project: Path, uses_gpu: bool) -> str:
    """An installed app keeps the build it was installed with until the owner switches it
    (ctl.lifecycle.compute_switch); only a new install follows the compute mode."""
    if not uses_gpu:
        return "cpu"
    return recorded_mode(project) or resolved_mode()


def compose_overrides(service_id: str, project: Path) -> list[Path]:
    """Return only checked-in/runtime-materialized overrides, never browser paths."""
    mode = recorded_mode(project) or resolved_mode()
    candidate = project / f"docker-compose.{mode}.yml"
    return [candidate] if mode != "cpu" and candidate.is_file() else []
