"""Mu3Lab :: tests/test_elevate.py

WHAT: Tests for ctl/elevate.py spawning the worker with PLAIN python3 (same
      protocol, no pkexec, no privilege) plus the combined-script builder.
WHY:  The one-dialog promise rests on this protocol. A fake transport would
      test the mock, not the code — so tests run the real RUNNER unprivileged
      and assert ordering, failure propagation, and cleanup.
RUN:  `.venv/bin/python -m unittest tests.test_elevate -v`.
DEBUG: Worker dirs live under /tmp/mu3lab-elev-*; leftovers mean stop() broke
      (the lifecycle test asserts the dir is gone).
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl import elevate


def _local_worker() -> elevate.Worker:
    """Worker whose runner is plain python3 (protocol-identical, unprivileged)."""
    w = elevate.Worker()
    # Build the same spawn the production path uses, minus pkexec.
    orig_start = w.start
    def start(log=None):
        import subprocess as _sp, tempfile as _tf
        w._dir = _tf.mkdtemp(prefix="mu3lab-elev-")
        os.chmod(w._dir, 0o700)
        cmd_fifo = os.path.join(w._dir, "cmd")
        res_fifo = os.path.join(w._dir, "res")
        os.mkfifo(cmd_fifo, 0o600)
        os.mkfifo(res_fifo, 0o600)
        w._proc = _sp.Popen([sys.executable, "-c", elevate.RUNNER,
                             cmd_fifo, res_fifo],
                            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        w._cmd_fd = os.open(cmd_fifo, os.O_WRONLY)
        w._res_file = open(res_fifo, "r")
        answer = w._request(["true"], timeout=10)
        if answer is None or answer.get("rc") != 0:
            w.stop()
            return False
        return True
    w.start = start  # type: ignore[method-assign]
    return w


class WorkerTests(unittest.TestCase):
    def test_start_is_bounded_when_pkexec_never_starts_runner(self):
        # Simulate a pkexec process waiting for an auth dialog.  The parent
        # must not block forever opening the response FIFO in that case.
        import time
        worker = elevate.Worker(
            spawn=[sys.executable, "-c", "import time; time.sleep(5)"],
            start_timeout=0.2)
        started = time.monotonic()
        self.assertFalse(worker.start())
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertFalse(worker.alive())

    def test_roundtrip(self):
        worker = _local_worker()
        self.assertTrue(worker.start())
        try:
            self.assertTrue(worker.alive())
            rc, out = worker.run(["echo", "hello"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "hello")
        finally:
            worker.stop()

    def test_failure_propagates(self):
        worker = _local_worker()
        self.assertTrue(worker.start())
        try:
            rc, out = worker.run(["ls", "/nonexistent-dir-xyz"])
            self.assertNotEqual(rc, 0)
        finally:
            worker.stop()

    def test_ordering(self):
        worker = _local_worker()
        self.assertTrue(worker.start())
        try:
            outs = [worker.run(["echo", str(i)])[1] for i in range(5)]
            self.assertEqual(outs, ["0", "1", "2", "3", "4"])
        finally:
            worker.stop()

    def test_stop_cleans_dir(self):
        worker = _local_worker()
        self.assertTrue(worker.start())
        path = worker._dir
        self.assertTrue(os.path.isdir(path))
        worker.stop()
        self.assertFalse(os.path.exists(path))
        self.assertFalse(worker.alive())
        worker.stop()  # idempotent: must not raise


class ScriptTests(unittest.TestCase):
    def test_combined_script(self):
        script = elevate.build_combined_script(
            ["apt-get update", "apt-get install -y docker-ce"])
        self.assertIn("set -e", script.splitlines()[3])
        self.assertIn("apt-get install -y docker-ce", script)
        self.assertTrue(script.endswith("\n"))

    def test_no_secret_params(self):
        import inspect
        for name in ("Worker", "build_combined_script"):
            obj = getattr(elevate, name)
            target = obj.run if name == "Worker" else obj
            for param in inspect.signature(target).parameters:
                self.assertNotIn("password", param.lower())
                self.assertNotIn("secret", param.lower())


if __name__ == "__main__":
    unittest.main()
