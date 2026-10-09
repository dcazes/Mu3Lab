"""R12: rollback returns the exact previous deployment. R15: backup status is per app and truthful."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ctl import backups
from ctl.app_settings import AppSettings
from ctl.lifecycle import deployments, maintenance
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.store import records
from tests.test_maintenance import NEW, OLD, _Workflow


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = RuntimePaths(Path(self.temp.name))
        self.paths.state.mkdir(parents=True)
        self.service = load().get("mealie")
        self.project = self.paths.projects / "mealie"
        (self.project / "config").mkdir(parents=True)
        (self.project / "docker-compose.yml").write_text("old compose\n", encoding="utf-8")
        (self.project / "config" / "app.ini").write_text("old=1\n", encoding="utf-8")
        self.settings = AppSettings("mealie", self.paths)
        self.settings.set_config({"currency": "EUR"})
        self.settings.set_generated({"DB_PASSWORD": "secret"})

    def capture(self) -> str:
        return deployments.capture(self.service, "b1", release_id="r1", storage=["mealie"], paths=self.paths)

    def test_restore_returns_every_file_and_setting_and_removes_added_ones(self):
        self.capture()
        (self.project / "docker-compose.yml").write_text("new compose\n", encoding="utf-8")
        (self.project / "docker-compose.nvidia.yml").write_text("added\n", encoding="utf-8")
        self.settings.set_config({"currency": "USD", "added": "x"})
        self.settings.replace_generated({"DB_PASSWORD": "rotated"})
        deployments.restore(self.service, "b1", self.paths)
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "old compose\n")
        self.assertEqual((self.project / "config" / "app.ini").read_text(), "old=1\n")
        self.assertFalse((self.project / "docker-compose.nvidia.yml").exists())
        self.assertEqual(self.settings.config(), {"currency": "EUR"})
        self.assertEqual(self.settings.generated(), {"DB_PASSWORD": "secret"})

    def test_a_damaged_bundle_is_refused_before_anything_changes(self):
        self.capture()
        bundle = self.paths.state / "deployments" / "mealie" / "b1" / "files" / "docker-compose.yml"
        bundle.write_text("tampered\n", encoding="utf-8")
        (self.project / "docker-compose.yml").write_text("new compose\n", encoding="utf-8")
        with self.assertRaisesRegex(deployments.BundleError, "damaged"):
            deployments.restore(self.service, "b1", self.paths)
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "new compose\n")

    def test_bundles_are_private_and_kept_per_release(self):
        self.capture()
        deployments.capture(self.service, "b2", release_id="r1", storage=["mealie"], paths=self.paths)
        deployments.capture(self.service, "b3", release_id="r2", storage=["mealie"], paths=self.paths)
        base = self.paths.state / "deployments" / "mealie"
        self.assertEqual((base / "b1").stat().st_mode & 0o077, 0)
        self.assertEqual(deployments.latest_for_release("mealie", "r1", self.paths), "b2")
        removed = deployments.prune("mealie", keep={"b1"}, paths=self.paths)
        self.assertEqual(removed, [])
        self.assertEqual(sorted(deployments.prune("mealie", keep=set(), paths=self.paths)), ["b1"])


class FullRollbackTests(_Workflow):
    """The new release changes its Compose file and settings; a rollback puts both back."""

    def setUp(self):
        super().setUp()
        (self.project / "docker-compose.yml").write_text("services: {old: {}}\n", encoding="utf-8")
        self.settings = AppSettings("mealie", self.paths)
        self.settings.set_config({"currency": "EUR"})

        def new_definition(*_args):
            (self.project / "docker-compose.yml").write_text("services: {new: {}}\n", encoding="utf-8")
            (self.project / "docker-compose.extra.yml").write_text("services: {}\n", encoding="utf-8")
            self.settings.set_config({"currency": "EUR", "new_field": "on"})

        self.stack.enter_context(patch.object(maintenance, "_refresh_definition", side_effect=new_definition))

    def update(self, starts):
        return self.run_update(starts)

    def test_a_failed_update_returns_the_previous_files_and_settings_too(self):
        job = self.update([(False, "entrypoint changed"), (True, "")])
        self.assertEqual(job["error_code"], "update_rolled_back")
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")
        self.assertFalse((self.project / "docker-compose.extra.yml").exists())
        self.assertEqual(self.settings.config(), {"currency": "EUR"})
        self.assertEqual(self.installed(), OLD)
        self.assertEqual(self.restored, ["c" * 64])

    def test_an_invalid_new_definition_stops_before_the_app_does(self):
        invalid = maintenance._Failed("stage_definition", "definition_invalid", "unknown service key")
        with patch.object(maintenance, "_validate_definition", side_effect=invalid):
            job = self.update([])
        self.assertEqual(job["error_code"], "definition_invalid")
        self.assertIn("Nothing changed", job["detail"])
        self.assertEqual(self.events, [])  # never stopped
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {old: {}}\n")
        self.assertEqual(self.settings.config(), {"currency": "EUR"})
        self.snapshot.assert_not_called()

    def test_a_healthy_update_keeps_the_new_files(self):
        job = self.update([(True, "")])
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual((self.project / "docker-compose.yml").read_text(), "services: {new: {}}\n")
        self.assertEqual(self.installed(), NEW)


class RestoreRollbackTests(_Workflow):
    def test_restored_data_that_does_not_start_is_swapped_back_for_the_data_from_before(self):
        listing = [{"id": "a" * 64, "time": "2026-10-01T12:00:00Z", "version": OLD.version, "paths": []}]
        self.stack.enter_context(patch.object(maintenance.backups, "snapshots", return_value=listing))
        self.stack.enter_context(patch.object(maintenance, "_start", side_effect=[(False, "crash"), (True, "")]))
        job = self.store.create(
            kind="lifecycle", service_id="mealie", action="restore", actor="owner", params={"snapshot_id": "a" * 64}
        )
        maintenance.execute(self.store, None, self.store.get(job["id"]), self.service, self.root)
        record = self.store.get(job["id"])
        self.assertEqual(record["error_code"], "restore_rolled_back")
        self.assertEqual(self.undone, ["a" * 64])
        self.assertIn("nothing was lost", record["detail"])


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = RuntimePaths(Path(self.temp.name))
        self.paths.state.mkdir(parents=True)

    def saved(self, service_id: str, days_ago: float = 0) -> str:
        when = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(timespec="seconds")
        records.put("backup-apps", service_id, {"snapshot_id": "s" * 64, "completed_at": when}, self.paths)
        return when

    def checked(self, *, data: bool = False, ok: bool = True) -> None:
        now = (datetime.now(UTC) + timedelta(seconds=1)).isoformat(timespec="seconds")
        value = {"ok": ok, "structure_checked_at": now, **({"data_checked_at": now} if data else {})}
        records.put("backup", "repository", value, self.paths)

    def test_each_app_reports_only_its_own_evidence(self):
        self.saved("mealie")
        self.checked()
        self.assertEqual(backups.protection("mealie", self.paths)["state"], "checked")
        self.assertEqual(backups.protection("paperless", self.paths)["state"], "missing")

    def test_a_backup_saved_after_the_last_check_is_unchecked(self):
        self.checked()
        records.put(
            "backup-apps",
            "mealie",
            {"snapshot_id": "s" * 64, "completed_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()},
            self.paths,
        )
        self.assertEqual(backups.protection("mealie", self.paths)["state"], "unchecked")

    def test_old_backups_become_stale_and_data_checks_are_reported(self):
        self.saved("mealie", days_ago=backups.STALE_DAYS + 1)
        self.checked(data=True)
        result = backups.protection("mealie", self.paths)
        self.assertEqual((result["state"], result["verification"]), ("stale", "data"))
        self.assertFalse(result["off_device"])

    def test_a_failed_check_is_not_verification(self):
        self.saved("mealie")
        self.checked(ok=False)
        self.assertEqual(backups.protection("mealie", self.paths)["verification"], "none")

    def test_a_restore_that_worked_is_remembered(self):
        self.saved("mealie")
        backups.record_restore_tested("mealie", self.paths)
        self.assertTrue(backups.protection("mealie", self.paths)["restore_tested_at"])

    def test_an_existing_repository_is_never_given_a_new_password(self):
        self.paths.backups.mkdir()
        (self.paths.backups / "config").write_text("restic", encoding="utf-8")
        with self.assertRaisesRegex(backups.BackupError, "password for it is missing"):
            backups._repository_password(self.paths)


if __name__ == "__main__":
    unittest.main()
