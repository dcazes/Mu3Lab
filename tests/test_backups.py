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
from ctl.store import records


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
            records.put(
                "backup", "verification", {"snapshot_id": "abc", "integrity_checked_at": "2026-01-01T00:00:00Z"}, paths
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
        self.bookkeeping: list[list[str]] = []

    def tearDown(self):
        self.temp.cleanup()

    def docker(self, outputs):
        def run(argv, _log, timeout=300):
            if backups.RESTIC_IMAGE not in argv:
                # Container bookkeeping: none survive unless a test says so.
                self.bookkeeping.append(argv)
                return outputs.get(argv[1], (0, ""))
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

    def test_restic_containers_are_named_and_labelled(self):
        with self.docker({"snapshots": (0, "[]")}):
            backups.snapshots("mealie", paths=self.paths)
        listing = self.calls[0]
        self.assertIn(backups.CONTAINER_LABEL, listing)
        self.assertTrue(listing[listing.index("--name") + 1].startswith("mu3lab-restic-"))
        # A read-only listing never waits for other restic containers.
        self.assertEqual(self.bookkeeping, [])

    def test_a_restic_container_that_outlives_a_timeout_is_stopped(self):
        with (
            self.docker({"backup": (124, "docker: timed out")}),
            self.assertRaisesRegex(BackupError, "did not complete"),
        ):
            backups.snapshot("mealie", [self.mealie], "manual", lambda _l: None, paths=self.paths)
        name = self.calls[0][self.calls[0].index("--name") + 1]
        self.assertIn(["docker", "stop", "--time", str(backups.STOP_SECONDS), name], self.bookkeeping)

    def test_a_surviving_restic_container_blocks_a_data_change(self):
        with (
            self.docker({"ps": (0, "abc123\n")}),
            patch("ctl.backups.time.sleep"),
            patch("ctl.backups.SURVIVOR_WAIT_SECONDS", 0),
            self.assertRaisesRegex(BackupError, "earlier attempt is still running"),
        ):
            backups.snapshot("mealie", [self.mealie], "manual", lambda _l: None, paths=self.paths)
        self.assertEqual(self.calls, [])

    def test_a_finished_survivor_lets_the_data_change_continue(self):
        answers = iter([(0, "abc123\n"), (0, "")])
        summary = json.dumps({"message_type": "summary", "snapshot_id": "b" * 64})
        outputs = {"backup": (0, summary)}

        def run(argv, _log, timeout=300):
            if backups.RESTIC_IMAGE not in argv:
                return next(answers, (0, ""))
            self.calls.append(argv)
            return outputs.get(argv[argv.index(backups.RESTIC_IMAGE) + 4], (0, ""))

        lines: list[str] = []
        with patch("ctl.backups.actions.docker_cmd", side_effect=run), patch("ctl.backups.time.sleep"):
            backups.snapshot("mealie", [self.mealie], "manual", lines.append, paths=self.paths)
        self.assertIn("still running; waiting", lines[0])
        self.assertEqual(self.calls[0][self.calls[0].index(backups.RESTIC_IMAGE) + 4], "backup")
