"""Kopia's server makes every backup; Mu3Lab stops and starts each app around it and keeps the server in step."""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import backup_service, backups, platform_backup
from ctl.api import create_app
from ctl.jobs import JobConflict, JobStore
from ctl.lifecycle import in_progress
from ctl.runtime import RuntimePaths
from ctl.store import records

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t" * 48


class ScheduleTests(unittest.TestCase):
    def cron(self, frequency, weekday=6, time="03:30"):
        return backup_service.crontab({"frequency": frequency, "weekday": weekday, "time": time})

    def test_the_households_choice_becomes_one_crontab_in_this_computers_time(self):
        self.assertEqual(self.cron("weekly"), "30 3 * * 0")  # Sunday
        self.assertEqual(self.cron("weekly", weekday=0), "30 3 * * 1")  # Monday
        self.assertEqual(self.cron("daily", time="23:05"), "5 23 * * *")
        self.assertEqual(self.cron("12h", time="03:30"), "30 3,15 * * *")
        self.assertEqual(self.cron("6h", time="14:00"), "0 2,8,14,20 * * *")
        self.assertEqual(self.cron("off"), "")

    def test_the_policy_carries_retention_the_schedule_and_catching_up(self):
        policy = backup_service.global_policy({"frequency": "weekly", "weekday": 6, "time": "03:30"})
        self.assertIn("--keep-latest=10", policy)
        self.assertIn("--snapshot-time-crontab=30 3 * * 0", policy)
        self.assertIn("--run-missed=true", policy)
        off = backup_service.global_policy({"frequency": "off", "weekday": 6, "time": "03:30"})
        self.assertIn("--snapshot-time-crontab=inherit", off)

    def test_settings_are_checked_and_default_to_weekly_on_sunday_night(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.state.mkdir(parents=True)
            self.assertEqual(backup_service.settings(paths)["frequency"], "weekly")
            for bad in ({"frequency": "hourly"}, {"frequency": "daily", "time": "25:00"}, {"weekday": 7}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    backup_service.validate({**backup_service.DEFAULTS, **bad})
            saved = backup_service.save({"frequency": "daily", "weekday": 2, "time": "01:15"}, "owner", paths)
            self.assertEqual((saved["frequency"], saved["time"]), ("daily", "01:15"))


class _Paths(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.paths = RuntimePaths(Path(temp.name))
        for folder in (self.paths.state, self.paths.data, self.paths.projects):
            folder.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.paths.state / "mu3lab.db")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mealie = self.paths.data / "mealie"
        self.mealie.mkdir()
        self.found = {
            "mu3lab": [("control", self.paths.state / "platform-export")],
            "mealie": [("mealie", self.mealie)],
        }
        self.stack.enter_context(patch.object(backup_service, "sources", side_effect=lambda *_a: self.found))
        records.put(*backup_service.STATE, {"inodes": {str(self.mealie): os.stat(self.mealie).st_ino}}, self.paths)


class SourcesTests(unittest.TestCase):
    def test_mu3lab_core_apps_and_installed_apps_with_data_are_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.state.mkdir(parents=True)
            (paths.projects / "mealie").mkdir(parents=True)
            (paths.projects / "mealie" / "docker-compose.yml").write_text("services: {}\n")
            for name in ("mealie", "authentik", "ollama", "paperless"):
                (paths.data / name).mkdir(parents=True)

            def folders(service, _root):
                return [paths.data / service.id]

            with patch.object(backup_service, "data_directories", side_effect=folders):
                found = backup_service.sources(ROOT, paths)
        self.assertEqual(found["mu3lab"][0], ("control", paths.state / "platform-export"))
        self.assertEqual(found["mealie"], [("mealie", paths.data / "mealie")])
        self.assertIn("authentik", found)
        self.assertNotIn("ollama", found)  # its models can be downloaded again
        self.assertNotIn("paperless", found)  # not installed
        mounts = backup_service.mounts({"mealie": found["mealie"]})
        self.assertEqual(mounts, [f"{paths.data / 'mealie'}:/data/mealie/mealie:ro"])


class HookTests(_Paths):
    def setUp(self):
        super().setUp()
        self.queued: list[str] = []
        self.outcome = "succeeded"

        def queue(_store, source, action, _detail, _since):
            self.queued.append(f"{action} {source}")
            return {"state": self.outcome if action == "backup_stop" else "succeeded", "detail": "no"}

        self.stack.enter_context(patch.object(backup_service, "_queue", side_effect=queue))
        self.stack.enter_context(patch.object(backup_service.app_releases, "installed", return_value=None))
        self.stack.enter_context(patch.object(backup_service.ControlState, "runtime", return_value=None))

    def test_an_app_is_stopped_before_and_started_after_its_backup(self):
        backup_service.before("mealie", self.store, ROOT, self.paths)
        self.assertTrue(backup_service.holding("mealie", self.paths))
        notes = records.get("backup-releases", "mealie", self.paths)["entries"]
        self.assertEqual(notes[0]["folders"], ["mealie"])
        backup_service.after("mealie", self.store, ROOT, self.paths)
        self.assertEqual(self.queued, ["backup_stop mealie", "backup_start mealie"])
        self.assertFalse(backup_service.holding("mealie", self.paths))
        self.assertEqual(backup_service.problems(self.paths), [])

    def test_an_app_the_owner_stopped_is_backed_up_without_starting_it(self):
        state = SimpleNamespace(installation=lambda _id: {"state": "stopped"})
        with patch.object(backup_service.ControlState, "runtime", return_value=state):
            backup_service.before("mealie", self.store, ROOT, self.paths)
            backup_service.after("mealie", self.store, ROOT, self.paths)
        self.assertEqual(self.queued, [])

    def test_an_app_that_cannot_be_stopped_is_started_again_and_skipped(self):
        self.outcome = "failed"
        with self.assertRaises(backup_service.HookRefused):
            backup_service.before("mealie", self.store, ROOT, self.paths)
        self.assertEqual(self.queued, ["backup_stop mealie", "backup_start mealie"])
        self.assertFalse(backup_service.holding("mealie", self.paths))
        self.assertTrue(backup_service.problems(self.paths)[0].startswith("mealie:"))

    def test_a_folder_swapped_by_a_restore_is_never_backed_up_through_a_stale_mount(self):
        self.mealie.rename(self.paths.data / ".mealie.original")
        self.mealie.mkdir()
        with self.assertRaisesRegex(backup_service.HookRefused, "restarts soon"):
            backup_service.before("mealie", self.store, ROOT, self.paths)
        self.assertEqual(self.queued, [])

    def test_an_app_waiting_for_its_owner_or_busy_is_skipped(self):
        in_progress.begin("mealie", "restore", "job", self.paths)
        with self.assertRaisesRegex(backup_service.HookRefused, "needs attention"):
            backup_service.before("mealie", self.store, ROOT, self.paths)
        in_progress.clear("mealie", self.paths)
        with (
            patch.object(backup_service, "_queue", side_effect=JobConflict("resource_busy", "j")),
            self.assertRaisesRegex(backup_service.HookRefused, "Another task"),
        ):
            backup_service.before("mealie", self.store, ROOT, self.paths)
        self.assertFalse(backup_service.holding("mealie", self.paths))

    def test_a_stop_still_waiting_for_the_worker_leaves_the_start_to_the_stuck_app_check(self):
        self.outcome = "failed"
        real = backup_service._queue

        def queue(store, source, action, detail, since):
            if action == "backup_start":
                raise JobConflict("resource_busy", "stop-job")
            return real(store, source, action, detail, since)

        with patch.object(backup_service, "_queue", side_effect=queue), self.assertRaises(backup_service.HookRefused):
            backup_service.before("mealie", self.store, ROOT, self.paths)
        self.assertTrue(backup_service.holding("mealie", self.paths))

    def test_unknown_sources_are_refused(self):
        for source in ("not-an-app", "kopia-ui"):
            with self.subTest(source=source), self.assertRaises(backup_service.HookRefused):
                backup_service.before(source, self.store, ROOT, self.paths)

    def test_mu3labs_own_state_is_exported_before_and_cleared_after(self):
        (self.paths.state / "secrets.key").write_bytes(b"key")
        backup_service.before("mu3lab", self.store, ROOT, self.paths)
        export = platform_backup.export_folder(self.paths)
        self.assertTrue((export / "secrets.key").is_file())
        backup_service.after("mu3lab", self.store, ROOT, self.paths)
        self.assertEqual(list(export.iterdir()), [])


class StuckTests(_Paths):
    def hold(self, minutes_ago: float) -> None:
        since = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        records.put(backup_service.HOLDS, "mealie", {"since": since, "was_running": True}, self.paths)

    def test_an_app_whose_backup_ended_without_its_after_action_is_started_again(self):
        self.hold(10)
        backup_service._restart_stuck(self.store, {"mealie": {"status": "IDLE"}}, lambda _l: None, self.paths)
        jobs = self.store.active_jobs()
        self.assertEqual([(job["service_id"], job["action"]) for job in jobs], [("mealie", "backup_start")])
        self.assertFalse(backup_service.holding("mealie", self.paths))
        self.assertIn("did not finish", backup_service.problems(self.paths)[0])

    def test_a_backup_still_running_or_just_started_is_left_alone(self):
        self.hold(10)
        backup_service._restart_stuck(self.store, {"mealie": {"status": "UPLOADING"}}, lambda _l: None, self.paths)
        self.hold(1)
        backup_service._restart_stuck(self.store, {"mealie": {"status": "IDLE"}}, lambda _l: None, self.paths)
        self.hold(10)
        backup_service._restart_stuck(self.store, None, lambda _l: None, self.paths)  # Kopia cannot be asked yet
        self.assertEqual(self.store.active_jobs(), [])
        self.assertTrue(backup_service.holding("mealie", self.paths))


class ReconcileTests(_Paths):
    def setUp(self):
        super().setUp()
        (self.paths.projects / "kopia-ui").mkdir()
        (self.paths.projects / "kopia-ui" / "docker-compose.yml").write_text("services: {}\n")
        self.container = {"id": "c1", "started_at": "t1", "mounts": backup_service.mounts(self.found)}
        self.stack.enter_context(patch.object(backup_service, "_container", side_effect=lambda _log: self.container))
        self.recreate = self.stack.enter_context(patch.object(backup_service, "_recreate"))
        self.exec = self.stack.enter_context(patch.object(backup_service, "_exec"))
        self.control = self.stack.enter_context(patch.object(backup_service, "_control", return_value={}))
        self.status = self.stack.enter_context(patch.object(backup_service, "source_status", return_value={}))

    def reconcile(self):
        backup_service.reconcile(self.store, ROOT, lambda _l: None, self.paths)

    def test_policies_are_set_once_and_newest_backups_are_recorded(self):
        self.status.return_value = {"mealie": {"status": "IDLE", "lastSnapshot": {"endTime": "2026-10-11T03:31:00Z"}}}
        self.reconcile()
        self.reconcile()
        targets = [call.args[1][2] for call in self.exec.call_args_list]
        self.assertEqual(targets, ["--global", "/data/mu3lab", "/data/mealie"])
        self.assertEqual(records.get("backup-apps", "mealie", self.paths)["completed_at"], "2026-10-11T03:31:00Z")
        self.recreate.assert_not_called()

    def refreshes(self):
        return [call for call in self.control.call_args_list if call.args[:2] == ("POST", "refresh")]

    def test_kopia_is_refreshed_only_after_a_change(self):
        self.reconcile()
        self.reconcile()
        self.assertEqual(len(self.refreshes()), 1)

    def test_a_restarted_server_keeps_its_policies(self):
        self.reconcile()
        self.exec.reset_mock()
        self.container = {**self.container, "started_at": "t2"}
        self.reconcile()
        self.exec.assert_not_called()

    def test_nothing_changes_while_an_app_waits_its_turn(self):
        # A refresh would queue the waiting app a second time.
        self.status.return_value = {"mealie": {"status": "PENDING"}}
        self.reconcile()
        self.exec.assert_not_called()
        self.assertEqual(self.refreshes(), [])
        self.status.return_value = {"mealie": {"status": "IDLE"}}
        self.reconcile()
        self.assertEqual(len(self.refreshes()), 1)

    def test_an_app_never_backed_up_waits_for_the_schedule(self):
        self.status.return_value = {"mealie": {"status": "IDLE"}}
        self.reconcile()
        self.assertIn("--run-missed=false", self.exec.call_args_list[-1].args[1])
        self.status.return_value = {"mealie": {"status": "IDLE", "lastSnapshot": {"endTime": "2026-10-11T03:31:00Z"}}}
        self.reconcile()
        self.assertIn("--run-missed=inherit", self.exec.call_args_list[-1].args[1])

    def test_a_schedule_changed_inside_kopia_is_put_back_once(self):
        backed_up = {"status": "IDLE", "lastSnapshot": {"endTime": "2026-10-11T03:31:00Z"}}
        self.status.return_value = {"mealie": {**backed_up, "schedule": {"cron": ["30 3 * * 0"], "runMissed": True}}}
        self.reconcile()
        self.exec.reset_mock()
        self.status.return_value = {"mealie": {**backed_up, "schedule": {"intervalSeconds": 3600, "runMissed": True}}}
        self.reconcile()
        targets = [call.args[1][2] for call in self.exec.call_args_list]
        self.assertEqual(targets, ["--global", "/data/mealie"])
        self.exec.reset_mock()
        self.reconcile()  # still different: something else in Kopia sets it, so it is not rewritten every minute
        self.exec.assert_not_called()

    def test_a_changed_schedule_reaches_kopia(self):
        self.reconcile()
        backup_service.save({"frequency": "daily", "weekday": 6, "time": "02:00"}, "owner", self.paths)
        self.reconcile()
        self.assertIn("--snapshot-time-crontab=0 2 * * *", self.exec.call_args_list[-1].args[1])

    def test_a_new_app_is_mounted_but_never_while_a_backup_runs(self):
        recipes = self.paths.data / "paperless"
        recipes.mkdir()
        self.found = {**self.found, "paperless": [("paperless", recipes)]}
        self.status.return_value = {"mealie": {"status": "UPLOADING"}}
        self.reconcile()
        self.recreate.assert_not_called()
        self.status.return_value = {"mealie": {"status": "IDLE"}}
        self.reconcile()
        self.recreate.assert_called_once()
        written = (self.paths.projects / "kopia-ui" / backup_service.SOURCES_FILE).read_text()
        self.assertIn(f"{recipes}:/data/paperless/paperless:ro", written)

    def test_a_folder_a_restore_swapped_restarts_the_server_once_it_is_idle(self):
        self.reconcile()  # records this start's folders
        self.mealie.rename(self.paths.data / ".mealie.original")
        self.mealie.mkdir()
        self.reconcile()
        self.recreate.assert_called_once()


class AttentionTests(_Paths):
    def setUp(self):
        super().setUp()
        self.install_kopia()

    def install_kopia(self):
        (self.paths.projects / "kopia-ui").mkdir(exist_ok=True)
        (self.paths.projects / "kopia-ui" / "docker-compose.yml").write_text("services: {}\n")

    def test_without_kopia_backups_home_says_only_that_backups_are_off(self):
        (self.paths.projects / "kopia-ui" / "docker-compose.yml").unlink()
        messages = backup_service.attention(self.paths)
        self.assertEqual([item["key"] for item in messages], ["backup-service-missing"])
        self.assertIn("Install Kopia backups", messages[0]["message"])
        self.assertFalse(backup_service.status(self.paths)["service_installed"])

    def test_a_new_install_is_asked_for_its_first_backup(self):
        keys = [item["key"] for item in backup_service.attention(self.paths)]
        self.assertEqual(keys, ["backup-missing"])

    def test_an_overdue_backup_and_failures_are_reported(self):
        old = (datetime.now(UTC) - timedelta(days=9)).isoformat(timespec="seconds")
        backups.record_backup("mu3lab", old, self.paths)
        records.put(backup_service.RESULTS, "mealie", {"ok": False, "detail": "x", "at": old}, self.paths)
        messages = backup_service.attention(self.paths)
        self.assertEqual([item["key"] for item in messages], ["backup-overdue", "backup-failed"])
        self.assertIn("9 days ago", messages[0]["message"])
        backups.record_backup("mu3lab", datetime.now(UTC).isoformat(timespec="seconds"), self.paths)
        self.assertEqual([item["key"] for item in backup_service.attention(self.paths)], ["backup-failed"])

    def test_a_stopped_backup_service_is_reported_once_it_stays_down(self):
        backups.record_backup("mu3lab", datetime.now(UTC).isoformat(timespec="seconds"), self.paths)
        with (
            patch.object(backup_service, "source_status", side_effect=OSError("refused")),
            patch.object(backup_service, "_container", return_value={}),
        ):
            backup_service.reconcile(self.store, ROOT, lambda _l: None, self.paths)
            backup_service.reconcile(self.store, ROOT, lambda _l: None, self.paths)
        soon = datetime.now(UTC) + timedelta(minutes=5)
        self.assertEqual(backup_service.attention(self.paths, now=soon), [])  # a restart, most likely
        later = datetime.now(UTC) + timedelta(minutes=11)
        messages = backup_service.attention(self.paths, now=later)
        self.assertEqual([item["key"] for item in messages], ["backup-service-stopped"])
        self.assertIn("no backups are being made", messages[0]["message"])
        with (
            patch.object(backup_service, "source_status", return_value={}),
            patch.object(backup_service, "_container", return_value={}),
        ):
            backup_service.reconcile(self.store, ROOT, lambda _l: None, self.paths)
        self.assertEqual(backup_service.attention(self.paths, now=later), [])


class HookApiTests(unittest.TestCase):
    url: ClassVar[str] = "/api/v1/backup-hooks/before-snapshot-root"

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(backup_service, "hook_token", return_value=TOKEN))
        self.before = self.stack.enter_context(patch.object(backup_service, "before", return_value="ready"))
        self.stack.enter_context(patch("ctl.api.runtime.job_store"))
        self.client = TestClient(create_app())

    def post(self, token=TOKEN, source="/data/mealie", url=None):
        return self.client.post(url or self.url, headers={"authorization": f"Bearer {token}"}, json={"source": source})

    def test_only_the_backup_services_token_is_accepted(self):
        for token in ("", "wrong", TOKEN[:-1]):
            with self.subTest(token=token):
                self.assertEqual(self.post(token=token).status_code, 401)
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.before.call_args.args[0], "mealie")

    def test_no_identity_header_or_session_stands_in_for_the_token(self):
        response = self.client.post(
            self.url,
            headers={"x-authentik-username": "owner", "x-mu3lab-proxy-token": "anything"},
            json={"source": "/data/mealie"},
        )
        self.assertEqual(response.status_code, 401)

    def test_only_backup_sources_and_kopias_two_actions_exist(self):
        self.assertEqual(self.post(source="/etc").status_code, 400)
        self.assertEqual(self.post(source="/data/../etc").status_code, 400)
        self.assertEqual(self.post(url="/api/v1/backup-hooks/before-folder").status_code, 422)

    def test_a_refused_backup_is_a_409_so_kopia_skips_it(self):
        self.before.side_effect = backup_service.HookRefused("busy")
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertIn("busy", response.text)

    def test_a_short_token_never_counts(self):
        with patch.object(backup_service, "hook_token", return_value="short"):
            self.assertFalse(backup_service.token_valid("short"))


