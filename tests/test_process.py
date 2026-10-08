"""R35: one bounded runner for every external command."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path

from ctl import process

PY = sys.executable


def python(code: str) -> list[str]:
    return [PY, "-c", code]


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        warnings.simplefilter("error", ResourceWarning)
        self.addCleanup(warnings.resetwarnings)

    def test_success_and_failure_keep_both_streams(self) -> None:
        result = process.run(python("import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"), timeout=10)
        self.assertEqual((result.status, result.returncode), ("failed", 3))
        self.assertEqual((result.stdout, result.stderr), ("out", "err"))
        self.assertIn("out", result.output)
        self.assertIn("err", result.output)
        self.assertTrue(process.run(python("pass"), timeout=10).ok)

    def test_missing_command_is_reported_not_raised(self) -> None:
        result = process.run(["/nonexistent/mu3lab-command"], timeout=5)
        self.assertEqual((result.status, result.returncode), ("not_found", 127))

    def test_child_that_closes_output_then_hangs_is_stopped_at_the_deadline(self) -> None:
        started = time.monotonic()
        result = process.run(python("import os, time; os.close(1); os.close(2); time.sleep(60)"), timeout=1)
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.returncode, 124)
        self.assertTrue(result.outcome_unknown)
        self.assertLess(time.monotonic() - started, 10)

    def test_timeout_stops_grandchildren_holding_the_pipes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "pid"
            code = (
                "import subprocess, sys, time\n"
                f"child = subprocess.Popen([{PY!r}, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                "time.sleep(60)\n"
            )
            started = time.monotonic()
            result = process.run(python(code), timeout=2)
            self.assertEqual(result.status, "timeout")
            self.assertLess(time.monotonic() - started, 12)
            grandchild = int(pid_file.read_text())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and _alive(grandchild):
                time.sleep(0.05)
            self.assertFalse(_alive(grandchild))

    def test_flood_is_kept_within_the_byte_budget_and_log_limit(self) -> None:
        lines: list[str] = []
        result = process.run(
            python("for i in range(20000): print('x' * 50, i)"),
            timeout=30,
            keep_bytes=10_000,
            forward_limit=100,
            on_line=lines.append,
        )
        self.assertTrue(result.ok)
        self.assertLessEqual(len(result.output), 11_000)
        self.assertGreater(result.dropped, 19_000)
        self.assertIn("19999", result.output.splitlines()[-1])
        self.assertEqual(len(lines), 101)
        self.assertIn("19900 more output lines were not logged", lines[-1])

    def test_a_full_stderr_pipe_cannot_deadlock_stdout(self) -> None:
        code = "import sys; sys.stderr.write('e' * 2_000_000 + '\\n'); sys.stderr.flush(); print('done')"
        result = process.run(python(code), timeout=20)
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "done")

    def test_overlong_line_is_cut_not_buffered(self) -> None:
        result = process.run(python("print('a' * 100000); print('next')"), timeout=10, max_line=1000)
        first, second = result.stdout.splitlines()
        self.assertEqual(len(first), 1000)
        self.assertEqual(second, "next")

    def test_raising_log_callback_lets_the_command_finish_then_reraises(self) -> None:
        class Stop(BaseException):
            pass

        seen: list[str] = []

        def log(line: str) -> None:
            seen.append(line)
            raise Stop

        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "finished"
            code = f"import time; print('one', flush=True); time.sleep(0.5); open({str(marker)!r}, 'w').write('x')"
            with self.assertRaises(Stop):
                process.run(python(code), timeout=10, on_line=log)
            self.assertTrue(marker.exists())
        self.assertEqual(seen, ["one"])

    def test_standard_input_and_feed(self) -> None:
        echo = python("import sys; print(sys.stdin.read().upper())")
        self.assertEqual(process.run(echo, timeout=10, input="secret").stdout, "SECRET")
        result = process.run(echo, timeout=10, feed=lambda stream: stream.write(b"fed"))
        self.assertEqual(result.stdout, "FED")

    def test_feed_failure_is_raised_after_the_child_exits(self) -> None:
        class Broken(RuntimeError):
            pass

        def feed(_stream) -> None:
            raise Broken("archive unreadable")

        with self.assertRaises(Broken):
            process.run(python("import sys; sys.stdin.read()"), timeout=10, feed=feed)

    def test_echo_stderr_only_forwards_diagnostics(self) -> None:
        lines: list[str] = []
        code = "import sys; print('{\"ok\": true}'); print('progress', file=sys.stderr)"
        result = process.run(python(code), timeout=10, on_line=lines.append, echo="stderr")
        self.assertEqual(lines, ["progress"])
        self.assertEqual(result.stdout, '{"ok": true}')

    def test_environment_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code = "import os; print(os.environ['MU3LAB_PROBE'], os.getcwd())"
            result = process.run(python(code), timeout=10, env={**os.environ, "MU3LAB_PROBE": "yes"}, cwd=directory)
            self.assertEqual(result.stdout, f"yes {os.path.realpath(directory)}")


class CompletedTests(unittest.TestCase):
    def test_matches_subprocess_run_shape(self) -> None:
        proc = process.completed(python("print('hi')"), timeout=10)
        self.assertEqual((proc.returncode, proc.stdout), (0, "hi\n"))
        self.assertEqual(process.completed(python("print('hi')"), timeout=10, text=False).stdout, b"hi\n")

    def test_raises_like_subprocess_run(self) -> None:
        with self.assertRaises(subprocess.TimeoutExpired):
            process.completed(python("import time; time.sleep(30)"), timeout=1)
        with self.assertRaises(FileNotFoundError):
            process.completed(["/nonexistent/mu3lab-command"], timeout=5)
        with self.assertRaises(subprocess.CalledProcessError):
            process.completed(python("raise SystemExit(2)"), timeout=10, check=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A reaped-elsewhere zombie still answers kill(0); check its state.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


if __name__ == "__main__":
    unittest.main()
