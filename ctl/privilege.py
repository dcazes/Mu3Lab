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


def _exec(argv: list[str], timeout: int = 300) -> tuple[int, str]:
    """Run argv, return (rc, merged output). Never raises, never uses shell."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
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


def run_privileged(argv: list[str], log: Callable[[str], None],
                   _exec=_exec, _sudo_fresh: bool | None = None,
                   _agent: bool | None = None) -> dict:
    """Run a root-needing command, or return how to run it by hand.

    Returns {"ok": True, "rc", "output"} on success, {"ok": False, ...} on
    command failure, or {"ok": False, "need_terminal": True,
    "terminal_command": ...} when no elevation path exists. `log` receives
    the exact argv first (audit trail) then the outcome. Overrides exist
    for tests; live callers omit them (real probes run).
    """
    log("$ " + quote_terminal(argv))
    fresh = has_fresh_sudo(_exec) if _sudo_fresh is None else _sudo_fresh
    if fresh:
        rc, out = _exec(["sudo"] + argv)
        log(out or f"(exit {rc}, no output)")
        return {"ok": rc == 0, "rc": rc, "output": out}
    agent = has_polkit_agent() if _agent is None else _agent
    if agent:
        # pkexec pops the NATIVE system dialog (dimmed screen). The secret
        # goes to the system auth agent only — this process never sees it.
        rc, out = _exec(["pkexec"] + argv)
        log(out or f"(exit {rc}, no output)")
        return {"ok": rc == 0, "rc": rc, "output": out}
    return {"ok": False, "need_terminal": True,
            "terminal_command": quote_terminal(argv)}
