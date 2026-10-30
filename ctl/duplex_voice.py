"""The voice assistant's full-duplex mode: one model that listens and speaks at once.

The household chooses how the voice assistant talks (Settings, Voice Assistant):

- **Separate steps** (``pipeline``, the default): Whisper hears, a chat model answers, Kokoro
  speaks, each through LiteLLM. App assistants and the spoken-yes check work here.
- **Full duplex** (``duplex``): MiniCPM-o 4.5, 4-bit (Q4_K_M), from llama.cpp-omni. It takes a
  system prompt. Its web search (SearXNG) stays off until duplex itself is reliable: the switch
  starts only the model.

The graphics card must never hold two models, so the switch is strict:

1. Switching on downloads and checks the model files first; until then nothing changes.
2. The setting is saved before anything stops. From then on every Compose start of an app that
   runs a GPU build is refused (``start_refused``, called from ``ctl.actions``), whoever asks:
   Start in Apps, an update, a backup, a restore or the installer.
3. Every app running a GPU build (Ollama, Speaches, Immich, SurfSense …) is stopped, and the
   card must then show no other process before the duplex model starts and loads.

Switching back stops the duplex model, checks the card is free, saves the setting, and starts
again the apps this mode stopped (an app the owner had stopped stays stopped).

The voice agent reads ``engine.json`` next to its app tools at the start of each call, so no
restart is needed: the next call uses the new mode.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl.runtime import RuntimePaths
from ctl.store import records

Log = Callable[[str], None]

SETTINGS = ("ai", "voice_engine")
ACTION = "voice_engine"
PIPELINE = "pipeline"
DUPLEX = "duplex"
ENGINES = (PIPELINE, DUPLEX)

MODEL_NAME = "MiniCPM-o 4.5 (4-bit)"
REPO = "openbmb/MiniCPM-o-4_5-gguf"
# One reviewed revision of the files; each is checked against its SHA-256 below.
REVISION = "db25077c33951fe163b42986fba0132e279872a2"
# (path in the repository, bytes, SHA-256). The language model is Q4_K_M; the speech and hearing
# parts come only in 16-bit and are small.
FILES: tuple[tuple[str, int, str], ...] = (
    ("MiniCPM-o-4_5-Q4_K_M.gguf", 5026714400, "1237a97ee081b8abebc47aa7dad565701e8f5f904cdc92f6723ac4281bbc0932"),
    (
        "audio/MiniCPM-o-4_5-audio-F16.gguf",
        660167904,
        "d5b188ac7feaf98e17175c3f9bd14bf269301bfd187439fdaa3e3a494fc32ef7",
    ),
    ("token2wav-gguf/encoder.gguf", 151339008, "7f8d265da594eaf5e1de2db8f5f1867dbcb0bb75ef5878fadf2952347116f4d0"),
    ("token2wav-gguf/flow_extra.gguf", 13663328, "c67611aa7d02500fe395a7798bf0bfdfb55c74d37ba93934ca74d82b4e63f78d"),
    (
        "token2wav-gguf/flow_matching.gguf",
        458250240,
        "eda6069f3edeb5dd3a87fbf2aedb2ddd1b46f3273926c4fcf09b24476a39cab8",
    ),
    ("token2wav-gguf/hifigan2.gguf", 83242816, "1b8b3bf5d8d3066aeee4324fdcdd41aefce170d0ee907645858de408d82835c2"),
    ("token2wav-gguf/prompt_cache.gguf", 211613152, "81fe6f541ebe0b67db06a1e395df928da47991dc9637c2cf47d6c59d5b979f2c"),
    (
        "tts/MiniCPM-o-4_5-projector-F16.gguf",
        14948640,
        "4b1b5b377358a5e594a304ff6ea5d52df606a9ba7d886c4299d232f0c67dd1fd",
    ),
    ("tts/MiniCPM-o-4_5-tts-F16.gguf", 1157244416, "c7be3748a863dd6966ae7eed42600b7f41ca67affb03729ff245247f0e5ea088"),
)
# The vision encoder: voice calls send no images, and its 1.1 GB on the card left too little room
# on a 12 GB card, so the server is built not to load it (duplex-text.patch). Earlier switches
# downloaded it; the next switch on deletes it.
RETIRED = ("vision",)
DOWNLOAD_BYTES = sum(size for _, size, _ in FILES)
DOWNLOAD_GB = round(DOWNLOAD_BYTES / 1e9, 1)
# Where the agent reaches the model and the search, on Mu3Lab's backend network.
OMNI_URL = "ws://minicpm-omni:9060/backend"
SEARCH_URL = "http://voice-search:8080"
ENGINE_FILE = "engine.json"
AGENT_CONTAINER = "mu3lab-livekit-agent-1"
# Building the server compiles CUDA code for several card generations: allow an hour.
BUILD_SECONDS = 3600
# The first call loads every part of the model onto the card.
LOAD_SECONDS = 300
# Stopped apps release the card within seconds; allow for a slow one.
FREE_SECONDS = 90
CHUNK = 1 << 20


class SwitchFailed(RuntimeError):
    """A step of the switch failed; the message is safe to show."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def record(paths: RuntimePaths | None = None) -> dict[str, Any]:
    stored = records.get(*SETTINGS, paths or RuntimePaths()) or {}
    engine = stored.get("engine")
    return {**stored, "engine": engine if engine in ENGINES else PIPELINE}


