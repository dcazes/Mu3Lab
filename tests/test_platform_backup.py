"""R14: Mu3Lab's own state is exported consistently for its backup, and an administrator can start one."""

from __future__ import annotations

import json
import sqlite3
import stat
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import platform_backup, recovery_sheet
from ctl.api import create_app
from ctl.jobs import JobStore
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.store.secrets import SecretStore
from tests import test_maintenance

ROOT = Path(__file__).resolve().parents[1]


class CoreAppTests(unittest.TestCase):
    def test_core_apps_are_ordered_by_dependency_and_downloads_are_left_out(self):
        included, skipped = platform_backup.core_apps(load(), ROOT)
        ids = [service.id for service in included]
        self.assertIn("authentik", ids)
        self.assertIn("vaultwarden", ids)
        self.assertNotIn("ingress", ids)  # no data of its own
        self.assertEqual([service.id for service, _reason in skipped], ["ollama"])
        self.assertIn("downloaded AI models", skipped[0][1])
        for position, service in enumerate(included):
            for dependency in service.dependencies:
                if dependency in ids:
                    self.assertLess(ids.index(dependency), position, f"{dependency} must start before {service.id}")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = RuntimePaths(Path(self.temp.name))
        self.paths.state.mkdir(parents=True)

    def test_the_export_is_a_consistent_private_copy_of_a_live_database(self):
        database = self.paths.state / "mu3lab.db"
        live = sqlite3.connect(database)
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE people (name TEXT)")
        live.execute("INSERT INTO people VALUES ('owner')")
        live.commit()  # still in the WAL, with the connection open
        (self.paths.state / "secrets.key").write_bytes(b"key")
        (self.paths.state / "deployments" / "mealie" / "b1").mkdir(parents=True)
        (self.paths.state / "deployments" / "mealie" / "b1" / "bundle.json").write_text("{}", encoding="utf-8")
        try:
            export = platform_backup.export_control_plane(self.paths)
        finally:
            live.close()
        with sqlite3.connect(export / "mu3lab.db") as copy:
            self.assertEqual(copy.execute("SELECT name FROM people").fetchall(), [("owner",)])
        self.assertEqual((export / "secrets.key").read_bytes(), b"key")
        self.assertTrue((export / "deployments" / "mealie" / "b1" / "bundle.json").is_file())
        self.assertEqual(stat.S_IMODE(export.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((export / "mu3lab.db").stat().st_mode), 0o600)
        manifest = json.loads((export / "export.json").read_text())
        self.assertIn("mu3lab.db", manifest["files"])


class ExportFolderTests(unittest.TestCase):
    def test_the_export_folder_outlives_every_export_and_is_emptied_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.state.mkdir(parents=True)
            (paths.state / "secrets.key").write_bytes(b"key")
            folder = platform_backup.export_folder(paths)
            inode = folder.stat().st_ino
            platform_backup.export_control_plane(paths)
            self.assertTrue((folder / "secrets.key").is_file())
            # The backup service sees it through a bind mount, which follows the folder itself.
            self.assertEqual(platform_backup.export_folder(paths).stat().st_ino, inode)
            platform_backup.clear_export(paths)
            self.assertEqual(list(folder.iterdir()), [])
            self.assertEqual(folder.stat().st_ino, inode)


class PlatformApiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("ctl.api.security.ingress_token", return_value="token"))
        self.stack.enter_context(patch("ctl.api.security.csrf_token", return_value="bound"))
        self.client = TestClient(create_app())
        self.headers = test_maintenance.MaintenanceApiTests.headers

    def test_an_operator_asks_the_backup_service_for_mu3lab_first_then_the_core_apps(self):
        with (
            patch("ctl.api.routes.system.backup_service.sources", return_value={"mu3lab": [], "authentik": []}),
            patch("ctl.api.routes.system.backup_service.trigger") as trigger,
        ):
            response = self.client.post("/api/v1/system/backups/platform", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(trigger.call_args.args[0], ["mu3lab", "authentik"])
        with patch("ctl.api.routes.system.backup_service.trigger", side_effect=OSError("refused")):
            down = self.client.post("/api/v1/system/backups/platform", headers=self.headers, json={})
        self.assertEqual(down.status_code, 503)
        self.assertIn("not running", down.text)
        status = self.client.get("/api/v1/system/backups/platform", headers=self.headers).json()
        self.assertEqual(status["platform"]["state"], "missing")
        self.assertTrue(any(app["id"] == "ollama" and not app["included"] for app in status["core_apps"]))

    def test_the_recovery_sheet_is_for_administrators_never_cached_and_audited(self):
        store = JobStore.runtime()
        assert store is not None
        response = self.client.post("/api/v1/system/backups/recovery-sheet", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        sheet = response.json()
        paths = RuntimePaths()
        self.assertEqual(sheet["password"], SecretStore(paths).get("platform", "backup-password"))
        self.assertEqual(sheet["password_check"], recovery_sheet.password_check(sheet["password"]))
        self.assertEqual(sheet["repository"], str(paths.backups / "kopia"))
        events = [event for event in store.audit() if event["event"] == "backup.recovery_sheet_opened"]
        self.assertTrue(events)
        self.assertNotIn(sheet["password"], str(events))
        status = self.client.get("/api/v1/system/backups/platform", headers=self.headers).json()
        self.assertEqual(status["recovery_sheet"]["opened_at"], sheet["generated_at"])
        # The same password every time: the backups never change their password.
        again = self.client.post("/api/v1/system/backups/recovery-sheet", headers=self.headers, json={}).json()
        self.assertEqual(again["password"], sheet["password"])


class RecoverySheetTests(unittest.TestCase):
    def test_the_check_code_confirms_a_password_without_revealing_it(self):
        code = recovery_sheet.password_check("correct horse")
        self.assertRegex(code, r"^[0-9A-F]{4}-[0-9A-F]{4}$")
        self.assertEqual(code, recovery_sheet.password_check("correct horse"))
        self.assertNotEqual(code, recovery_sheet.password_check("correct hors"))

    def test_status_says_never_until_someone_opens_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.state.mkdir(parents=True)
            self.assertIsNone(recovery_sheet.status(paths))
            recovery_sheet.contents("host.ts.net", "owner", paths)
            self.assertEqual(recovery_sheet.status(paths)["opened_by"], "owner")


if __name__ == "__main__":
    unittest.main()
