"""Run one external command with a whole-operation deadline and bounded memory.

Every command Mu3Lab runs goes through ``run`` (or ``completed``, its
``subprocess.run``-shaped wrapper for short probes). The guarantees:

- One monotonic deadline covers start, output, and exit. A child that closes
  its output and then hangs is still stopped when the deadline passes.
- stdout and stderr are drained concurrently, so a child blocked on one full
  pipe cannot deadlock the caller.
- Kept output is a bounded tail (bytes, not lines). A single line has a
  length cap; lines dropped from the tail are counted, not silently lost.
- Lines forwarded to a log callback are capped per command, so a flooding
  command cannot write unbounded job events.
- The child runs in its own session. On timeout the whole process group gets
  SIGTERM, then SIGKILL, and is reaped; ``sg docker -c`` and similar wrappers
  cannot leave a grandchild holding the pipes open.
- If the log callback raises (a cancelled job's ``JobInterrupted``), the
  command is not killed: like ``job_guard`` promises, a command already
  running may finish. Output keeps draining until it exits or the deadline
  passes, then the callback's exception is re-raised.
- Streams are closed and children reaped in ``finally``, whatever is raised.

Killing a Docker CLI does not stop the container it started. Callers that
start long-lived containers name them so they can be found and stopped; see
``ctl.backups``.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

Status = Literal["ok", "failed", "timeout", "not_found", "error"]
Stream = Literal["stdout", "stderr"]

KEEP_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
FORWARD_LIMIT = 5000
FORWARD_LINE_CHARS = 2000
KILL_GRACE_SECONDS = 5.0
_QUEUE_LINES = 32
_POLL_SECONDS = 0.5


@dataclass(frozen=True)
class Result:
    status: Status
    returncode: int
    output: str
    stdout: str
    stderr: str
    # Lines that fell out of the kept tail, and lines not forwarded to the log.
    dropped: int = 0
    unlogged: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def outcome_unknown(self) -> bool:
        """The command was stopped before it reported; its effect may be partial."""
        return self.status == "timeout"


class _Tail:
    """The newest lines of a command's output, within a byte budget."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.size = 0
        self.dropped = 0
        self.lines: deque[tuple[Stream, str]] = deque()

    def add(self, stream: Stream, line: str) -> None:
        self.lines.append((stream, line))
        self.size += len(line) + 1
        while self.size > self.budget and len(self.lines) > 1:
            _old_stream, old = self.lines.popleft()
            self.size -= len(old) + 1
            self.dropped += 1

    def text(self, stream: Stream | None = None) -> str:
        return "\n".join(line for source, line in self.lines if stream is None or source == stream).strip()


def _read(pipe: IO[bytes], stream: Stream, inbox: queue.Queue, stop: threading.Event, max_line: int) -> None:
    """Forward complete lines; keep only the first ``max_line`` bytes of an overlong one."""

    def put(item: tuple[Stream, str] | None) -> bool:
        while True:
            try:
                inbox.put(item, timeout=_POLL_SECONDS)
                return True
            except queue.Full:
                if stop.is_set():
                    return False

    try:
        skipping = False
        while chunk := pipe.readline(max_line):
            complete = chunk.endswith(b"\n")
            if not skipping and not put((stream, chunk.decode("utf-8", "replace").rstrip("\r\n"))):
                return
            skipping = not complete
    except (OSError, ValueError):
        pass
    finally:
        put(None)


