"""Which local models fit this computer, after the room the household's other apps need.

A model is judged against what is free now, less what installed apps would take back when they
run: each app's highest use seen so far (``remember``, from the worker's host measurements), or a
cautious default for one never measured. The model runner's own use does not count against it,
since a new model replaces the one it holds.

- **good**: fits in the GPU's memory, or (without one) is small enough to answer quickly on the
  processor;
- **slow**: fits in memory, but runs on the processor;
- **too_big**: does not fit beside the other apps.

The suggestion is the largest good model on the reviewed list (``local_models.CHAT``); with none,
Mu3Lab suggests outside providers instead.
"""

from __future__ import annotations

from typing import Any

import psutil

from ctl.runtime import RuntimePaths
from ctl.store import records

FOOTPRINTS = ("status", "app-footprints")
GIB = 1024**3
# An app never measured: memory it may take when started, and graphics memory if it uses the GPU.
DEFAULT_MEMORY = 1 * GIB
DEFAULT_GPU = 2 * GIB
# Left free for the system, the desktop and an app's bad day.
MEMORY_HEADROOM = 2 * GIB
GPU_HEADROOM = 1 * GIB
# On the processor alone, models needing up to this much memory answer at a comfortable speed.
CPU_COMFORTABLE_GB = 3.0
IGNORED = {"core", "removed", "unused", ""}


def remember(by_owner: dict[str, int], gpu_by_owner: dict[str, int], paths: RuntimePaths | None = None) -> None:
    """Keep each app's highest memory and graphics memory seen, for the next suggestion."""
    paths = paths or RuntimePaths()
    saved = records.get(*FOOTPRINTS, paths)
    changed = False
    for kind, values in (("memory", by_owner), ("gpu", gpu_by_owner)):
        for owner, used in values.items():
            if owner in IGNORED or not isinstance(used, int):
                continue
            entry = dict(saved.get(owner) or {})
            if used > int(entry.get(kind, 0)):
                entry[kind] = used
                saved[owner] = entry
                changed = True
    if changed:
        records.put(*FOOTPRINTS, saved, paths)


def _installed(paths: RuntimePaths) -> list:
    from ctl.control_state import ControlState
    from ctl.manifest.catalog import cached

    state = ControlState.runtime(paths)
    # Core apps are always installed, so a fresh install counts them before they exist.
    return [
        app
        for app in cached().apps
        if app.manifest.tier in {"core", "foundation"}
        or (state and (record := state.installation(app.id)) and record.get("state") not in {None, "not_installed"})
    ]


def measure() -> dict[str, Any]:
    """Memory and graphics memory per app now, as the worker's host observation has them."""
    from pathlib import Path

    from ctl import resource_usage
    from ctl.registry import load

    registry, root = load(), Path(__file__).resolve().parent.parent
    try:
        _names, by_owner, _parts = resource_usage.memory(registry, root)
    except OSError:
        by_owner = {}  # before Docker is installed nothing of Mu3Lab's runs yet
    return {"memory_by_owner": by_owner, "gpu": resource_usage.gpu_memory(registry, root)}


def budget(paths: RuntimePaths | None = None, measured: dict[str, Any] | None = None) -> dict[str, Any]:
    """Memory and graphics memory a local model may use here, in bytes (``gpu`` is None without one).

    ``measured`` is the worker's host observation (``status.host.observe_system``); measured now otherwise.
    """
    from ctl import local_models

    paths = paths or RuntimePaths()
    if measured is None:
        measured = measure()
    runner = local_models.runner(paths).id
    by_owner: dict[str, int] = measured.get("memory_by_owner") or {}
    gpu = measured.get("gpu") or None
    gpu_by_owner: dict[str, int] = (gpu or {}).get("by_owner") or {}
    peaks = records.get(*FOOTPRINTS, paths)
    reserve_memory = reserve_gpu = 0
    waiting: list[str] = []
    for app in _installed(paths):
        if app.id == runner:
            continue
        peak = peaks.get(app.id) or {}
        memory_peak = int(peak.get("memory") or DEFAULT_MEMORY)
        reserve_memory += max(0, memory_peak - int(by_owner.get(app.id, 0)))
        if app.manifest.service and app.manifest.service.uses_gpu:
            gpu_peak = int(peak.get("gpu") or DEFAULT_GPU)
            room = max(0, gpu_peak - int(gpu_by_owner.get(app.id, 0)))
            reserve_gpu += room
            if room:
                waiting.append(app.manifest.name)
    available = psutil.virtual_memory().available + int(by_owner.get(runner, 0))
    memory = max(0, available - reserve_memory - MEMORY_HEADROOM)
    graphics = None
    if gpu:
        free = int(gpu["total"]) - int(gpu["used"]) + int(gpu_by_owner.get(runner, 0))
        graphics = max(0, free - reserve_gpu - GPU_HEADROOM)
    return {
        "memory": memory,
        "gpu": graphics,
        "gpu_name": (gpu or {}).get("name", ""),
        "gpu_total": int((gpu or {}).get("total") or 0),
        "gpu_apps": waiting,
    }


def fit(model, room: dict[str, Any]) -> tuple[str, str]:
    """(good | slow | too_big, gpu | cpu | "") for one model."""
    needed = model.memory_gb * GIB
    if room["gpu"] is not None and needed <= room["gpu"]:
        return "good", "gpu"
    if needed > room["memory"]:
        return "too_big", ""
    if room["gpu"] is None and model.memory_gb <= CPU_COMFORTABLE_GB:
        return "good", "cpu"
    return "slow", "cpu"


def report(paths: RuntimePaths | None = None, measured: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every reviewed chat model's fit, the suggestion and a sentence explaining it."""
    from ctl import local_models

    room = budget(paths, measured)
    fits = {model.id: fit(model, room) for model in local_models.CHAT}
    good = [model for model in local_models.CHAT if fits[model.id][0] == "good"]
    suggested = max(good, key=lambda model: model.memory_gb) if good else None
    gb = lambda value: f"{value / GIB:.1f} GB"  # noqa: E731
    if suggested and fits[suggested.id][1] == "gpu":
        reason = (
            f"{suggested.name} fits in {room['gpu_name'] or 'the GPU'}'s memory with room left for your other apps"
            f" ({gb(room['gpu'])} free for AI)."
        )
    elif suggested:
        reason = f"{suggested.name} is small enough to answer at a comfortable speed on this computer's processor."
    else:
        reason = (
            "This computer doesn't have enough free memory for a local model beside your other apps, so outside "
            "AI providers are suggested."
        )
    if room["gpu_apps"]:
        reason += " Room is kept for " + ", ".join(room["gpu_apps"]) + " on the GPU."
    return {
        "suggested": suggested.id if suggested else "",
        "reason": reason,
        "memory_bytes": room["memory"],
        "gpu_bytes": room["gpu"],
        "gpu_name": room["gpu_name"],
        "fits": {model_id: {"fit": verdict, "where": where} for model_id, (verdict, where) in fits.items()},
    }
