"""Mu3Lab :: ctl/privilege.py

WHAT: Runs host-mutating commands with the right elevation, or honestly
      reports that the user must run them by hand. Three paths, in order:
      fresh sudo (silent) → pkexec (native system dialog) → terminal fallback
      (copy-paste command, nothing executed).
WHY:  The browser dashboard cannot summon a TTY sudo prompt, and this backend
      must NEVER accept, hold, or forward secrets: there is no secret
      parameter anywhere in this module — elevation reuses the OS timestamp
      (sudo) or the system auth agent (polkit). If neither exists, the exact
      shell command is returned for the user to run themselves.
RUN:  Imported by ctl/actions.py. No standalone server use.
DEBUG: Every elevation attempt logs its full argv BEFORE running (audit).
      Fallback dicts carry `terminal_command`: paste it into a terminal,
      run it, click Retry. `quote_terminal()` output is shell-safe
      (shlex.join) — never hand-built quoting.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable


def _exec(argv: list[str], timeout: int = 300,
          env: dict | None = None) -> tuple[int, str]:
    """Run argv, return (rc, merged output). Never raises, never uses shell.

    `env` merges over os.environ (used for DOCKER_CONFIG isolation); None
    inherits the environment unchanged.
    """
    import os as _os
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout,
                              env={**_os.environ, **(env or {})})
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except FileNotFoundError:
        return 127, f"{argv[0]}: command not found"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: timed out"
    except OSError as exc:
        return 126, f"{argv[0]}: {exc}"


def has_fresh_sudo(_exec=_exec) -> bool:
    """True when `sudo -n true` succeeds (timestamp alive, no prompt needed)."""
    return _exec(["sudo", "-n", "true"])[0] == 0


def has_polkit_agent(env: dict | None = None) -> bool:
    """Best-effort graphical-session detection (pkexec needs an auth agent).

    `env` injected for tests; live callers pass nothing (reads os.environ).
    """
    env = os.environ if env is None else env
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")
                or env.get("DBUS_SESSION_BUS_ADDRESS"))


def quote_terminal(argv: list[str]) -> str:
    """Build the copy-paste fallback string: `sudo ` + shell-quoted argv."""
    return "sudo " + shlex.join(argv)


# Module-owned elevated worker (ONE pkexec dialog per install, not per
# command). Created by ensure_elevation(), used by run_privileged(),
# destroyed by release_elevation(). Never holds secrets — it only ferries
# argv (see ctl/elevate.py).
_worker = None


def ensure_elevation(log: Callable[[str], None]) -> str:
    """Prepare ONE elevation for many commands. Returns "sudo" | "worker" |
    "terminal". Spawns the worker (single pkexec dialog) only when sudo is
    stale AND a polkit agent exists; otherwise reports the fallback path.
    Idempotent: safe to call per job start AND per step."""
    global _worker
    if has_fresh_sudo():
        return "sudo"
    if _worker is not None and _worker.alive():
        return "worker"
    if has_polkit_agent():
        from ctl import elevate
        worker = elevate.Worker()
        if worker.start(log):
            _worker = worker
            return "worker"
        _worker = None
        log("system dialog cancelled/failed; falling back to terminal commands")
    return "terminal"


def release_elevation() -> None:
    """Stop the worker if running. Always called at job end (finally).

    Tolerates foreign worker objects (tests) — a missing stop() is treated
    as already-stopped, never as an error worth crashing cleanup.
    """
    global _worker
    if _worker is not None:
        try:
            stop = getattr(_worker, "stop", None)
            if callable(stop):
                stop()
        except OSError:
            pass
        _worker = None


def run_privileged(argv: list[str], log: Callable[[str], None],
                   _exec=_exec, _sudo_fresh: bool | None = None,
                   _agent: bool | None = None, timeout: int = 300) -> dict:
    """Run a root-needing command, or return how to run it by hand.

    Returns {"ok": True, "rc", "output"} on success, {"ok": False, ...} on
    command failure, or {"ok": False, "need_terminal": True,
    "terminal_command": ...} when no elevation path exists. `log` receives
    the exact argv first (audit trail) then the outcome. Overrides exist
    for tests; live callers omit them (real probes run).
    """
    log("$ " + quote_terminal(argv))
    # One-dialog path: the session worker (spawned once by ensure_elevation)
    # runs every command without further prompts.
    if _worker is not None and _worker.alive():
        rc, out = _worker.run(argv, timeout=timeout)
        log(out or f"(exit {rc}, no output)")
        return {"ok": rc == 0, "rc": rc, "output": out}
    fresh = has_fresh_sudo(_exec) if _sudo_fresh is None else _sudo_fresh
    if fresh:
        rc, out = (_exec(["sudo"] + argv) if timeout == 300
                   else _exec(["sudo"] + argv, timeout=timeout))
        log(out or f"(exit {rc}, no output)")
        return {"ok": rc == 0, "rc": rc, "output": out}
    agent = has_polkit_agent() if _agent is None else _agent
    if agent:
        # Standalone single command (no session): one dialog for this call.
        # Install jobs avoid this path via ensure_elevation().
        rc, out = (_exec(["pkexec"] + argv) if timeout == 300
                   else _exec(["pkexec"] + argv, timeout=timeout))
        log(out or f"(exit {rc}, no output)")
        return {"ok": rc == 0, "rc": rc, "output": out}
    return {"ok": False, "need_terminal": True,
            "terminal_command": quote_terminal(argv)}
