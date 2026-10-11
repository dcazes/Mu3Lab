"""Scheduled backups run when the owner chose, once each, through the ordinary backup jobs."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ctl import backup_schedule, backups, platform_backup
from ctl.api import create_app
from ctl.jobs import JobStore
from ctl.runtime import RuntimePaths
from ctl.store import records
from tests import test_maintenance

NEW_YORK = ZoneInfo("America/New_York")
WEEKLY = {"frequency": "weekly", "weekday": 6, "time": "03:30", "changed_at": ""}


def local(*args) -> datetime:
    return datetime(*args, tzinfo=NEW_YORK)


class TimingTests(unittest.TestCase):
    def test_weekly_runs_on_the_chosen_day_and_time(self):
        # Wednesday 2026-10-07: the latest run was Sunday 2026-10-04 at 03:30, the next is 2026-10-11.
        now = local(2026, 10, 7, 12, 0)
        self.assertEqual(backup_schedule.latest(WEEKLY, now, NEW_YORK), local(2026, 10, 4, 3, 30))
        self.assertEqual(backup_schedule.upcoming(WEEKLY, now, NEW_YORK), local(2026, 10, 11, 3, 30))

    def test_shorter_frequencies_repeat_from_the_chosen_time(self):
        now = local(2026, 10, 7, 12, 0)
        every_six = {**WEEKLY, "frequency": "6h"}
        self.assertEqual(backup_schedule.latest(every_six, now, NEW_YORK), local(2026, 10, 7, 9, 30))
        self.assertEqual(backup_schedule.upcoming(every_six, now, NEW_YORK), local(2026, 10, 7, 15, 30))
        twice = {**WEEKLY, "frequency": "12h"}
        self.assertEqual(backup_schedule.upcoming(twice, now, NEW_YORK), local(2026, 10, 7, 15, 30))
        daily = {**WEEKLY, "frequency": "daily"}
        self.assertEqual(backup_schedule.upcoming(daily, now, NEW_YORK), local(2026, 10, 8, 3, 30))
        self.assertIsNone(backup_schedule.upcoming({**WEEKLY, "frequency": "off"}, now, NEW_YORK))

    def test_local_time_holds_across_daylight_saving_changes(self):
        # Clocks go back on Sunday 2026-11-01: the run is still at 03:30 local, now five hours after UTC.
        after = local(2026, 11, 2, 12, 0)
        run = backup_schedule.latest(WEEKLY, after, NEW_YORK)
        self.assertEqual(run, local(2026, 11, 1, 3, 30))
        self.assertEqual(run.astimezone(UTC).hour, 8)
        before = backup_schedule.latest(WEEKLY, local(2026, 10, 26, 12, 0), NEW_YORK)
        self.assertEqual(before.astimezone(UTC).hour, 7)

    def test_a_run_counts_only_after_the_last_run_and_the_last_change(self):
        now = local(2026, 10, 7, 12, 0)
        self.assertEqual(backup_schedule.due(WEEKLY, {}, now, NEW_YORK), local(2026, 10, 4, 3, 30))
        ran = {"scheduled_for": local(2026, 10, 4, 3, 30).isoformat()}
        self.assertIsNone(backup_schedule.due(WEEKLY, ran, now, NEW_YORK))
        changed = {**WEEKLY, "changed_at": local(2026, 10, 5, 9, 0).isoformat()}
        self.assertIsNone(backup_schedule.due(changed, {}, now, NEW_YORK))
        # The next Sunday after the change is due.
        self.assertIsNotNone(backup_schedule.due(changed, ran, local(2026, 10, 11, 3, 31), NEW_YORK))

    def test_settings_are_checked(self):
        self.assertEqual(
            backup_schedule.validate({"frequency": "daily", "weekday": 0, "time": "23:59"})["time"], "23:59"
        )
        for bad in (
            {"frequency": "hourly", "weekday": 0, "time": "03:30"},
            {"frequency": "weekly", "weekday": 7, "time": "03:30"},
            {"frequency": "weekly", "weekday": True, "time": "03:30"},
            {"frequency": "weekly", "weekday": 0, "time": "3:30"},
            {"frequency": "weekly", "weekday": 0, "time": "24:00"},
        ):
            with self.subTest(settings=bad), self.assertRaises(ValueError):
                backup_schedule.validate(bad)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = RuntimePaths(Path(self.tmp.name))
        self.paths.state.mkdir(parents=True)
        self.store = JobStore(self.paths.state / "mu3lab.db")
        self.apps = [
            SimpleNamespace(id="mealie", name="Mealie", stage="optional"),
            SimpleNamespace(id="immich", name="Immich", stage="optional"),
            SimpleNamespace(id="lobehub", name="LobeChat", stage="core"),
        ]
        for app in self.apps:
            (self.paths.projects / app.id).mkdir(parents=True)
            (self.paths.projects / app.id / "docker-compose.yml").write_text("services: {}\n")
        self.states = {"mealie": "running", "immich": "stopped"}
        control = SimpleNamespace(installation=lambda service_id: {"state": self.states.get(service_id, "")})
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("ctl.backup_schedule.load", return_value=SimpleNamespace(services=self.apps)))
        stack.enter_context(patch("ctl.backup_schedule.data_directories", return_value=[Path("/data")]))
        stack.enter_context(patch("ctl.backup_schedule.ControlState.runtime", return_value=control))
        stack.enter_context(patch("ctl.backup_schedule.OperationStore.runtime", return_value=None))

    def queued(self):
        return [(job["service_id"], job["action"], job["actor"]) for job in self.store.active_jobs()]

    def test_mu3lab_itself_goes_first_then_each_installed_app(self):
        result = backup_schedule.queue(self.store, Path("/repo"), local(2026, 10, 4, 3, 30), self.paths)
        self.assertEqual(
            self.queued(),
            [
                (platform_backup.SERVICE_ID, platform_backup.ACTION, "schedule"),
                ("mealie", "backup", "schedule"),
                ("immich", "backup", "schedule"),
            ],
        )
        self.assertEqual(len(result["jobs"]), 3)
        # Repeating the same run, as after a worker restart, queues nothing new.
        again = backup_schedule.queue(self.store, Path("/repo"), local(2026, 10, 4, 3, 30), self.paths)
        self.assertEqual(again["jobs"], result["jobs"])
        self.assertEqual(len(self.queued()), 3)

    def test_apps_mid_change_or_busy_are_skipped_with_why(self):
        self.states["immich"] = "installing"
        self.store.create(kind="lifecycle", service_id="mealie", action="update", actor="owner")
        result = backup_schedule.queue(self.store, Path("/repo"), local(2026, 10, 4, 3, 30), self.paths)
        reasons = {item["id"]: item["reason"] for item in result["skipped"]}
        self.assertEqual(reasons["immich"], "it was being set up or changed")
        self.assertEqual(reasons["mealie"], "another job was running on it")

    def test_a_new_schedule_starts_from_now_and_never_runs_at_once(self):
        now = local(2026, 10, 7, 12, 0)
        started = now - timedelta(hours=1)
        self.assertIsNone(
            backup_schedule.tick(
                self.store, Path("/repo"), print, worker_started=started, now=now, paths=self.paths, tz=NEW_YORK
            )
        )
        self.assertEqual(self.queued(), [])
        sunday = local(2026, 10, 11, 3, 30)
        result = backup_schedule.tick(
            self.store,
            Path("/repo"),
            print,
            worker_started=started,
            now=sunday + timedelta(seconds=30),
            paths=self.paths,
            tz=NEW_YORK,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["scheduled_for"], sunday.isoformat())

    def test_a_missed_run_waits_until_the_server_has_settled(self):
        records.put(
            *backup_schedule.LAST_RUN, {"scheduled_for": local(2026, 10, 4, 3, 30).isoformat(), "jobs": []}, self.paths
        )
        now = local(2026, 10, 11, 9, 0)  # off at 03:30, switched on at 08:58
        self.assertIsNone(
            backup_schedule.tick(
                self.store,
                Path("/repo"),
                print,
                worker_started=now - timedelta(minutes=2),
                now=now,
                paths=self.paths,
                tz=NEW_YORK,
            )
        )
        result = backup_schedule.tick(
            self.store,
            Path("/repo"),
            print,
            worker_started=now - timedelta(minutes=11),
            now=now,
            paths=self.paths,
            tz=NEW_YORK,
        )
        self.assertEqual(result["scheduled_for"], local(2026, 10, 11, 3, 30).isoformat())

    def test_status_reports_the_last_run_from_its_jobs(self):
        result = backup_schedule.queue(self.store, Path("/repo"), local(2026, 10, 4, 3, 30), self.paths)
        self.store.transition(result["jobs"][1], "running", actor="worker")
        self.store.transition(result["jobs"][1], "failed", actor="worker", detail="Mealie was not backed up: disk full")
        status = backup_schedule.status(self.store, self.paths, now=local(2026, 10, 7, 12, 0))
        self.assertEqual(status["frequency"], "weekly")
        self.assertEqual((status["last_run"]["jobs"], status["last_run"]["failed"]), (3, 1))
        self.assertIn("disk full", status["last_run"]["problems"][0])


class ScheduledReasonTests(unittest.TestCase):
    def test_scheduled_is_a_backup_reason(self):
        self.assertIn("scheduled", backups.REASONS)


class AttentionTests(unittest.TestCase):
    """Home warns administrators only when Mu3Lab's own backup falls behind the schedule."""

    NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = RuntimePaths(Path(self.tmp.name))
        self.paths.state.mkdir(parents=True)
        self.store = JobStore(self.paths.state / "mu3lab.db")

    def backed_up(self, days_ago: float) -> None:
        when = (self.NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
        records.put("backup-apps", backups.PLATFORM_SOURCE, {"completed_at": when, "snapshot_id": "k1"}, self.paths)

    def schedule(self, frequency: str, started_days_ago: float = 1) -> None:
        records.put(*backup_schedule.SETTINGS, {"frequency": frequency, "weekday": 6, "time": "03:30"}, self.paths)
        start = (self.NOW - timedelta(days=started_days_ago)).isoformat(timespec="seconds")
        records.put(*backup_schedule.LAST_RUN, {"scheduled_for": start, "jobs": [], "skipped": []}, self.paths)

    def keys(self) -> list[str]:
        return [item["key"] for item in backup_schedule.attention(self.store, self.paths, now=self.NOW)]

    def test_nothing_while_backups_keep_up(self):
        self.schedule("weekly")
        self.backed_up(6)
        self.assertEqual(self.keys(), [])

    def test_an_overdue_backup_is_reported_with_its_age(self):
        self.schedule("weekly")
        self.backed_up(9)
        items = backup_schedule.attention(self.store, self.paths, now=self.NOW)
        self.assertEqual([item["key"] for item in items], ["backup-overdue"])
        self.assertIn("9 days ago", items[0]["message"])
        # A daily schedule allows a day and a half.
        self.schedule("daily")
        self.backed_up(1.2)
        self.assertEqual(self.keys(), [])
        self.backed_up(2)
        self.assertEqual(self.keys(), ["backup-overdue"])

    def test_a_new_install_is_not_nagged_before_its_first_run_was_due(self):
        self.schedule("weekly", started_days_ago=2)
        self.assertEqual(self.keys(), [])
        self.schedule("weekly", started_days_ago=8)
        self.assertEqual(self.keys(), ["backup-missing"])

    def test_backups_turned_off_are_named(self):
        self.schedule("off")
        items = backup_schedule.attention(self.store, self.paths, now=self.NOW)
        self.assertEqual([item["key"] for item in items], ["backup-missing"])
        self.assertIn("Scheduled backups are off", items[0]["message"])
        self.backed_up(3)
        self.assertEqual(self.keys(), [])

    def test_failed_jobs_of_the_last_run_are_counted_and_a_running_run_is_left_alone(self):
        self.schedule("weekly")
        self.backed_up(9)
        done = self.store.create(kind="lifecycle", service_id="mealie", action="backup", actor="schedule")
        self.store.transition(done["id"], "failed", actor="worker", detail="disk full")
        running = self.store.create(
            kind="backup", service_id=platform_backup.SERVICE_ID, action="backup", actor="schedule"
        )
        last = {"scheduled_for": self.NOW.isoformat(), "jobs": [done["id"], running["id"]], "skipped": []}
        records.put(*backup_schedule.LAST_RUN, last, self.paths)
        items = backup_schedule.attention(self.store, self.paths, now=self.NOW)
        # The overdue warning waits while the run is still going; its failure already shows.
        self.assertEqual([item["key"] for item in items], ["backup-failed"])
        self.assertIn("1 failed backup", items[0]["message"])


class ScheduleApiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("ctl.api.security.ingress_token", return_value="token"))
        self.stack.enter_context(patch("ctl.api.security.csrf_token", return_value="bound"))
        self.client = TestClient(create_app())
        self.headers = test_maintenance.MaintenanceApiTests.headers

    def test_an_administrator_reads_and_changes_the_schedule_and_it_is_audited(self):
        current = self.client.get("/api/v1/system/backups/schedule", headers=self.headers)
        self.assertEqual(current.status_code, 200, current.text)
        body = {"frequency": "daily", "weekday": 2, "time": "01:15"}
        changed = self.client.put("/api/v1/system/backups/schedule", headers=self.headers, json=body)
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual({key: changed.json()[key] for key in body}, body)
        self.assertTrue(changed.json()["next_run_at"])
        store = JobStore.runtime()
        assert store is not None
        self.assertTrue(any(event["event"] == "backup.schedule_changed" for event in store.audit()))
        restore = {"frequency": "weekly", "weekday": 6, "time": "03:30"}
        self.client.put("/api/v1/system/backups/schedule", headers=self.headers, json=restore)

    def test_out_of_range_settings_are_refused(self):
        for body in (
            {"frequency": "hourly", "weekday": 0, "time": "03:30"},
            {"frequency": "weekly", "weekday": 9, "time": "03:30"},
            {"frequency": "weekly", "weekday": 0, "time": "25:00"},
        ):
            with self.subTest(body=body):
                response = self.client.put("/api/v1/system/backups/schedule", headers=self.headers, json=body)
                self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
