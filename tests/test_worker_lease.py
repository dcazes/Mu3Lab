"""A worker gives up a job only when another worker could really have taken it (review B-L4)."""

from __future__ import annotations

import sqlite3
import threading
import unittest

from ctl import worker


class _Stop(threading.Event):
    """Never waits; stops after ``rounds`` renewals."""

    def __init__(self, rounds: int) -> None:
        super().__init__()
        self.rounds = rounds

    def wait(self, timeout=None) -> bool:
        self.rounds -= 1
        return self.rounds < 0


class _Store:
    def __init__(self, results) -> None:
        self.results = list(results)

    def heartbeat(self, *_args, **_kwargs) -> bool:
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class MaintainLeaseTests(unittest.TestCase):
    def run_lease(self, results, rounds: int) -> bool:
        ticks = iter(range(0, 1000, 10))  # each renewal is 10 seconds after the last
        lost = threading.Event()
        worker._maintain_lease(
            _Store(results), {"id": "j"}, "w", _Stop(rounds), lost, every=10, clock=lambda: next(ticks)
        )
        return lost.is_set()

    def test_a_briefly_locked_database_does_not_stop_the_job(self):
        locked = sqlite3.OperationalError("database is locked")
        self.assertFalse(self.run_lease([locked, locked, True, locked, True], rounds=5))

    def test_a_database_locked_until_the_lease_could_expire_stops_the_job(self):
        locked = sqlite3.OperationalError("database is locked")
        self.assertTrue(self.run_lease([locked] * 5, rounds=5))

    def test_a_refused_renewal_stops_the_job_at_once(self):
        self.assertTrue(self.run_lease([True, False], rounds=5))


if __name__ == "__main__":
    unittest.main()