class ManualBackupApiTests(unittest.TestCase):
    def setUp(self):
        from tests.test_maintenance import MaintenanceApiTests

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("ctl.api.security.ingress_token", return_value="token"))
        self.stack.enter_context(patch("ctl.api.security.csrf_token", return_value="bound"))
        self.stack.enter_context(patch("ctl.api.runtime.job_store"))
        self.headers = MaintenanceApiTests.headers
        self.client = TestClient(create_app())
        self.installed = self.stack.enter_context(
            patch("ctl.api.routes.services.backup_service.installed", return_value=True)
        )

    def test_back_up_now_waits_for_kopia_backups_to_be_installed(self):
        self.installed.return_value = False
        response = self.client.post("/api/v1/services/mealie/backups", headers=self.headers, json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "backup_service_missing")

    def test_back_up_now_asks_kopia_for_exactly_that_app(self):
        with (
            patch("ctl.api.routes.services.backup_service.sources", return_value={"mealie": []}),
            patch("ctl.api.routes.services.backup_service.trigger") as trigger,
        ):
            response = self.client.post("/api/v1/services/mealie/backups", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200, response.text)
        trigger.assert_called_once_with(["mealie"])

    def test_an_app_without_data_or_already_being_backed_up_is_refused(self):
        with patch("ctl.api.routes.services.backup_service.sources", return_value={}):
            self.assertEqual(
                self.client.post("/api/v1/services/mealie/backups", headers=self.headers, json={}).status_code, 409
            )
        with patch("ctl.api.routes.services.backup_service.holding", return_value=True):
            self.assertEqual(
                self.client.post("/api/v1/services/mealie/backups", headers=self.headers, json={}).status_code, 409
            )

    def test_a_core_app_is_not_backed_up_from_an_app_card(self):
        response = self.client.post("/api/v1/services/authentik/backups", headers=self.headers, json={})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