def engine(paths: RuntimePaths | None = None) -> str:
    """How the voice assistant talks now: ``pipeline`` or ``duplex``."""
    return record(paths)["engine"]


def duplex_on(paths: RuntimePaths | None = None) -> bool:
    return engine(paths) == DUPLEX


def voice_app():
    from ctl import voice_tools

    return voice_tools.app()


def project(paths: RuntimePaths | None = None) -> Path:
    return (paths or RuntimePaths()).projects / voice_app().id / "duplex"


def models_dir(paths: RuntimePaths | None = None) -> Path:
    return (paths or RuntimePaths()).data / voice_app().id / "duplex-models"


# --- The guard every Compose start passes ---------------------------------------


def uses_gpu_build(projdir: Path) -> bool:
    """Whether a project starts a GPU build: its compute mode is not cpu and it has that override."""
    from ctl import compute

    mode = compute.recorded_mode(projdir) or compute.resolved_mode()
    return mode != "cpu" and (projdir / f"docker-compose.{mode}.yml").is_file()


def start_refused(projdir: Path) -> str:
    """Why Compose must not start ``projdir`` now, or "" when it may.

    While the full-duplex model holds the graphics card, no app that runs a GPU build may start.
    """
    try:
        if not duplex_on():
            return ""
        if projdir.resolve() == project().resolve() or not uses_gpu_build(projdir):
            return ""
    except (OSError, ValueError, RuntimeError):
        return ""
    return (
        f"{projdir.name} uses the graphics card, which the voice assistant's full-duplex model holds. "
        "Switch the voice assistant back to separate steps in Settings, Voice Assistant first."
    )


# --- What the switch stops ---------------------------------------------------------


def gpu_apps(root: Path, paths: RuntimePaths | None = None) -> list:
    """Installed apps whose GPU build is in use, in registry order."""
    from ctl.control_state import ControlState
    from ctl.registry import load
    from ctl.service_ops import project_path

    state = ControlState.runtime()
    found = []
    for service in load().services:
        installation = state.installation(service.id) if state else None
        if not service.uses_gpu or not installation or installation["state"] == "not_installed":
            continue
        if uses_gpu_build(project_path(service, root)):
            found.append(service)
    return found


