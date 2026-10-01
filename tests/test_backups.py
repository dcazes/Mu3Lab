"""Backup status must reflect actual repository and verification facts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import backups
from ctl.backups import BackupError, readiness
from ctl.runtime import RuntimePaths


class BackupReadinessTests(unittest.TestCase):
    def test_empty_backup_directory_is_not_configured(self):
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.backups.shutil.which", return_value="/usr/bin/restic"):
            paths = RuntimePaths(Path(tmp))
            paths.backups.mkdir()
            result = readiness(paths)
        self.assertEqual(result["state"], "not_configured")
        self.assertFalse(result["repository_present"])

    def test_repository_without_successful_check_is_local_only(self):
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.backups.shutil.which", return_value="/usr/bin/restic"):
            paths = RuntimePaths(Path(tmp))
            paths.backups.mkdir()
            (paths.backups / "config").write_text("restic", encoding="utf-8")
            result = readiness(paths)
        self.assertEqual(result["state"], "local_only")

    def test_snapshot_and_integrity_metadata_are_required_for_verified(self):
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.backups.shutil.which", return_value="/usr/bin/restic"):
            paths = RuntimePaths(Path(tmp))
            paths.backups.mkdir()
            paths.runtime.mkdir()
            (paths.backups / "config").write_text("restic", encoding="utf-8")
            (paths.runtime / "backup-verification.json").write_text(
                json.dumps(
                    {
                        "snapshot_id": "abc",
                        "integrity_checked_at": "2026-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            result = readiness(paths)
        self.assertEqual(result["state"], "verified")


def _snapshot_listing(paths: list[str], snapshot_id: str = "a" * 64) -> str:
    return json.dumps(
        [
            {
                "id": snapshot_id,
                "short_id": snapshot_id[:8],
                "time": "2026-10-01T12:00:00.1+00:00",
                "tags": ["app:mealie", "reason:pre-update", "version:v3.22.0"],
                "paths": paths,
            }
        ]
    )


class ResticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.temp.name))
        for path in (self.paths.data, self.paths.backups, self.paths.runtime):
            path.mkdir()
        (self.paths.backups / "config").write_text("restic", encoding="utf-8")
        self.mealie = self.paths.data / "mealie"
        self.mealie.mkdir()
        self.calls: list[list[str]] = []

    def tearDown(self):
        self.temp.cleanup()

    def docker(self, outputs):
        def run(argv, _log, timeout=300):
            self.calls.append(argv)
            command = argv[argv.index(backups.RESTIC_IMAGE) + 4]
            return outputs.get(command, (0, ""))

        return patch("ctl.backups.actions.docker_cmd", side_effect=run)

    def test_a_snapshot_reads_only_the_apps_folders_and_is_checked(self):
        summary = json.dumps({"message_type": "summary", "snapshot_id": "b" * 64, "total_files_processed": 3})
        with self.docker({"backup": (0, f"warning on stderr\n{summary}")}):
            saved = backups.snapshot(
                "mealie", [self.mealie], "pre-update", lambda _l: None, version="v3.22.0", paths=self.paths
            )
        self.assertEqual(saved["snapshot_id"], "b" * 64)
        backup = self.calls[0]
        self.assertIn(f"{self.mealie}:/data/mealie:ro", backup)
        self.assertIn("none", backup)
        self.assertEqual(backup[-1], "/data/mealie")
        self.assertIn("version:v3.22.0", backup)
        commands = [argv[argv.index(backups.RESTIC_IMAGE) + 4] for argv in self.calls]
        self.assertEqual(commands, ["backup", "check", "forget"])
        self.assertEqual(readiness(self.paths)["snapshot_present"], True)

    def test_a_failed_integrity_check_is_not_reported_as_verified(self):
        summary = json.dumps({"message_type": "summary", "snapshot_id": "b" * 64})
        with (
            self.docker({"backup": (0, summary), "check": (1, "pack damaged")}),
            self.assertRaisesRegex(BackupError, "integrity check failed"),
        ):
            backups.snapshot("mealie", [self.mealie], "manual", lambda _l: None, paths=self.paths)
        self.assertFalse((self.paths.runtime / "backup-verification.json").exists())

    def test_folders_outside_the_data_root_are_refused(self):
        with self.assertRaises(BackupError):
            backups.snapshot("mealie", [Path("/etc")], "manual", lambda _l: None, paths=self.paths)

    def test_restore_replaces_each_folder_in_its_own_run(self):
        with self.docker({"snapshots": (0, _snapshot_listing(["/data/mealie"]))}):
            backups.restore("mealie", "a" * 64, [self.mealie], lambda _l: None, paths=self.paths)
        restore = self.calls[-1]
        self.assertIn(f"{self.mealie}:/data/mealie", restore)
        mounts = [restore[i + 1] for i, part in enumerate(restore) if part == "-v"]
        self.assertEqual(len(mounts), 3)  # repository, password, and this one folder
        self.assertEqual(restore[-5:], ["restore", f"{'a' * 64}:/data/mealie", "--target", "/data/mealie", "--delete"])

    def test_restore_refuses_another_apps_or_layouts_backup(self):
        for listing in (_snapshot_listing(["/data/paperless"]), "[]"):
            with (
                self.subTest(listing=listing),
                self.docker({"snapshots": (0, listing)}),
                self.assertRaises(BackupError),
            ):
                backups.restore("mealie", "a" * 64, [self.mealie], lambda _l: None, paths=self.paths)
        self.assertFalse(any("restore" in argv for argv in self.calls))

    def test_snapshots_are_listed_with_their_reason_and_version(self):
        with self.docker({"snapshots": (0, _snapshot_listing(["/data/mealie"]))}):
            listed = backups.snapshots("mealie", paths=self.paths)
        self.assertEqual(listed[0]["reason"], "pre-update")
        self.assertEqual(listed[0]["version"], "v3.22.0")
        self.assertEqual(listed[0]["short_id"], "aaaaaaaa")
