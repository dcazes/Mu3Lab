"""Kopia backups: status reflects real evidence, and every command keeps the B-milestone guarantees."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ctl import backups
from ctl.backups import BackupError, readiness
from ctl.lifecycle import restore
from ctl.runtime import RuntimePaths
from ctl.store import records
from ctl.store.secrets import SecretStore

SNAP = "a" * 32
NEW = "b" * 32


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _create_repository(paths: RuntimePaths) -> None:
    """What ``kopia repository create`` and a first connect leave on disk."""
    (paths.backups / "kopia").mkdir(parents=True, exist_ok=True)
    (paths.backups / "kopia" / backups.REPOSITORY_MARKER).write_text("kopia", encoding="utf-8")
    (paths.state / "kopia").mkdir(parents=True, exist_ok=True)
    (paths.state / "kopia" / "repository.config").write_text("{}", encoding="utf-8")


class BackupReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = RuntimePaths(Path(self.temp.name))
        self.paths.backups.mkdir()
        self.paths.state.mkdir()
        patcher = patch("ctl.backups.shutil.which", return_value="/usr/bin/docker")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_backup_directory_is_not_configured(self):
        result = readiness(self.paths)
        self.assertEqual((result["state"], result["engine"]), ("not_configured", "kopia"))
        self.assertFalse(result["repository_present"])

    def test_repository_without_successful_check_is_local_only(self):
        _create_repository(self.paths)
        self.assertEqual(readiness(self.paths)["state"], "local_only")

    def test_snapshot_and_integrity_metadata_are_required_for_verified(self):
        _create_repository(self.paths)
        backups.record_backup("mealie", _now(), self.paths)
        # A snapshot alone is not enough: a repository check must have passed recently.
        self.assertEqual(readiness(self.paths)["state"], "local_only")
        records.put("backup", "repository", {"ok": True, "structure_checked_at": _now()}, self.paths)
        self.assertEqual(readiness(self.paths)["state"], "verified")


def _snapshot_listing(folders: list[str], snapshot_id: str = SNAP, app: str = "mealie") -> str:
    """``kopia snapshot list --json``: pretty-printed, after progress lines on stderr."""
    return "Listing snapshots...\n" + json.dumps(
        [
            {
                "id": snapshot_id,
                "source": {"host": "mu3lab", "userName": "mu3lab", "path": f"/data/{app}"},
                "startTime": "2026-10-01T12:00:00.1Z",
                "tags": {
                    "tag:app": app,
                    "tag:reason": "pre-update",
                    "tag:version": "v3.22.0",
                    "tag:release": "0123456789abcdef",
                    "tag:folders": "+".join(folders),
                },
                "rootEntry": {"summ": {"size": 3, "files": 1}},
            }
        ],
        indent=1,
    )


def _created(snapshot_id: str = NEW, failed: int = 0) -> str:
    """``kopia snapshot create --json``."""
    summary = {"size": 3, "files": 3, "numFailed": failed}
    return "Snapshotting mu3lab@mu3lab:/data/mealie ...\n" + json.dumps(
        {"id": snapshot_id, "rootEntry": {"summ": summary}, "tags": {"tag:app": "mealie"}}
    )


class KopiaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.temp.name))
        for path in (self.paths.data, self.paths.backups, self.paths.runtime):
            path.mkdir()
        self.paths.state.mkdir(exist_ok=True)
        _create_repository(self.paths)
        SecretStore(self.paths).put("platform", "backup-password", "test-password")
        self.mealie = self.paths.data / "mealie"
        self.mealie.mkdir()
        (self.mealie / "old.db").write_text("old", encoding="utf-8")
        self.calls: list[list[str]] = []
        self.bookkeeping: list[list[str]] = []
        self.env_files: list[str] = []

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def command(argv: list[str]) -> str:
        index = argv.index(backups.KOPIA_IMAGE)
        return " ".join(argv[index + 1 : index + 3])

    def docker(self, outputs):
        def run(argv, _log, timeout=300):
            if backups.KOPIA_IMAGE not in argv:
                # Container bookkeeping: none survive unless a test says so.
                self.bookkeeping.append(argv)
                return outputs.get(argv[1], (0, ""))
            self.calls.append(argv)
            if "--entrypoint" in argv:  # deleting restore leftovers
                for place in argv[argv.index("--") + 1 :]:
                    shutil.rmtree(self.paths.data / Path(place).name, ignore_errors=True)
                return 0, ""
            if "--env-file" in argv:
                self.env_files.append(Path(argv[argv.index("--env-file") + 1]).read_text())
            command = self.command(argv)
            if command == "snapshot restore" and outputs.get(command, (0, ""))[0] == 0:
                # Kopia restores the folder into the mounted staging folder itself.
                mount = next(part for part in argv if part.endswith(":/restore"))
                (Path(mount.rsplit(":", 1)[0]) / "new.db").write_text("new", encoding="utf-8")
            return outputs.get(command, (0, ""))

        return patch("ctl.backups.actions.docker_cmd", side_effect=run)

    def test_a_snapshot_reads_only_the_apps_folders_and_is_checked(self):
        recipes = self.paths.data / "mealie-recipes"
        recipes.mkdir()
        with self.docker({"snapshot create": (0, _created()), "snapshot list": (0, "[]")}):
            saved = backups.snapshot(
                "mealie",
                [self.mealie, recipes],
                "pre-update",
                lambda _l: None,
                version="v3.22.0",
                release_id="0123456789abcdef",
                paths=self.paths,
            )
        self.assertEqual((saved["snapshot_id"], saved["files"]), (NEW, 3))
        create = self.calls[0]
        self.assertIn(f"{self.mealie}:/data/mealie/mealie:ro", create)
        self.assertIn(f"{recipes}:/data/mealie/mealie-recipes:ro", create)
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertIn("/data/mealie", create)
        for tag in ("app:mealie", "reason:pre-update", "version:v3.22.0", "release:0123456789abcdef"):
            self.assertIn(f"--tags={tag}", create)
        self.assertIn("--tags=folders:mealie+mealie-recipes", create)
        self.assertEqual([self.command(argv) for argv in self.calls], ["snapshot create"])
        self.assertTrue(records.get("backup-apps", "mealie", self.paths)["completed_at"])
        self.assertEqual(readiness(self.paths)["snapshot_present"], True)

    def test_the_password_travels_in_a_private_env_file_never_on_the_command_line(self):
        with self.docker({"snapshot list": (0, "[]")}):
            backups.snapshots("mealie", paths=self.paths)
        self.assertEqual(self.env_files, ["KOPIA_PASSWORD=test-password\n"])
        self.assertFalse(any("test-password" in part for argv in self.calls for part in argv))

    def test_a_snapshot_with_unreadable_files_is_not_a_backup(self):
        with (
            self.docker({"snapshot create": (0, _created(failed=2))}),
            self.assertRaisesRegex(BackupError, "skipped 2 unreadable files"),
        ):
            backups.snapshot("mealie", [self.mealie], "pre-update", lambda _l: None, paths=self.paths)

    def test_only_an_update_makes_its_own_backup(self):
        for reason in ("manual", "scheduled", "pre-restore"):
            with self.subTest(reason=reason), self.assertRaises(BackupError):
                backups.snapshot("mealie", [self.mealie], reason, lambda _l: None, paths=self.paths)
        self.assertEqual(self.calls, [])

    def test_a_new_repository_gets_mu3labs_identity_and_kopias_retention(self):
        shutil.rmtree(self.paths.backups / "kopia")
        shutil.rmtree(self.paths.state / "kopia")

        def create(argv, log, timeout=300):
            if backups.KOPIA_IMAGE in argv and self.command(argv) == "repository create":
                (self.paths.backups / "kopia" / backups.REPOSITORY_MARKER).write_text("kopia", encoding="utf-8")
            return run(argv, log, timeout)

        with self.docker({}) as patched:
            run = patched.side_effect
            patched.side_effect = create
            backups.ensure_repository(lambda _l: None, self.paths)
        created, policy = self.calls
        for flag in ("--override-username", "--override-hostname", "--no-persist-credentials"):
            self.assertIn(flag, created)
        self.assertIn("--keep-latest=10", policy)
        self.assertIn("--keep-monthly=12", policy)
        self.assertIn("--ignore-cache-dirs=false", policy)

    def test_a_repository_without_local_configuration_is_reconnected_not_recreated(self):
        (self.paths.state / "kopia" / "repository.config").unlink()
        with self.docker({"snapshot list": (0, "[]")}):
            backups.snapshots("mealie", paths=self.paths)
        self.assertEqual([self.command(argv) for argv in self.calls], ["repository connect", "snapshot list"])

    def test_folders_outside_the_data_root_are_refused(self):
        with self.assertRaises(BackupError):
            backups.snapshot("mealie", [Path("/etc")], "pre-update", lambda _l: None, paths=self.paths)

    def test_one_folder_is_restored_into_a_new_folder_beside_the_live_one(self):
        staging = self.paths.data / ".mealie.restoring"
        staging.mkdir()
        with self.docker({}):
            backups.restore_folder(SNAP, "mealie", staging, lambda _l: None, self.paths)
        restore_call = self.calls[0]
        mounts = [restore_call[i + 1] for i, part in enumerate(restore_call) if part == "-v"]
        self.assertEqual(mounts[-1], f"{staging}:/restore")
        self.assertEqual(restore_call[-2:], [f"{SNAP}/mealie", "/restore"])
        self.assertIn("--no-ignore-permission-errors", restore_call)
        self.assertEqual(sorted(path.name for path in staging.iterdir()), ["new.db"])
        self.assertEqual(sorted(path.name for path in self.mealie.iterdir()), ["old.db"])

    def test_a_backup_made_by_the_backup_service_is_listed_with_the_release_noted_before_it(self):
        backups.note_release("mealie", "0123456789abcdef", "v3.22.0", ["mealie"], self.paths)
        noted_at = records.get("backup-releases", "mealie", self.paths)["entries"][0]["at"]
        later = (datetime.fromisoformat(noted_at) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
        listing = json.dumps(
            [
                {
                    "id": SNAP,
                    "source": {"host": "mu3lab", "userName": "mu3lab", "path": "/data/mealie"},
                    "startTime": noted_at.replace("+00:00", "Z"),
                    "endTime": later,
                    "stats": {"errorCount": 0},
                },
                {
                    "id": NEW,
                    "source": {"path": "/data/mealie"},
                    "startTime": "2026-01-01T00:00:00Z",
                    "endTime": "2026-01-01T00:00:05Z",
                },
            ]
        )
        with self.docker({"snapshot list": (0, listing)}):
            listed = backups.snapshots("mealie", paths=self.paths)
        newest, oldest = listed
        self.assertEqual(
            (newest["reason"], newest["version"], newest["release_id"], newest["paths"]),
            ("backup", "v3.22.0", "0123456789abcdef", ["/data/mealie"]),
        )
        # A backup older than any note: no release, and its folders are read from the backup itself.
        self.assertEqual((oldest["version"], oldest["paths"]), ("", []))

    def test_the_folders_of_a_backup_are_read_from_the_backup(self):
        output = (
            "drwxrwxr-x  6 2026-10-11 23:12:20 UTC ka5bea870f3ed8614399a2cfc2400561a  mealie-recipes/\n"
            "drwxrwxr-x  6 2026-10-11 23:12:20 UTC k7c1b3113d6554f46fdfef0b7014a7662  mealie/\n"
            "-rw-r--r--  6 2026-10-11 23:12:20 UTC k7c1b3113d6554f46fdfef0b7014a7663  stray.txt\n"
        )
        with patch("ctl.backups._kopia", return_value=(0, output)):
            self.assertEqual(backups.folders_in(SNAP, lambda _l: None, self.paths), ["mealie", "mealie-recipes"])

    def test_snapshots_are_listed_with_their_reason_version_release_and_folders(self):
        with self.docker({"snapshot list": (0, _snapshot_listing(["mealie", "mealie-recipes"]))}):
            listed = backups.snapshots("mealie", paths=self.paths)
        self.assertEqual(
            {key: listed[0][key] for key in ("reason", "version", "release_id", "short_id", "paths", "size")},
            {
                "size": 3,
                "reason": "pre-update",
                "version": "v3.22.0",
                "release_id": "0123456789abcdef",
                "short_id": "aaaaaaaa",
                "paths": ["/data/mealie", "/data/mealie-recipes"],
            },
        )

    def test_backup_containers_are_named_and_labelled(self):
        with self.docker({"snapshot list": (0, "[]")}):
            backups.snapshots("mealie", paths=self.paths)
        listing = self.calls[0]
        self.assertIn(backups.CONTAINER_LABEL, listing)
        self.assertTrue(listing[listing.index("--name") + 1].startswith("mu3lab-kopia-"))
        # A read-only listing never waits for other backup containers.
        self.assertEqual(self.bookkeeping, [])

    def test_a_backup_container_that_outlives_a_timeout_is_stopped(self):
        with (
            self.docker({"snapshot create": (124, "docker: timed out")}),
            self.assertRaisesRegex(BackupError, "did not complete"),
        ):
            backups.snapshot("mealie", [self.mealie], "pre-update", lambda _l: None, paths=self.paths)
        name = self.calls[0][self.calls[0].index("--name") + 1]
        self.assertIn(["docker", "stop", "--time", str(backups.STOP_SECONDS), name], self.bookkeeping)

    def test_a_surviving_backup_container_blocks_a_data_change(self):
        with (
            self.docker({"ps": (0, "abc123\n")}),
            patch("ctl.backups.time.sleep"),
            patch("ctl.backups.SURVIVOR_WAIT_SECONDS", 0),
            self.assertRaisesRegex(BackupError, "earlier attempt is still running"),
        ):
            backups.snapshot("mealie", [self.mealie], "pre-update", lambda _l: None, paths=self.paths)
        self.assertEqual(self.calls, [])

    def test_a_finished_survivor_lets_the_data_change_continue(self):
        answers = iter([(0, "abc123\n"), (0, "")])
        outputs = {"snapshot create": (0, _created()), "snapshot list": (0, "[]")}

        def run(argv, _log, timeout=300):
            if backups.KOPIA_IMAGE not in argv:
                return next(answers, (0, ""))
            self.calls.append(argv)
            return outputs.get(self.command(argv), (0, ""))

        lines: list[str] = []
        with patch("ctl.backups.actions.docker_cmd", side_effect=run), patch("ctl.backups.time.sleep"):
            backups.snapshot("mealie", [self.mealie], "pre-update", lines.append, paths=self.paths)
        self.assertIn("still running; waiting", lines[0])
        self.assertEqual(self.command(self.calls[0]), "snapshot create")


class RestorePlanTests(unittest.TestCase):
    """R13: a restore never leaves old and new data side by side."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.paths = RuntimePaths(Path(temp.name))
        self.paths.data.mkdir(parents=True)
        self.mealie = self.paths.data / "mealie"
        self.mealie.mkdir()
        self.recipes = self.paths.data / "mealie-recipes"

    def plan(self, saved=("/data/mealie",), directories=None):
        return restore.plan({"paths": list(saved)}, directories or [self.mealie], self.paths)

    def test_a_backup_of_another_layout_is_refused(self):
        for saved in (("/data/paperless",), ()):
            with self.subTest(saved=saved), self.assertRaisesRegex(restore.RestoreFailed, "different folder layout"):
                self.plan(saved)

    def test_a_backup_missing_a_folder_the_app_uses_is_refused(self):
        self.recipes.mkdir()
        (self.recipes / "soup.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(restore.RestoreFailed, "does not include mealie-recipes"):
            self.plan(directories=[self.mealie, self.recipes])

    def test_a_missing_folder_with_no_data_here_either_is_allowed(self):
        names, absent = self.plan(directories=[self.mealie, self.recipes])
        self.assertEqual((names, absent), (["mealie"], []))

    def test_a_folder_in_the_backup_that_does_not_exist_yet_is_noted(self):
        names, absent = self.plan(("/data/mealie", "/data/mealie-recipes"), [self.mealie, self.recipes])
        self.assertEqual((names, absent), (["mealie", "mealie-recipes"], ["mealie-recipes"]))

    def test_a_linked_data_folder_is_refused(self):
        shutil.rmtree(self.mealie)
        os.symlink("/etc", self.mealie)
        with self.assertRaisesRegex(BackupError, "is a link"):
            self.plan()


if __name__ == "__main__":
    unittest.main()
