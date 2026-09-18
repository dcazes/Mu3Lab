"""Mu3Lab :: ctl/elevate.py

WHAT: ONE elevation for a whole install. Spawns a single privileged worker
      (one pkexec dialog) that executes many commands on demand over private
      FIFOs, plus a combined-shell-script builder for headless sessions where
      no dialog exists at all.
WHY:  Fresh `pkexec` per command means a password dialog per command (~10 in
      card ③). A persistent worker keeps per-step logging/verify/resume
      (which a literal one-liner would destroy) while prompting exactly once.
      The worker dies with the job — no lingering root process.
RUN:  Owned by ctl/privilege.py's session (ensure/release). Tests spawn the
      worker with plain python3 (no pkexec) to exercise the real protocol.
DEBUG: Every command AND its exit code are logged by the caller (see
      privilege.run_privileged). Worker dir lives under /tmp/mu3lab-elev-*
      (0700); `ls` leftovers there mean a crashed job didn't clean up.
      Protocol: JSON lines {id, argv} -> {id, rc, output}, NUL-free, shell
      never involved on either side.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading

# The worker program (runs AS ROOT under pkexec, or as self in tests).
# Reads requests, runs them with shell=False, answers each. Exits on EOF
# (command fifo closed) or the literal {"cmd": "stop"} request.
#
# FIFO discipline (deadlock-free by construction): the runner opens BOTH
# ends O_RDWR, which never blocks on Linux and counts as reader+writer, so
# the server's blocking opens (cmd O_WRONLY, res O_RDONLY) always succeed
# regardless of startup order. Opening either end read/write-only first
# would deadlock both sides waiting for each other — do not "simplify" this.
RUNNER = "\n".join([
    "import json, os, subprocess, sys",
    "cmd_fd = os.open(sys.argv[1], os.O_RDWR)",
    "res_fd = os.open(sys.argv[2], os.O_RDWR)",
    "inp = os.fdopen(cmd_fd, 'r')",
    "out = os.fdopen(res_fd, 'w', buffering=1)",
    "for line in inp:",
    "    try:",
    "        req = json.loads(line)",
    "    except ValueError:",
    "        continue",
    "    if req.get('cmd') == 'stop':",
    "        break",
    "    try:",
    "        proc = subprocess.run(req['argv'], capture_output=True,",
    "                              text=True, timeout=req.get('timeout', 300))",
    "        answer = {'id': req['id'], 'rc': proc.returncode,",
    "                  'output': (proc.stdout + proc.stderr).strip()}",
    "    except Exception as exc:",
    "        answer = {'id': req.get('id'), 'rc': 126, 'output': str(exc)}",
    "    out.write(json.dumps(answer) + chr(10))",
])


class Worker:
    """A single elevated command runner. Not thread-safe: one job owns one."""

    def __init__(self, spawn: list[str] | None = None,
                 start_timeout: float = 60.0) -> None:
        # spawn: full argv to launch the runner. Production passes
        # ["pkexec", "python3", "-c", RUNNER, cmd_fifo, res_fifo] (built in
        # start()); tests pass plain ["python3", ...] (same protocol, no auth).
        self._spawn = spawn
        self._start_timeout = start_timeout
        self._proc: subprocess.Popen | None = None
        self._dir = ""
        self._cmd_fd = -1
        self._res_file = None
        self._next_id = 0
        self._lock = threading.Lock()

    def start(self, log=None) -> bool:
        """Launch the worker. ONE auth dialog happens here (pkexec form).

        Returns True iff the runner answered a handshake ping. False means
        the dialog was cancelled/failed → caller falls back to terminal mode.
        """
        self._dir = tempfile.mkdtemp(prefix="mu3lab-elev-")
        os.chmod(self._dir, 0o700)
        cmd_fifo = os.path.join(self._dir, "cmd")
        res_fifo = os.path.join(self._dir, "res")
        os.mkfifo(cmd_fifo, 0o600)
        os.mkfifo(res_fifo, 0o600)
        spawn = self._spawn or ["pkexec", "python3", "-c", RUNNER,
                                cmd_fifo, res_fifo]
        try:
            self._proc = subprocess.Popen(spawn, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
        except OSError:
            self.stop()
            return False
        # Open both ends read/write and non-blocking in the unprivileged
        # process.  This is intentional: opening the response FIFO read-only
        # can block forever while pkexec is waiting for the native password
        # dialog.  Holding a local write end also prevents an early EOF; the
        # bounded handshake below decides whether the elevated runner really
        # came up.
        import time as _time
        deadline = _time.monotonic() + self._start_timeout
        try:
            self._cmd_fd = os.open(cmd_fifo, os.O_RDWR | os.O_NONBLOCK)
            os.set_blocking(self._cmd_fd, True)
            res_fd = os.open(res_fifo, os.O_RDWR | os.O_NONBLOCK)
            os.set_blocking(res_fd, True)
            self._res_file = os.fdopen(res_fd, "r", buffering=1)
        except OSError:
            self.stop()
            return False
        # Ping proves the loop is alive (not just the process).
        answer = self._request(["true"], timeout=10,
                               response_timeout=max(0.1, deadline - _time.monotonic()))
        if answer is None or answer.get("rc") != 0:
            self.stop()
            return False
        if log:
            log("elevated worker ready (one system dialog covered this install)")
        return True

    def _request(self, argv: list[str], timeout: int = 300,
                 response_timeout: float | None = None) -> dict | None:
        """One round-trip. None on protocol/IO failure (caller treats as dead)."""
        import select as _select
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            try:
                os.write(self._cmd_fd,
                         (json.dumps({"id": rid, "argv": argv,
                                      "timeout": timeout}) + "\n").encode())
                # Bounded wait: a wedged runner must not hang the install.
                wait_for = timeout + 10 if response_timeout is None else response_timeout
                ready, _, _ = _select.select([self._res_file], [], [], wait_for)
                if not ready:
                    return None
                line = self._res_file.readline()  # type: ignore[union-attr]
            except OSError:
                return None
        if not line:
            return None
        try:
            answer = json.loads(line)
        except ValueError:
            return None
        return answer if answer.get("id") == rid else None

    def run(self, argv: list[str], timeout: int = 300) -> tuple[int, str]:
        """Execute argv as root. (rc, output); rc 124/126 are worker-level
        failures (prognosis: worker dead → caller respawns or falls back)."""
        answer = self._request(argv, timeout)
        if answer is None:
            return 126, "elevated worker stopped responding"
        return answer.get("rc", 126), answer.get("output", "")

    def alive(self) -> bool:
        """True if the process exists AND the pipes are open."""
        return (self._proc is not None and self._proc.poll() is None
                and self._cmd_fd != -1)

    def stop(self) -> None:
        """Terminate runner, close pipes, remove dir. Idempotent, never raises."""
        try:
            if self._cmd_fd != -1:
                try:
                    os.write(self._cmd_fd,
                             (json.dumps({"cmd": "stop"}) + "\n").encode())
                except OSError:
                    pass
                os.close(self._cmd_fd)
        except OSError:
            pass
        finally:
            self._cmd_fd = -1
        try:
            if self._res_file is not None:
                self._res_file.close()
        except OSError:
            pass
        finally:
            self._res_file = None
        try:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except OSError:
            pass
        finally:
            self._proc = None
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = ""


def build_combined_script(commands: list[str]) -> str:
    """Assemble recorded admin commands into ONE copy-paste script.

    Input: shell-quoted command lines WITHOUT any sudo prefix (as recorded
    from worker traffic). Output runs with `sudo bash script.sh` (or pasted
    into a root shell): `set -e` stops at the first failure, echo markers
    delimit steps for eyeballing progress. Headless-session fallback only.
    """
    lines = ["#!/usr/bin/env bash",
             "# Mu3Lab fallback: run with `sudo bash this-file.sh`.",
             "# Generated from the installer's recorded admin commands.",
             "set -e"]
    for cmd in commands:
        lines.append(f"echo '==> {cmd}'")
        lines.append(cmd)
    return "\n".join(lines) + "\n"
