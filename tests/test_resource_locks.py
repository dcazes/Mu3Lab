"""R10: one executing worker, ordered resource locks, and no new work beside a leftover command."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import job_guard, process, resource_locks, worker
from ctl.job_processes import JobProcesses, start_ticks
from ctl.jobs import JobStore
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]


def _hold_in_child(paths: RuntimePaths, code: str) -> subprocess.Popen:
    """Start a separate process that takes a lock, prints 'held', then sleeps."""
    script = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "from ctl import resource_locks\n"
        "from ctl.runtime import RuntimePaths\n"
        f"paths = RuntimePaths(Path({str(paths.root)!r}))\n"
        f"{code}\n"
        "print('held', flush=True)\n"
        "time.sleep(30)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "held"
    child.stdout.close()
    return child


class _Paths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = RuntimePaths(Path(self.temp.name))


class ResourceLockTests(_Paths):
    def test_a_key_is_exclusive_between_threads(self):
        inside = threading.Event()
        release = threading.Event()

        def holder():
            with resource_locks.hold("app:mealie", paths=self.paths):
                inside.set()
                release.wait(5)

        thread = threading.Thread(target=holder)
        thread.start()
        inside.wait(5)
        with self.assertRaises(resource_locks.Busy), resource_locks.hold("app:mealie", timeout=0.3, paths=self.paths):
            pass
        release.set()
        thread.join()
        with resource_locks.hold("app:mealie", timeout=1, paths=self.paths):
            pass

    def test_a_key_is_exclusive_between_processes(self):
        child = _hold_in_child(
            self.paths, "lock = resource_locks.hold('backup-repository', paths=paths)\nlock.__enter__()"
        )
        try:
            with (
                self.assertRaises(resource_locks.Busy),
                resource_locks.hold("backup-repository", timeout=0.3, paths=self.paths),
            ):
                pass
        finally:
            child.kill()
            child.wait()
        with resource_locks.hold("backup-repository", timeout=2, paths=self.paths):
            pass

    def test_holding_a_key_again_is_free_and_release_is_exact(self):
        with resource_locks.hold("app:a", paths=self.paths):
            with resource_locks.hold("app:a", "app:b", paths=self.paths):
                self.assertEqual(resource_locks.held(), {"app:a", "app:b"})
            self.assertEqual(resource_locks.held(), {"app:a"})
        self.assertEqual(resource_locks.held(), frozenset())

    def test_overlapping_sets_taken_in_any_order_never_deadlock(self):
        errors: list[BaseException] = []

        def work(keys):
            try:
                for _ in range(30):
                    with resource_locks.hold(*keys, timeout=10, paths=self.paths):
                        time.sleep(0.001)
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=work, args=(("app:a", "app:b", "backup-repository"),)),
            threading.Thread(target=work, args=(("backup-repository", "app:b", "app:a"),)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])

    def test_keys_are_validated(self):
        with self.assertRaises(ValueError), resource_locks.hold("../etc/passwd", paths=self.paths):
            pass


class WorkerLockTests(_Paths):
    def test_a_paused_or_running_worker_keeps_others_from_executing(self):
        # SIGSTOP leaves the kernel lock held, exactly like a live worker.
        child = _hold_in_child(self.paths, "assert resource_locks.WorkerLock(paths).try_acquire()")
        try:
            os.kill(child.pid, 19)  # SIGSTOP
            self.assertFalse(resource_locks.WorkerLock(self.paths).try_acquire())
        finally:
            child.kill()
            child.wait()
        second = resource_locks.WorkerLock(self.paths)
        self.assertTrue(second.try_acquire())
        second.release()

    def test_jobs_lock_their_own_resource(self):
        self.assertEqual(worker.resources({"service_id": "mealie"}), ("app:mealie",))
        self.assertEqual(worker.resources({"service_id": "mcp:grocy"}), ("mcp:grocy",))
        self.assertEqual(worker.resources({"service_id": "mu3lab"}), ("platform",))

    def test_a_busy_resource_fails_the_job_without_running_it(self):
        store = JobStore(self.paths.state / "mu3lab.db")
        job = store.create(kind="lifecycle", service_id="mealie", action="backup", actor="owner")
        claimed = store.claim("w")
        with (
            patch("ctl.worker.resource_locks.hold", side_effect=resource_locks.Busy("app:mealie")),
            patch("ctl.worker._dispatch") as dispatch,
        ):
            worker.run_job(store, claimed, "w")
        dispatch.assert_not_called()
        self.assertEqual(store.get(job["id"])["error_code"], "resource_busy")


class LeftoverCommandTests(_Paths):
    def setUp(self):
        super().setUp()
        self.store = JobStore(self.paths.state / "mu3lab.db")
        self.job = self.store.create(kind="lifecycle", service_id="mealie", action="update", actor="owner")["id"]
        self.processes = JobProcesses(self.store.database)
        self.processes.install()
        self.addCleanup(process.set_tracker, None)

    def run_in_job(self, argv, timeout=30.0):
        claimed = self.store.claim("w")
        with job_guard.executing(self.store, claimed["id"], "w"):
            return process.run(argv, timeout=timeout)

    def rows(self):
        with self.store._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM job_processes")]

    def test_commands_a_job_runs_are_recorded_until_reaped(self):
        rows = self.rows
        code = f"import sqlite3; print(sqlite3.connect({str(self.store.database)!r}).execute('SELECT COUNT(*) FROM job_processes').fetchone()[0])"
        result = self.run_in_job([sys.executable, "-c", code])
        self.assertEqual(result.stdout, "1")
        self.assertEqual(rows(), [])
        # Outside a job nothing is recorded.
        process.run([sys.executable, "-c", "pass"], timeout=10)
        self.assertEqual(rows(), [])

    def leftover(self, deadline_offset: float) -> subprocess.Popen:
        """A command an earlier, now dead worker started and never saw finish."""
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        self.addCleanup(lambda: (child.kill(), child.wait()))
        with self.store._connect() as connection:
            connection.execute(
                "INSERT INTO job_processes VALUES (?,?,?,?,?,?,?,?)",
                (child.pid, start_ticks(child.pid), self.job, 999999, 1, "docker", time.time() + deadline_offset, "t"),
            )
        return child

    def test_a_leftover_within_its_deadline_blocks_new_work(self):
        child = self.leftover(600)
        running = self.processes.settle(lambda _line: None)
        self.assertEqual(len(running), 1)
        self.assertIn(f"pid {child.pid}", running[0])
        self.assertIsNone(child.poll())

    def test_a_leftover_past_its_deadline_is_stopped(self):
        child = self.leftover(-3600)
        lines: list[str] = []
        self.assertEqual(self.processes.settle(lines.append), [])
        self.assertIsNotNone(child.wait(5))
        self.assertIn("past its time limit", lines[0])
        self.assertEqual(self.rows(), [])

    def test_a_finished_leftover_is_forgotten(self):
        child = self.leftover(600)
        child.kill()
        child.wait()
        self.assertEqual(self.processes.settle(lambda _line: None), [])
        self.assertEqual(self.rows(), [])


class RewireTests(_Paths):
    def test_a_consumer_waiting_for_recovery_or_held_by_another_job_is_left_alone(self):
        from types import SimpleNamespace

        from ctl.engine import rewire

        def app(app_id):
            return SimpleNamespace(id=app_id, manifest=SimpleNamespace(name=app_id.title()))

        blocked, busy, free = app("blocked"), app("busy"), app("free")
        journal = SimpleNamespace(needing_attention=lambda app_id: object() if app_id == "blocked" else None)
        lines: list[str] = []
        holder_inside, release = threading.Event(), threading.Event()

        def hold_busy():
            with resource_locks.hold("app:busy", paths=self.paths):
                holder_inside.set()
                release.wait(5)

        thread = threading.Thread(target=hold_busy)
        thread.start()
        holder_inside.wait(5)
        try:
            with (
                patch.object(rewire, "consumers", return_value=[blocked, busy, free]),
                patch.object(rewire, "Facts"),
                patch.object(rewire, "tailnet_dns_name", return_value=""),
                patch.object(rewire, "LOCK_WAIT_SECONDS", 0.3),
                patch("ctl.operations.OperationStore.runtime", return_value=journal),
                patch.object(rewire, "rewire_one", return_value=True) as rewire_one,
            ):
                restarted = rewire.rewire_consumers(app("provider"), None, lines.append, self.paths)
        finally:
            release.set()
            thread.join()
        self.assertEqual(restarted, ["Free"])
        self.assertEqual([call.args[0].id for call in rewire_one.call_args_list], ["free"])
        self.assertTrue(any("needs attention first" in line for line in lines))
        self.assertTrue(any("busy with other work" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
