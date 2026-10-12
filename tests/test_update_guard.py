"""After an update the previous version comes back by itself if the new one fails (R17)."""

from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from ctl import update_guard
from ctl.jobs import JobStore

ROOT = Path(__file__).resolve().parents[1]


class FakeSystem:
    """Answers systemctl, git, uv and the old release's finish; records each command."""

    def __init__(self, *, active=True, fail: str = ""):
        self.commands: list[list[str]] = []
        self.active, self.fail = active, fail

    def __call__(self, argv, **_kwargs):
        self.commands.append(list(argv))
        joined = " ".join(argv)
        if self.fail and self.fail in joined:
            return 1, "it broke"
        if "is-active" in argv:
            return (0 if self.active else 3), ""
        return 0, ""

    def ran(self, word: str) -> bool:
        return any(word in " ".join(command) for command in self.commands)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        state = Path(self.temp.name)
        self.database = state / "mu3lab.db"
        self.store = JobStore(self.database)
        self.job = self.store.create(kind="update", service_id="mu3lab", action="self_update", actor="owner")
        self.store.claim("old-worker")
        self.copy = state / "pre-update.db"
        with closing(sqlite3.connect(self.database)) as live, closing(sqlite3.connect(self.copy)) as copy:
            live.backup(copy)
        self.plan = {
            "root": "/home/me/Mu3Lab",
            "before": "abc123",
            "running": "v1.0.1",
            "target": "v1.0.2",
            "job_id": self.job["id"],
            "state": str(state),
            "database": str(self.database),
            "database_copy": str(self.copy),
            "units": ["mu3lab-ctl.service", "mu3lab-worker.service"],
            "health_url": "http://127.0.0.1:8787/api/health",
            "env": {},
            "verify_seconds": 60,
        }
        self.clock = [0.0]

    def guard(self, mode: str, system: FakeSystem, answers: bool):
        def sleep(seconds):
            self.clock[0] += seconds

        return update_guard.guard(
            self.plan, mode, runner=system, check=lambda _url: answers, sleep=sleep, clock=lambda: self.clock[0]
        )

    def recorded(self) -> dict:
        return json.loads((Path(self.plan["state"]) / update_guard.RESULT).read_text())

    def test_a_new_release_that_answers_is_recorded_as_verified(self):
        system = FakeSystem()
        result = self.guard("verify", system, answers=True)
        self.assertEqual(result["state"], "verified")
        self.assertEqual(self.recorded()["to"], "v1.0.2")
        self.assertFalse(system.ran("checkout"))
        self.assertTrue(any(event["event"] == "platform.update_verified" for event in self.store.audit()))

    def test_a_release_that_does_not_start_is_rolled_back(self):
        system = FakeSystem()
        answered = iter([False] * 6 + [True] * 10)  # dead for a minute, then the old version answers
        result = update_guard.guard(
            self.plan,
            "verify",
            runner=system,
            check=lambda _url: next(answered),
            sleep=lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds),
            clock=lambda: self.clock[0],
        )
        self.assertEqual(result["state"], "rolled_back", result["detail"])
        order = [" ".join(command[:4]) for command in system.commands if "is-active" not in command]
        self.assertTrue(order[0].startswith("systemctl --user stop"))
        self.assertIn("git -C /home/me/Mu3Lab checkout", order[1])
        self.assertTrue(system.ran("sync --frozen"))
        self.assertTrue(system.ran("ctl.self_update finish"))
        self.assertTrue(order[-1].startswith("systemctl --user start"))
        self.assertIn("did not start", result["detail"])
        # The update's job ends failed, with why, and the audit log says so.
        job = self.store.get(self.job["id"])
        self.assertEqual(job["state"], "failed")
        self.assertTrue(any(event["event"] == "platform.update_rolled_back" for event in self.store.audit()))

    def test_the_database_copy_is_put_back_only_if_the_new_release_upgraded_it(self):
        # Work done during the update, on the same schema, is kept.
        later = self.store.create(kind="update", service_id="other", action="x", actor="owner")
        result = self.guard("rollback", FakeSystem(), answers=True)
        self.assertNotIn("database was put back", result["detail"])
        with closing(sqlite3.connect(self.database)) as conn, conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM jobs WHERE id = ?", (later["id"],)).fetchone()[0], 1)
        # A later accepted job must survive even if the schema then changes.
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO schema_version (version) VALUES (999)")
        system = FakeSystem()
        result = self.guard("rollback", system, answers=True)
        self.assertEqual(result["state"], "needs_attention")
        self.assertFalse(system.ran("checkout"))
        self.assertFalse(system.ran("--user start"))
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM jobs WHERE id=?", (later["id"],)).fetchone()[0], 1)
        self.assertEqual(update_guard.schema(self.database), 999)

    def test_schema_only_change_can_restore_the_checkpoint(self):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO schema_version (version) VALUES (999)")
        result = self.guard("rollback", FakeSystem(), answers=True)
        self.assertEqual(result["state"], "rolled_back", result["detail"])
        self.assertNotEqual(update_guard.schema(self.database), 999)
        self.assertEqual(self.store.get(self.job["id"])["state"], "failed")

    def test_completed_effect_is_not_requeued_by_schema_rollback(self):
        effect = self.store.create(kind="lifecycle", service_id="demo", action="reset", actor="owner")
        with closing(sqlite3.connect(self.database)) as live, closing(sqlite3.connect(self.copy)) as copy:
            live.backup(copy)
        self.store.transition(effect["id"], "running", actor="worker")
        self.store.transition(effect["id"], "succeeded", actor="worker")
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO schema_version (version) VALUES (999)")
        result = self.guard("rollback", FakeSystem(), answers=True)
        self.assertEqual(result["state"], "needs_attention")
        self.assertEqual(self.store.get(effect["id"])["state"], "succeeded")

    def test_old_healthy_worker_cannot_verify_the_new_release(self):
        self.plan["old_worker_pid"] = 123
        system = FakeSystem()
        with patch.object(update_guard, "_alive", return_value=True):
            healthy = update_guard.started(
                self.plan,
                system,
                lambda _url: True,
                lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds),
                lambda: self.clock[0],
            )
        self.assertFalse(healthy)
        self.assertFalse(system.ran("is-active"))

    def test_failed_stop_never_replaces_code_or_database(self):
        system = FakeSystem(fail="--user stop")
        before = self.database.read_bytes()
        result = self.guard("rollback", system, answers=True)
        self.assertEqual(result["state"], "needs_attention")
        self.assertFalse(system.ran("checkout"))
        self.assertFalse(system.ran("--user start"))
        self.assertEqual(update_guard.schema(self.database), update_guard.schema(self.copy))
        self.assertTrue(before)

    def test_when_going_back_fails_the_person_is_told_exactly_what_to_run(self):
        result = self.guard("rollback", FakeSystem(fail="checkout"), answers=True)
        self.assertEqual(result["state"], "needs_attention")
        self.assertIn("git checkout abc123 && ./install.sh", result["detail"])

    def test_the_guard_needs_only_the_standard_library(self):
        # It is copied out of the checkout and must run whatever Mu3Lab version is on disk.
        tree = ast.parse((ROOT / "ctl" / "update_guard.py").read_text(encoding="utf-8"))
        imported = {
            (node.module or "").split(".")[0] if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse({name for name in imported if name == "ctl" or name in {"httpx", "pydantic", "yaml"}})


if __name__ == "__main__":
    unittest.main()