def gpu_processes() -> list[str]:
    """Every process using the graphics card, as "name (MiB)"; empty when none or no NVIDIA card."""
    from ctl import process

    try:
        result = process.completed(
            ["nvidia-smi", "--query-compute-apps=process_name,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError:
        return []
    if result.returncode:
        return []
    found = []
    for line in result.stdout.splitlines():
        name, _, used = line.rpartition(",")
        if name.strip():
            found.append(f"{Path(name.strip()).name} ({used.strip()} MiB)")
    return found


def wait_card_free(log: Log, timeout: float = FREE_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while True:
        busy = gpu_processes()
        if not busy:
            log("The graphics card is free.")
            return
        if time.monotonic() >= deadline:
            raise SwitchFailed(f"The graphics card is still in use by: {', '.join(busy)}.")
        time.sleep(2)


# --- The model files -----------------------------------------------------------------


def _verified(target: Path, size: int, digest: str) -> bool:
    marker = target.with_name(target.name + ".sha256")
    try:
        return target.stat().st_size == size and marker.read_text(encoding="utf-8").strip() == digest
    except OSError:
        return False


def downloaded(paths: RuntimePaths | None = None) -> bool:
    folder = models_dir(paths)
    return all(_verified(folder / name, size, digest) for name, size, digest in FILES)


def _sha256(path: Path) -> str:
    hashed = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK * 8):
            hashed.update(block)
    return hashed.hexdigest()


def download(log: Log, report: Callable[[int, int], None], paths: RuntimePaths | None = None) -> None:
    """Fetch every model file not yet checked, resuming partial files, and check each one's SHA-256."""
    from ctl import job_guard

    folder = models_dir(paths)
    for name in RETIRED:
        shutil.rmtree(folder / name, ignore_errors=True)
    done = sum(size for name, size, digest in FILES if _verified(folder / name, size, digest))
    if done == DOWNLOAD_BYTES:
        return
    log(f"Downloading {MODEL_NAME} ({DOWNLOAD_GB} GB).")
    for name, size, digest in FILES:
        target = folder / name
        if _verified(target, size, digest):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        have = partial.stat().st_size if partial.is_file() else 0
        if have > size:
            partial.unlink()
            have = 0
        if have < size:
            request = urllib.request.Request(
                f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}",
                headers={"Range": f"bytes={have}-", "User-Agent": "Mu3Lab"},
            )
            with urllib.request.urlopen(request, timeout=60) as reply, partial.open("ab") as out:
                if have and reply.status != 206:
                    # The server ignored the range: start this file again.
                    out.truncate(0)
                    have = 0
                while block := reply.read(CHUNK):
                    out.write(block)
                    have += len(block)
                    report(done + have, DOWNLOAD_BYTES)
                    job_guard.checkpoint()
        if partial.stat().st_size != size:
            raise SwitchFailed(f"{name} stopped downloading early. Try again to continue where it stopped.")
        log(f"Checking {name}.")
        if _sha256(partial) != digest:
            partial.unlink()
            raise SwitchFailed(f"{name} did not match its published checksum, so it was deleted. Try again.")
        partial.replace(target)
        target.with_name(target.name + ".sha256").write_text(digest + "\n", encoding="utf-8")
        done += size
        report(done, DOWNLOAD_BYTES)


# --- The duplex project ---------------------------------------------------------------


def _write_env(paths: RuntimePaths) -> None:
    """The project's Compose values: the model folder and SearXNG's own secret, kept between switches."""
    from ctl.secrets import read_runtime_env

    env_file = project(paths) / ".env"
    current = read_runtime_env(env_file) if env_file.is_file() else {}
    values = {
        "MU3LAB_DUPLEX_MODELS": str(models_dir(paths)),
        "SEARXNG_SECRET": current.get("SEARXNG_SECRET") or secrets.token_hex(32),
    }
    text = "".join(f"{key}={value}\n" for key, value in values.items())
    partial = env_file.with_name(".env.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)
    partial.replace(env_file)


def _compose(log: Log, *verb: str, timeout: int = 600, on_output: Log | None = None) -> None:
    from ctl import actions

    folder = project()
    argv = ["docker", "compose", "-f", str(folder / "docker-compose.yml"), "--project-directory", str(folder), *verb]
    if on_output:
        rc, output = actions.docker_cmd_stream(argv, log, timeout=timeout, on_output=on_output)
    else:
        rc, output = actions.docker_cmd(argv, log, timeout=timeout)
    if rc:
        raise SwitchFailed(f"docker compose {verb[0]} failed for the full-duplex model: {output[-400:]}")


def build(log: Log) -> None:
    """Build the model server image (once; Docker's cache makes later builds quick)."""
    log("Building the full-duplex model server. The first build compiles for your graphics card and can take a while.")
    lines = 0

    def progress(line: str) -> None:
        nonlocal lines
        lines += 1
        if lines % 200 == 0:
            log(f"Still building ({line.strip()[:120]}).")

    _compose(log, "build", "omni", timeout=BUILD_SECONDS, on_output=progress)


def stop_project(log: Log) -> None:
    # SearXNG too: earlier switches started it.
    if (project() / "docker-compose.yml").is_file():
        _compose(log, "stop", "omni", "searxng", timeout=180)


def warm(log: Log) -> None:
    """Load the model onto the card by opening and closing one call from the agent's container."""
    from ctl import actions

    log("Loading the full-duplex model onto the graphics card.")
    rc, output = actions.docker_cmd(
        ["docker", "exec", AGENT_CONTAINER, "/app/.venv/bin/python", "src/duplex.py", "warm"],
        log,
        timeout=LOAD_SECONDS,
    )
    if rc:
        raise SwitchFailed(f"The full-duplex model did not load: {output[-400:]}")


def write_agent_mode(engine_name: str, paths: RuntimePaths | None = None) -> None:
    """Tell the voice agent which mode its next call uses (read at each call's start)."""
    from ctl import voice_tools

    target = voice_tools.folder(paths)
    target.mkdir(mode=0o755, exist_ok=True)
    data = {"engine": engine_name, "omni_url": OMNI_URL, "search_url": SEARCH_URL}
    partial = target / f".{ENGINE_FILE}.partial"
    partial.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.chmod(partial, 0o644)
    partial.replace(target / ENGINE_FILE)


# --- What Settings shows -----------------------------------------------------------------


def unavailable_reason(root: Path, paths: RuntimePaths | None = None) -> str:
    from ctl import chat_ops, compute

    if compute.resolved_mode() != "nvidia":
        return "The full-duplex model needs an NVIDIA graphics card."
    if not chat_ops.installed(voice_app().id):
        return "Install LiveKit from Apps first."
    if not (root / "apps" / voice_app().id / "duplex" / "docker-compose.yml").is_file():
        return "This Mu3Lab version has no full-duplex model files; update Mu3Lab first."
    return ""


def view(root: Path, paths: RuntimePaths | None = None) -> dict[str, Any]:
    paths = paths or RuntimePaths()
    current = record(paths)
    try:
        from ctl.control_state import ControlState
        from ctl.lifecycle.app_control import was_running

        state = ControlState.runtime()
        stops = [service.name for service in gpu_apps(root, paths) if was_running(state, service)]
    except (OSError, ValueError, RuntimeError):
        stops = []
    return {
        "engine": current["engine"],
        "model": MODEL_NAME,
        "download_gb": DOWNLOAD_GB,
        "downloaded": downloaded(paths),
        "stops": stops,
        "stopped": [str(name) for name in current.get("stopped_names") or []],
        "unavailable_reason": unavailable_reason(root, paths),
        "changed_at": str(current.get("changed_at") or ""),
        "changed_by": str(current.get("changed_by") or ""),
    }


# --- The switch job ----------------------------------------------------------------------


def _save(engine_name: str, actor: str, stopped: list, paths: RuntimePaths) -> None:
    records.put(
        *SETTINGS,
        {
            "engine": engine_name,
            "stopped": [service.id for service in stopped],
            "stopped_names": [service.name for service in stopped],
            "changed_at": _now(),
            "changed_by": actor,
        },
        paths,
    )


def _stop_apps(services: list, stopped: list, job_id: str, root: Path, log: Log, remember: Callable[[], None]) -> None:
    """Stop each app, adding it to ``stopped`` and remembering that at once, so it starts again
    afterwards even when a later one fails or the worker stops."""
    from ctl.control_state import ControlState
    from ctl.lifecycle import app_control

    state = ControlState.runtime()
    for service in services:
        if not app_control.was_running(state, service):
            continue
        log(f"Stopping {service.name}, which uses the graphics card.")
        try:
            app_control.stop(service, root, log)
        except app_control.StopFailed as exc:
            raise SwitchFailed(str(exc)) from exc
        if state:
            state.set_installation(service.id, "stopped", job_id=job_id, route_state="ready")
        stopped.append(service)
        remember()


def _start_apps(ids: list[str], job_id: str, root: Path, log: Log) -> list[str]:
    """Start the apps this mode stopped; return the names of any that did not come back."""
    from ctl.control_state import ControlState
    from ctl.lifecycle import app_control
    from ctl.registry import load

    state = ControlState.runtime()
    by_id = {service.id: service for service in load().services}
    failed = []
    for app_id in ids:
        service = by_id.get(app_id)
        if service is None:
            continue
        log(f"Starting {service.name} again.")
        healthy, why = app_control.start(service, root, log)
        if not healthy:
            log(f"{service.name} did not start: {why[-300:]}")
            failed.append(service.name)
            continue
        if state:
            state.set_installation(service.id, "running", job_id=job_id, route_state="ready")
    return failed


def _switch_on(job_id: str, actor: str, root: Path, log: Log, report: Callable[[int, int], None]) -> str:
    paths = RuntimePaths()
    reason = unavailable_reason(root, paths)
    if reason:
        raise SwitchFailed(reason)
    # 1. Nothing changes until the files are here and the server is built.
    download(log, report, paths)
    folder = project(paths)
    if not (folder / "docker-compose.yml").is_file():
        raise SwitchFailed("The full-duplex model's project is missing; run ./install.sh to repair LiveKit.")
    _write_env(paths)
    build(log)
    # 2. From here every start of a GPU app is refused.
    services = gpu_apps(root, paths)
    _save(DUPLEX, actor, [], paths)
    stopped: list = []
    try:
        _stop_apps(services, stopped, job_id, root, log, lambda: _save(DUPLEX, actor, stopped, paths))
        # 3. The card must be empty before the model loads.
        wait_card_free(log)
        log("Starting the full-duplex model.")
        _compose(log, "up", "-d", "--wait", "--wait-timeout", "300", "omni", timeout=420)
        warm(log)
        write_agent_mode(DUPLEX, paths)
    except Exception as exc:
        log(f"Switching on failed; putting separate steps back: {exc}")
        try:
            stop_project(log)
        except SwitchFailed as undo:
            log(str(undo))
        write_agent_mode(PIPELINE, paths)
        _save(PIPELINE, actor, [], paths)
        failed = _start_apps([service.id for service in stopped], job_id, root, log)
        if failed:
            raise SwitchFailed(
                f"{exc} These apps did not start again: {', '.join(failed)}. Start them in Apps."
            ) from exc
        raise
    names = ", ".join(service.name for service in stopped)
    detail = f"The voice assistant now talks through {MODEL_NAME}, listening and speaking at once."
    if names:
        detail += f" Stopped while it is on: {names}."
    return detail


def _switch_off(job_id: str, actor: str, root: Path, log: Log) -> str:
    paths = RuntimePaths()
    current = record(paths)
    log("Stopping the full-duplex model.")
    stop_project(log)
    wait_card_free(log)
    _save(PIPELINE, actor, [], paths)
    write_agent_mode(PIPELINE, paths)
    failed = _start_apps([str(app_id) for app_id in current.get("stopped") or []], job_id, root, log)
    if failed:
        raise SwitchFailed(
            f"The voice assistant is back to separate steps, but these apps did not start again: {', '.join(failed)}. "
            "Start them in Apps."
        )
    return "The voice assistant talks in separate steps again: Whisper listens, the chat model answers, Kokoro speaks."


def execute(store, job: dict, worker_id: str, root: Path) -> None:
    """Switch how the voice assistant talks, holding every app the switch stops or starts."""
    from ctl import resource_locks
    from ctl.jobs import job_params, redact
    from ctl.local_models import SERVICE_ID, _pull_reporter

    job_id = str(job["id"])
    actor = str(job.get("actor") or worker_id)
    paths = RuntimePaths()

    def log(line: str) -> None:
        store.append_event(job_id, "log", line)

    wanted = job_params(job).get("engine")
    if wanted not in ENGINES:
        store.transition(
            job_id,
            "failed",
            actor=actor,
            detail="Choose how the voice assistant talks.",
            error_code="invalid_settings",
            step_id="validate",
        )
        return
    if wanted == engine(paths):
        store.transition(
            job_id,
            "failed",
            actor=actor,
            detail="The voice assistant already talks this way.",
            error_code="invalid_settings",
            step_id="validate",
        )
        return
    report, finish = _pull_reporter(SERVICE_ID, job_id)
    keys = [f"app:{service.id}" for service in gpu_apps(root, paths)] + [f"app:{voice_app().id}"]
    keys += [f"app:{app_id}" for app_id in record(paths).get("stopped") or []]
    try:
        with resource_locks.hold(*sorted(set(keys)), timeout=600, paths=paths):
            if wanted == DUPLEX:
                detail = _switch_on(job_id, actor, root, log, report)
            else:
                detail = _switch_off(job_id, actor, root, log)
        finish()
    except resource_locks.Busy:
        store.transition(
            job_id,
            "failed",
            actor=actor,
            detail="Nothing was changed: another change to these apps is still running. Try again shortly.",
            error_code="busy",
            step_id="validate",
        )
        return
    except (SwitchFailed, OSError, ValueError, RuntimeError) as exc:
        store.transition(
            job_id, "failed", actor=actor, detail=redact(str(exc)), error_code="switch_failed", step_id="switch"
        )
        return
    store.record_audit(actor=actor, event="ai.voice_engine_changed", detail=detail, job_id=job_id)
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
