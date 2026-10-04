"""Encrypted stores keep every record when the dashboard and worker write at once."""

from __future__ import annotations

import multiprocessing
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from ctl.runtime import RuntimePaths
from ctl.secret_file import read_or_create_key
from ctl.store import calendars as calendar_secrets
from ctl.store import providers as provider_secrets
from ctl.store import workflows as workflow_secrets


def _save_identity(root: str, index: int) -> None:
    workflow_secrets.save_job_identity(
        f"job-{index}",
        owner_uid=f"owner-{index}",
        email=f"o{index}@example.test",
        username=f"o{index}",
        display_name="Owner",
        paths=RuntimePaths(Path(root)),
    )


def _make_key(root: str, results) -> None:
    results.append(read_or_create_key(Path(root) / "shared.key", lambda: __import__("os").urandom(16)))


class ConcurrentStoreTests(unittest.TestCase):
    def test_parallel_workflow_saves_keep_every_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            threads = [threading.Thread(target=_save_identity, args=(tmp, index)) for index in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            paths = RuntimePaths(Path(tmp))
            found = [workflow_secrets.job_identity(f"job-{index}", paths) for index in range(12)]
        self.assertTrue(all(found))

    def test_parallel_processes_keep_every_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = multiprocessing.get_context("fork")
            processes = [context.Process(target=_save_identity, args=(tmp, index)) for index in range(6)]
            for process in processes:
                process.start()
            for process in processes:
                process.join()
            paths = RuntimePaths(Path(tmp))
            self.assertTrue(all(workflow_secrets.job_identity(f"job-{index}", paths) for index in range(6)))

    def test_one_key_is_created_even_when_processes_race(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = multiprocessing.get_context("fork").Manager()
            results = manager.list()
            processes = [
                multiprocessing.get_context("fork").Process(target=_make_key, args=(tmp, results)) for _ in range(6)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join()
            self.assertEqual(len(set(results)), 1)
            mode = stat.S_IMODE((Path(tmp) / "shared.key").stat().st_mode)
            manager.shutdown()
        self.assertEqual(mode, 0o600)

    def test_provider_and_calendar_stores_keep_parallel_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            providers = ("groq", "cerebras", "mistral", "openrouter")
            threads = [
                threading.Thread(
                    target=provider_secrets.save,
                    args=(provider, provider, f"key-{provider}-123456"),
                    kwargs={"paths": paths},
                )
                for provider in providers
            ] + [
                threading.Thread(
                    target=calendar_secrets.save, args=(f"owner-{i}", f"user{i}", "app-pass"), kwargs={"paths": paths}
                )
                for i in range(6)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual({item["id"] for item in provider_secrets.metadata(paths)}, set(providers))
            self.assertTrue(all(calendar_secrets.get(f"owner-{i}", paths) for i in range(6)))
            store = paths.state / "mu3lab.db"
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)
            self.assertEqual([p.name for p in paths.runtime.glob("*.tmp")], [])


if __name__ == "__main__":
    unittest.main()