def _signal_group(proc: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(proc.pid, signum)
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(signum)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def stop(proc: subprocess.Popen, grace: float = KILL_GRACE_SECONDS) -> None:
    """SIGTERM the process group, then SIGKILL after ``grace``, and reap the child."""
    if proc.poll() is not None:
        return
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    _signal_group(proc, signal.SIGKILL)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def run(
    argv: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    input: str | bytes | None = None,
    feed: Callable[[IO[bytes]], None] | None = None,
    on_line: Callable[[str], None] | None = None,
    echo: Literal["all", "stderr"] = "all",
    keep_bytes: int = KEEP_BYTES,
    max_line: int = MAX_LINE_BYTES,
    forward_limit: int = FORWARD_LIMIT,
    clock: Callable[[], float] = time.monotonic,
) -> Result:
    """Run ``argv`` (never through a shell) and return its bounded outcome.

    ``env`` replaces the environment when given, as with ``subprocess``.
    ``input`` or ``feed`` (a writer given the child's stdin) supply standard
    input from a separate thread. ``on_line`` receives each output line, cut
    to ``FORWARD_LINE_CHARS``, on the calling thread.
    """
    argv = list(argv)
    deadline = clock() + timeout
    has_stdin = input is not None or feed is not None
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if has_stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            start_new_session=True,
        )
    except FileNotFoundError:
        message = f"{argv[0]}: command not found"
        return Result("not_found", 127, message, "", message)
    except OSError as exc:
        message = f"{argv[0]}: {exc}"
        return Result("error", 126, message, "", message)

    inbox: queue.Queue[tuple[Stream, str] | None] = queue.Queue(maxsize=_QUEUE_LINES)
    halt = threading.Event()
    assert proc.stdout is not None and proc.stderr is not None
    threads = [
        threading.Thread(target=_read, args=(proc.stdout, "stdout", inbox, halt, max_line), daemon=True),
        threading.Thread(target=_read, args=(proc.stderr, "stderr", inbox, halt, max_line), daemon=True),
    ]
    writer_failure: list[BaseException] = []
    if has_stdin:
        assert proc.stdin is not None
        stdin = proc.stdin
        data = input.encode() if isinstance(input, str) else input

        def write() -> None:
            try:
                if feed is not None:
                    feed(stdin)
                elif data:
                    stdin.write(data)
            except (BrokenPipeError, OSError, ValueError):
                pass
            except BaseException as exc:  # re-raised on the caller's thread once the child is done
                writer_failure.append(exc)
            finally:
                try:
                    stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass

        threads.append(threading.Thread(target=write, daemon=True))
    for thread in threads:
        thread.start()

    tail = _Tail(keep_bytes)
    interruption: BaseException | None = None
    forwarded = unlogged = 0
    timed_out = False

    def forward(line: str) -> None:
        nonlocal interruption
        if on_line is None or interruption is not None:
            return
        try:
            on_line(line)
        except BaseException as exc:  # finish draining first; never abandon a running command
            interruption = exc

    try:
        open_streams = 2
        while open_streams:
            remaining = deadline - clock()
            if remaining <= 0:
                timed_out = True
                break
            try:
                item = inbox.get(timeout=min(_POLL_SECONDS, remaining))
            except queue.Empty:
                continue
            if item is None:
                open_streams -= 1
                continue
            stream, line = item
            tail.add(stream, line)
            if echo == "all" or stream == "stderr":
                if forwarded < forward_limit:
                    forwarded += 1
                    forward(line[:FORWARD_LINE_CHARS])
                else:
                    unlogged += 1
        if not timed_out:
            try:
                proc.wait(timeout=max(0.0, deadline - clock()))
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        # Reached on timeout and on anything raised by this loop itself.
        stop(proc)
        halt.set()
        for thread in threads:
            thread.join(timeout=1)
        # Closing a pipe another thread is still reading would block on its
        # lock; a reader only stays alive if something outside the process
        # group inherited the pipe, and then it ends when that holder exits.
        for pipe, reader in ((proc.stdout, threads[0]), (proc.stderr, threads[1])):
            if not reader.is_alive():
                try:
                    pipe.close()
                except OSError:
                    pass
    if unlogged:
        forward(f"… {unlogged} more output lines were not logged.")
    if interruption is not None:
        raise interruption
    if writer_failure:
        raise writer_failure[0]
    stdout, stderr = tail.text("stdout"), tail.text("stderr")
    output = tail.text()
    if tail.dropped:
        output = f"[{tail.dropped} earlier output lines omitted]\n{output}"
    if timed_out:
        note = f"{argv[0]}: timed out after {int(timeout)}s"
        return Result("timeout", 124, f"{output}\n{note}".strip(), stdout, f"{stderr}\n{note}".strip(), tail.dropped)
    returncode = int(proc.returncode)
    return Result("ok" if returncode == 0 else "failed", returncode, output, stdout, stderr, tail.dropped, unlogged)


def completed(
    argv: Sequence[str],
    *,
    timeout: float,
    capture_output: bool = True,
    text: bool = True,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    input: str | bytes | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """``subprocess.run`` semantics for probes, with ``run``'s guarantees.

    Raises ``FileNotFoundError``/``OSError`` when the command cannot start and
    ``subprocess.TimeoutExpired`` at the deadline, as ``subprocess.run`` does.
    Output is always captured; ``capture_output`` exists for call-site parity.
    """
    del capture_output
    result = run(argv, timeout=timeout, env=env, cwd=cwd, input=input)
    if result.status == "not_found":
        raise FileNotFoundError(2, result.output, argv[0])
    if result.status == "error":
        raise OSError(result.output)
    if result.status == "timeout":
        raise subprocess.TimeoutExpired(list(argv), timeout, output=result.stdout, stderr=result.stderr)
    stdout: str | bytes = result.stdout + ("\n" if result.stdout else "")
    stderr: str | bytes = result.stderr + ("\n" if result.stderr else "")
    if not text:
        stdout, stderr = str(stdout).encode(), str(stderr).encode()
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, list(argv), stdout, stderr)
    return subprocess.CompletedProcess(list(argv), result.returncode, stdout, stderr)
