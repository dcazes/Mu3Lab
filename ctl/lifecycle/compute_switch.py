"""Move an app between its CPU and GPU builds of the same release, only when the owner asks.

An app that can use a graphics card is installed with the build that fits the
computer's compute mode (``ctl.compute``). Changing that mode later moves
nothing by itself: the app's page offers "Switch to GPU version" (or CPU), and
this job downloads the other build, records it and restarts the app. Both
builds share the same data, so no backup is needed; if the other build does
not start, the build from before is put back and started again. Settings that
belong to one build (``build_env``) are rendered again for the build recorded.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ctl.compute import resolved_mode
from ctl.lifecycle import app_control, app_releases
from ctl.registry import Service
from ctl.runtime import RuntimePaths

Log = Callable[[str], None]
Step = Callable[[str, str], None]

LABELS = {"cpu": "CPU", "nvidia": "NVIDIA GPU", "amd": "AMD GPU"}


class SwitchFailed(RuntimeError):
    def __init__(self, message: str, code: str = "compute_switch_failed") -> None:
        super().__init__(message)
        self.code = code


def _render(service: Service, root: Path) -> None:
    """Settings and templates only: a switch must not slip in newer deployment files (R12)."""
    from ctl.engine.runtime import render_service

    render_service(service, root, copy_files=False)


def wanted(service: Service, root: Path) -> str:
    """The build the current compute mode asks for: a GPU build only where the app ships one."""
    if not service.uses_gpu:
        return "cpu"
    mode = resolved_mode()
    return mode if mode != "cpu" and (service.compose_path(root) / f"docker-compose.{mode}.yml").is_file() else "cpu"


def info(service: Service, root: Path, paths: RuntimePaths | None = None) -> dict[str, str] | None:
    """Which build runs and which the compute mode asks for, for apps that have a GPU build."""
    if not service.uses_gpu:
        return None
    current = app_releases.installed(service, root, paths)
    if not current:
        return None
    return {"running": current.variant, "wanted": wanted(service, root)}


def switch(
    service: Service, root: Path, log: Log, step: Step, *, running: bool, paths: RuntimePaths | None = None
) -> str:
    paths = paths or RuntimePaths()
    previous = app_releases.installed(service, root, paths)
    if not previous:
        raise SwitchFailed(f"{service.name} is not installed.", "not_installed")
    variant = wanted(service, root)
    if variant == previous.variant:
        raise SwitchFailed(
            f"{service.name} already runs its {LABELS.get(variant, variant)} version.", "already_current"
        )
    target = app_releases.approved(service, root, variant)
    if target.version != previous.version:
        raise SwitchFailed(
            f"{service.name} {previous.version} has no {LABELS.get(variant, variant)} version here; "
            f"update it to {target.version} first, then switch.",
            "update_first",
        )
    step("download_images", f"Downloading {service.name}'s {LABELS.get(variant, variant)} version.")
    try:
        app_releases.download(target.images, log)
    except RuntimeError as exc:
        raise SwitchFailed(f"Nothing changed: {exc}", "image_pull_failed") from exc
    app_releases.write(service.id, target, paths)
    _render(service, root)
    if not running:
        return f"{service.name} will run its {LABELS.get(variant, variant)} version when you start it."
    step("apply_update", f"Restarting {service.name} on its {LABELS.get(variant, variant)} version.")
    started, detail = app_control.start(service, root, log)
    if started:
        return f"{service.name} now runs its {LABELS.get(variant, variant)} version."
    app_releases.write(service.id, previous, paths)
    _render(service, root)
    back, _detail = app_control.start(service, root, log)
    raise SwitchFailed(
        f"The {LABELS.get(variant, variant)} version did not start ({detail[-200:]}), so Mu3Lab put back the "
        f"{LABELS.get(previous.variant, previous.variant)} version"
        + (" and started it." if back else "; start it again."),
        "compute_switch_rolled_back",
    )
