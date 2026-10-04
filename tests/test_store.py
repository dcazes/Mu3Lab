"""Shared state must be private, atomic and safe across process restarts."""

from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.runtime import RuntimePaths
from ctl.store import db, owners
from ctl.store.secrets import SecretError, SecretStore


def initialize(root: str) -> None:
    paths = RuntimePaths(Path(root))
    SecretStore(paths).put("process", str(multiprocessing.current_process().pid), "private-value")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = RuntimePaths(Path(self.temporary.name))
        self.store = SecretStore(self.paths)

    def test_parallel_process_startup_applies_schema_once_and_keeps_all_writes(self):
        context = multiprocessing.get_context("spawn")
        processes = [context.Process(target=initialize, args=(self.temporary.name,)) for _ in range(4)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(self.store.list_names("process")), 4)
        with db.connect(db.database(self.paths)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0],
                len(list(db.MIGRATIONS.glob("*.sql"))),
            )
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(list(self.paths.state.glob("*.db")), [db.database(self.paths)])
        for path in (db.database(self.paths), self.paths.state / "secrets.key"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_expired_secrets_cannot_be_read_and_are_removed(self):
        with patch("ctl.store.secrets.time.time", return_value=100):
            self.store.put("job", "one", {"password": "hidden"}, ttl=10)
            self.store.put("provider", "one", "permanent")
        with patch("ctl.store.secrets.time.time", return_value=111):
            self.assertIsNone(self.store.get("job", "one"))
            self.assertEqual(self.store.list_names("job"), [])
            self.assertEqual(self.store.cleanup(), ["one"])
            self.assertEqual(self.store.get("provider", "one"), "permanent")

    def test_missing_key_is_reported_without_creating_a_replacement(self):
        self.store.put("provider", "one", "private-value")
        key = self.paths.state / "secrets.key"
        key.unlink()
        with self.assertRaises(SecretError):
            self.store.get("provider", "one")
        with self.assertRaises(SecretError):
            self.store.put("provider", "two", "another-value")
        self.assertFalse(key.exists())

    def test_private_job_inputs_and_job_are_committed_or_rolled_back_together(self):
        jobs = JobStore(db.database(self.paths))

        def prepare(job_id: str) -> None:
            self.store.put("job", job_id, "private-value")
            raise ValueError("preparation failed")

        with self.assertRaises(ValueError):
            jobs.create(kind="lifecycle", service_id="example", action="install", actor="owner", prepare=prepare)
        self.assertEqual(jobs.jobs(), [])
        self.assertEqual(self.store.list_names("job"), [])
        job = jobs.create(
            kind="lifecycle",
            service_id="example",
            action="install",
            actor="owner",
            prepare=lambda job_id: self.store.put("job", job_id, "private-value"),
        )
        self.assertEqual(self.store.get("job", job["id"]), "private-value")

    def test_one_owner_is_shared_and_cannot_be_replaced_by_another_installer(self):
        path = db.database(self.paths)
        state = ControlState(path)
        state.set_initialization("example", "api_bootstrap", "pending", owner_uid="first")
        owners.remember("example", {"owner_uid": "first", "email": "first@example.com"}, path)
        state.set_service_identity("example", "native_oidc", "ready", owner_uid="second")
        self.assertEqual(state.initialization("example")["owner_uid"], "first")
        self.assertEqual(state.service_identity("example")["owner_uid"], "first")
        self.assertEqual(owners.get("example", path)["email"], "first@example.com")

    def test_newer_schema_is_rejected(self):
        path = db.database(self.paths)
        with db.connect(path) as connection:
            connection.execute("INSERT INTO schema_version VALUES (999)")
        db._ready.discard((str(path.resolve()), path.stat().st_ino))
        with self.assertRaisesRegex(ValueError, "newer Mu3Lab release"):
            db.connect(path)
