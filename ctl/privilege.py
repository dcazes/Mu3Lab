"""Mu3Lab :: ctl/privilege.py

WHAT: Runs host-mutating commands through the administrator session that
      ./install.sh opens, or honestly reports that the user must run them by
      hand. There is exactly one elevation path: a fresh sudo timestamp.
WHY:  ./install.sh asks for the password once, in the terminal the user
      already has open, and keeps the timestamp alive while the bootstrap page
      runs. Commands always use `sudo -n` so an expired session fails fast
      with a clear message instead of silently waiting for a password on a
      terminal nobody is watching. This module never accepts, holds, or
      forwards a secret.
RUN:  Imported by ctl/actions.py and ctl/install.py.
DEBUG: Every elevation attempt logs its full argv BEFORE running (audit).
      Fallback dicts carry `terminal_command`: paste it into a terminal, run
      it, then retry. `quote_terminal()` output is shell-safe (shlex.join).
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable

from ctl import job_guard, process

SESSION_EXPIRED = (
    "Mu3Lab no longer has administrator access. Go back to the terminal window, "
    "stop the installer with Ctrl+C, and run ./install.sh again."
)


def _exec(argv: list[str], timeout: int = 300, env: dict | None = None) -> tuple[int, str]:
    """Run argv, return (rc, merged output). Never raises, never uses shell.

    `env` merges over os.environ (used for DOCKER_CONFIG isolation); None
    inherits the environment unchanged.
    """
    job_guard.checkpoint()
    result = process.run(argv, timeout=timeout, env={**os.environ, **(env or {})})
    if result.status == "timeout":
        return 124, f"{argv[0]}: timed out"
    return result.returncode, result.output


def has_fresh_sudo(_exec=_exec) -> bool:
    """True when `sudo -n true` succeeds (timestamp alive, no prompt needed)."""
    return _exec(["sudo", "-n", "true"])[0] == 0


def quote_terminal(argv: list[str]) -> str:
    """Build the copy-paste fallback string: `sudo ` + shell-quoted argv."""
    return "sudo " + shlex.join(argv)


def run_privileged(
    argv: list[str],
    log: Callable[[str], None],
    _exec=_exec,
    _sudo_fresh: bool | None = None,
    timeout: int = 300,
) -> dict:
    """Run a root-needing command, or return how to run it by hand.

    Returns {"ok": True, "rc", "output"} on success, {"ok": False, ...} on
    command failure, or {"ok": False, "need_terminal": True,
    "terminal_command": ...} when no administrator session exists.
    """
    log("$ " + quote_terminal(argv))
    fresh = has_fresh_sudo(_exec) if _sudo_fresh is None else _sudo_fresh
    if not fresh:
        log(SESSION_EXPIRED)
        return {"ok": False, "need_terminal": True, "terminal_command": quote_terminal(argv), "error": SESSION_EXPIRED}
    rc, out = _exec(["sudo", "-n", *argv], timeout=timeout)
    log(out or f"(exit {rc}, no output)")
    return {"ok": rc == 0, "rc": rc, "output": out}
