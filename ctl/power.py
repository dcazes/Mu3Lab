"""Turn the whole of Mu3Lab off, and back on.

Off stops every Mu3Lab container, then the dashboard and its worker, and starts the wake page
(``ctl/power_wake.py``): a tiny page on Caddy's addresses whose one button turns Mu3Lab on again.
Docker keeps a stopped container stopped across reboots, and the systemd units are enabled and
disabled to match, so off stays off after a restart of the computer and on comes back on.

On starts exactly the containers off stopped, in the opposite order: sign-in and the other core
apps first (waiting for their health checks), then everything else, then the dashboard and
worker, and Caddy last, once the wake page has given its addresses back.

Which apps the household runs (``ControlState``) is never touched: off pauses Mu3Lab as a whole,
it does not stop each app, so on brings back exactly what was running.

    python -m ctl.power off      turn off (refused while a task runs), following its progress
    python -m ctl.power on       turn on, following its progress
    python -m ctl.power status   print the power state as JSON

Each change runs as its own systemd unit, so it finishes even though it stops the dashboard
that asked for it, or the terminal that started it closes.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctl import actions, process
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
RECORD = "power.json"
LOCK = "power.lock"
DASHBOARD_UNITS = ("mu3lab-ctl.service", "mu3lab-worker.service")
WORKER_UNIT = "mu3lab-worker.service"
WAKE_UNIT = "mu3lab-wake.service"
RUN_UNITS = {"off": "mu3lab-power-off", "on": "mu3lab-power-on"}
DASHBOARD_HEALTH = "http://127.0.0.1:8787/api/health"
DASHBOARD_CADDY_PORT = 19460  # the dashboard's Caddy listener, which the wake page answers on
PROJECT_LABEL = "com.docker.compose.project"
PREFIX = "mu3lab-"
CORE_WAIT = 300  # seconds sign-in and the core apps get to pass their health checks when turning on
STOP_TIMEOUT = 60  # seconds a container gets to shut down cleanly; Docker's own default is 10

# Stop order, lowest first; turning on runs it backwards. The dashboard stops between the core
# apps and sign-in, because its own page needs sign-in and Caddy to show the progress.
CONNECTORS, APPS, CORE, FOUNDATION, IDENTITY, PROXY = range(6)
STEPS = {
    CONNECTORS: "chat connectors and helpers",
    APPS: "your apps",
    CORE: "the AI core",
    FOUNDATION: "the password vault and core services",
    IDENTITY: "sign-in",
    PROXY: "the web server",
}
_TIERS = {"optional": APPS, "core": CORE, "foundation": FOUNDATION}

Log = Callable[[str], None]


class PowerError(RuntimeError):
    """A change could not start; the message is safe to show."""


# ---------------------------------------------------------------- the record


def _state_dir(paths: RuntimePaths | None = None) -> Path:
    return (paths or RuntimePaths()).state


def read(paths: RuntimePaths | None = None) -> dict[str, Any] | None:
    """The record of a turn-off still in effect, or None while Mu3Lab is on."""
    try:
        value = json.loads((_state_dir(paths) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write(record: dict[str, Any], paths: RuntimePaths | None = None) -> None:
    from ctl.secret_file import write_atomic

    write_atomic(_state_dir(paths) / RECORD, json.dumps(record, indent=2).encode())


def public(record: dict[str, Any] | None) -> dict[str, Any]:
    """What the dashboard and the wake page show: never container names or Docker output."""
    if record is None:
        return {"state": "on"}
    keys = ("state", "step", "done", "total", "since", "error")
    return {key: record[key] for key in keys if record.get(key) not in (None, "")}


@contextmanager
def _locked(paths: RuntimePaths | None = None) -> Iterator[None]:
    """One change at a time: a turn-on never races a turn-off."""
    path = _state_dir(paths) / LOCK
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PowerError("Mu3Lab is already turning on or off.") from exc
        yield


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- what runs


def _docker_lines(argv: list[str]) -> list[str]:
    command = actions.docker_argv(argv)
    result = process.completed(command, timeout=60, env={**os.environ, **actions._docker_config_env()})
    if result.returncode:
        raise PowerError(f"Docker did not answer: {str(result.stderr).strip()[-200:]}")
    return [line for line in str(result.stdout).splitlines() if line.strip()]


def _rank(project: str, services: dict[str, Any]) -> int:
    service = services.get(project)
    if service is None:  # a chat connector or a platform helper: nothing depends on it
        return CONNECTORS
    capabilities = set(service.manifest.capabilities)
    if "private_proxy" in capabilities:
        return PROXY
    if "identity_provider" in capabilities:
        return IDENTITY
    return _TIERS.get(service.manifest.tier, APPS)


def _levels(depends: dict[str, set[str]]) -> dict[str, int]:
    """How late each of a project's services stops: 0 for one no other service needs.

    A database that the app needs stops one level after the app, so it shuts down cleanly last.
    """
    levels: dict[str, int] = {}

    def level(name: str, seen: frozenset[str]) -> int:
        if name in levels:
            return levels[name]
        users = [other for other, needs in depends.items() if name in needs and other not in seen]
        levels[name] = max((level(other, seen | {name}) + 1 for other in users), default=0)
        return levels[name]

    for name in depends:
        level(name, frozenset())
    return levels


def running_containers() -> list[dict[str, Any]]:
    """Every running Mu3Lab container, in the order turning off stops them."""
    from ctl.registry import load

    services = {f"{PREFIX}{service.id}": service for service in load().services}
    lines = _docker_lines(
        [
            "docker",
            "ps",
            "--filter",
            f"label={PROJECT_LABEL}",
            "--format",
            '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}'
            '\t{{.Label "com.docker.compose.depends_on"}}',
        ]
    )
    projects: dict[str, dict[str, tuple[str, set[str]]]] = {}
    for line in lines:
        name, project, service, depends = [*line.split("\t"), "", "", ""][:4]
        if not project.startswith(PREFIX):
            continue
        needs = {item.split(":", 1)[0] for item in depends.split(",") if item.strip()}
        projects.setdefault(project, {})[service or name] = (name, needs)
    containers = []
    for project, members in projects.items():
        levels = _levels({service: needs for service, (_name, needs) in members.items()})
        for service, (name, _needs) in members.items():
            containers.append(
                {"name": name, "project": project, "rank": _rank(project, services), "level": levels[service]}
            )
    return sorted(containers, key=lambda item: (item["rank"], item["level"], item["project"], item["name"]))


def _batches(containers: list[dict[str, Any]]) -> list[tuple[int, list[str]]]:
    """Containers that may stop together, in order: one rank and one level at a time."""
    batches: dict[tuple[int, int], list[str]] = {}
    for item in containers:
        batches.setdefault((item["rank"], item["level"]), []).append(item["name"])
    return [(rank, names) for (rank, _level), names in sorted(batches.items())]


def _caddy_ports(paths: RuntimePaths | None = None) -> list[int]:
    """Every loopback port Caddy listens on, for the wake page to answer while Caddy is off."""
    from ctl.platform_apps import by_capability

    ports = {DASHBOARD_CADDY_PORT}
    try:
        caddyfile = (paths or RuntimePaths()).projects / by_capability("private_proxy").id / "Caddyfile"
        ports.update(int(port) for port in re.findall(r"(?m)^:(\d+)\s*\{", caddyfile.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return sorted(port for port in ports if 1024 < port < 65536)


def blockers() -> list[str]:
    """Why Mu3Lab cannot turn off right now; empty when it can."""
    from ctl.jobs import JobStore

    store = JobStore.runtime()
    jobs = store.active_jobs() if store else []
    if jobs:
        return [f"{len(jobs)} task{'s are' if len(jobs) != 1 else ' is'} still running. Wait for them to finish first."]
    return []


# ---------------------------------------------------------------- systemd


def _systemctl(*argv: str, log: Log) -> bool:
    log("$ systemctl --user " + " ".join(argv))
    try:
        result = subprocess.run(["systemctl", "--user", *argv], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(str(exc))
        return False
    if result.returncode:
        log((result.stdout + result.stderr).strip() or f"(exit {result.returncode})")
    return result.returncode == 0


def launch(change: str, paths: RuntimePaths | None = None) -> None:
    """Start turning off or on as its own systemd unit, which outlives the dashboard and the terminal."""
    if change not in RUN_UNITS:
        raise ValueError(change)
    argv = [
        "systemd-run",
        "--user",
        "--collect",
        f"--unit={RUN_UNITS[change]}",
        f"--working-directory={ROOT}",
        f"--setenv=MU3LAB_RUNTIME_ROOT={(paths or RuntimePaths()).root}",
        str(ROOT / ".venv" / "bin" / "python"),
        "-m",
        "ctl.power",
        f"run-{change}",
    ]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PowerError(f"Mu3Lab could not start turning {change}: {exc}") from exc
    if result.returncode:
        output = (result.stdout + result.stderr).strip()
        if "already" in output.lower() or "exists" in output.lower():
            raise PowerError(f"Mu3Lab is already turning {change}.")
        raise PowerError(f"Mu3Lab could not start turning {change}: {output[-200:]}")


# ---------------------------------------------------------------- turning off


def turn_off(log: Log, paths: RuntimePaths | None = None) -> None:
    """Stop every Mu3Lab container and the dashboard, then start the wake page."""
    with _locked(paths):
        if (current := read(paths)) and current.get("state") == "off":
            log("Mu3Lab is already off.")
            return
        problems = blockers()
        if problems:
            raise PowerError(problems[0])
        containers = running_containers()
        # A turn-off or turn-on that stopped part way already stopped some: keep them on the list.
        running = {item["name"] for item in containers}
        earlier = [item for item in (current or {}).get("containers", []) if item.get("name") not in running]
        containers = sorted(
            [*containers, *earlier], key=lambda item: (item["rank"], item["level"], item["project"], item["name"])
        )
        record: dict[str, Any] = {
            "state": "turning_off",
            "since": (current or {}).get("since") or _now(),
            "containers": containers,
            "ports": _caddy_ports(paths),
            "done": 0,
            "total": len(containers),
            "step": "Stopping the background worker",
        }
        # Written before anything stops: an interrupted turn-off still knows what to start again.
        _write(record, paths)
        log(record["step"])
        _systemctl("stop", WORKER_UNIT, log=log)
        dashboard_stopped = False
        for rank, names in _batches(containers):
            if rank >= IDENTITY and not dashboard_stopped:
                record["step"] = "Stopping the dashboard"
                _write(record, paths)
                log(record["step"])
                _systemctl("disable", "--now", *DASHBOARD_UNITS, log=log)
                dashboard_stopped = True
            record["step"] = f"Stopping {STEPS[rank]}"
            _write(record, paths)
            log(f"{record['step']}: {', '.join(names)}")
            rc, output = actions.docker_cmd(
                ["docker", "stop", "--timeout", str(STOP_TIMEOUT), *names], log, timeout=STOP_TIMEOUT + 120
            )
            if rc:
                log(f"Some containers did not stop: {output[-300:]}")
            record["done"] += len(names)
        if not dashboard_stopped:
            _systemctl("disable", "--now", *DASHBOARD_UNITS, log=log)
        record.update(state="off", step="", off_at=_now())
        _write(record, paths)
        if not (_systemctl("enable", WAKE_UNIT, log=log) and _systemctl("restart", WAKE_UNIT, log=log)):
            log("The wake page did not start. Turn Mu3Lab on from a terminal: ./start.sh")
        log("Mu3Lab is off.")


# ---------------------------------------------------------------- turning on


def _wait_until(check: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(2)
    return False


def _answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return 200 <= response.status < 400
    except OSError:
        return False


def _start(names: list[str], log: Log) -> None:
    if not names:
        return
    rc, output = actions.docker_cmd(["docker", "start", *names], log, timeout=300)
    if rc:
        log(f"Some containers did not start: {output[-300:]}")


def turn_on(log: Log, paths: RuntimePaths | None = None) -> None:
    """Start what turning off stopped, then hand the addresses back from the wake page to Caddy."""
    from ctl.lifecycle.health import wait_healthy
    from ctl.registry import load

    with _locked(paths):
        record = read(paths)
        if record is None:
            log("Mu3Lab is already on.")
            return
        containers: list[dict[str, Any]] = list(reversed(record.get("containers") or []))
        record.update(state="turning_on", done=0, total=len(containers), error="")
        services = {f"{PREFIX}{service.id}": service for service in load().services}

        def step(text: str) -> None:
            record["step"] = text
            _write(record, paths)
            log(text)

        def start(items: list[dict[str, Any]]) -> None:
            # Within a project a database starts before the app that needs it.
            for level in sorted({item["level"] for item in items}, reverse=True):
                _start([item["name"] for item in items if item["level"] == level], log)
            record["done"] += len(items)
            _write(record, paths)

        core = [item for item in containers if CORE <= item["rank"] < PROXY]
        step("Starting sign-in and the core apps")
        start(core)
        for project in dict.fromkeys(item["project"] for item in core):
            if (service := services.get(project)) is not None:
                healthy, detail = wait_healthy(service, timeout=CORE_WAIT)
                if not healthy:
                    log(f"{service.name} is not answering yet ({detail}); carrying on.")
        step("Starting your apps")
        start([item for item in containers if item["rank"] == APPS])
        start([item for item in containers if item["rank"] == CONNECTORS])
        step("Starting the dashboard")
        _systemctl("enable", *DASHBOARD_UNITS, log=log)
        _systemctl("start", *DASHBOARD_UNITS, log=log)
        if not _wait_until(lambda: _answers(DASHBOARD_HEALTH), 120):
            log("The dashboard is not answering yet; carrying on.")
        # The wake page gives Caddy's addresses back; the page in the browser expects a short gap.
        step("Opening the dashboard")
        time.sleep(3)
        _systemctl("disable", "--now", WAKE_UNIT, log=log)
        start([item for item in containers if item["rank"] == PROXY])
        if not _wait_until(lambda: _answers(f"http://127.0.0.1:{DASHBOARD_CADDY_PORT}/__mu3lab_caddy_health"), 60):
            log("Caddy is not answering yet.")
        (_state_dir(paths) / RECORD).unlink(missing_ok=True)
        log("Mu3Lab is on.")


# ---------------------------------------------------------------- the command line


def _follow(change: str, paths: RuntimePaths | None = None) -> int:
    """Print the progress of a change running in its own unit until it ends."""
    shown = ""
    unit = f"{RUN_UNITS[change]}.service"
    started = time.monotonic()
    while True:
        record = read(paths)
        state = (record or {}).get("state", "on")
        if change == "on" and record and record.get("error") and time.monotonic() - started > 3:
            print(f"Mu3Lab did not turn on: {record['error']}", file=sys.stderr)
            return 1
        if (change == "off" and state == "off") or (change == "on" and record is None):
            print(f"Mu3Lab is {change}.", flush=True)
            if change == "off":
                print("To turn it on: open the dashboard address and press Turn on, or run ./start.sh", flush=True)
            return 0
        line = (record or {}).get("step", "")
        if line and line != shown:
            done, total = (record or {}).get("done", 0), (record or {}).get("total", 0)
            print(f"{line}… ({done} of {total})" if total else f"{line}…", flush=True)
            shown = line
        active = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True)
        if active.returncode and time.monotonic() - started > 10:
            print(f"Turning {change} stopped early. Its log: journalctl --user -u {unit}", file=sys.stderr)
            return 1
        time.sleep(1)


def _record_error(command: str, message: str) -> None:
    """A failed turn-on keeps its record, so the wake page shows why and offers to try again."""
    if command == "run-on" and (record := read()) is not None:
        record.update(state="off", error=message, step="")
        _write(record)


def main(argv: list[str]) -> int:
    from ctl import logs

    command = argv[0] if argv else ""

    def log(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    try:
        if command == "status":
            print(json.dumps(public(read())))
            return 0
        if command in {"off", "on"}:
            current = read()
            if command == "on" and current is None:
                print("Mu3Lab is already on.")
                return 0
            if command == "off" and current and current.get("state") == "off":
                print("Mu3Lab is already off.")
                return 0
            if command == "off" and (problems := blockers()):
                print(problems[0], file=sys.stderr)
                return 1
            launch(command)
            return _follow(command)
        if command in {"run-off", "run-on"}:
            logs.configure()
            (turn_off if command == "run-off" else turn_on)(log)
            return 0
    except PowerError as exc:
        print(str(exc), file=sys.stderr)
        _record_error(command, str(exc))
        return 1
    except Exception as exc:  # the wake page must say so rather than show "Turning on…" forever
        log(f"Turning {command.removeprefix('run-')} failed: {exc!r}")
        _record_error(command, f"Something went wrong. Its log: journalctl --user -u {RUN_UNITS.get(command[4:], '')}")
        raise
    print("usage: python -m ctl.power off|on|status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
