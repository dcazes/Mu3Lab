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
from ctl.backups import BackupError, Retention, readiness
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
        records.put("backup", "verification", {"snapshot_id": "abc"}, self.paths)
        # A snapshot alone is not enough: a repository check must have passed recently.
        self.assertEqual(readiness(self.paths)["state"], "local_only")
        records.put("backup", "repository", {"ok": True, "structure_checked_at": _now()}, self.paths)
        self.assertEqual(readiness(self.paths)["state"], "verified")

    def test_an_older_restic_repository_is_reported_and_left_alone(self):
        (self.paths.backups / "config").write_text("restic", encoding="utf-8")
        result = readiness(self.paths)
        self.assertEqual(result["legacy_repository"], str(self.paths.backups))
        self.assertFalse(result["repository_present"])
        self.assertEqual((self.paths.backups / "config").read_text(), "restic")


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
        create = next(argv for argv in self.calls if self.command(argv) == "snapshot create")
        self.assertIn(f"{self.mealie}:/data/mealie/mealie:ro", create)
        self.assertIn(f"{recipes}:/data/mealie/mealie-recipes:ro", create)
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertIn("/data/mealie", create)
        for tag in ("app:mealie", "reason:pre-update", "version:v3.22.0", "release:0123456789abcdef"):
            self.assertIn(f"--tags={tag}", create)
        self.assertIn("--tags=folders:mealie+mealie-recipes", create)
        self.assertEqual(
            [self.command(argv) for argv in self.calls],
            ["policy set", "snapshot create", "snapshot verify", "snapshot list"],
        )
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
            backups.snapshot("mealie", [self.mealie], "manual", lambda _l: None, paths=self.paths)

    def test_a_failed_integrity_check_is_not_reported_as_verified(self):
        with (
            self.docker({"snapshot create": (0, _created()), "snapshot verify": (1, "object not found")}),
            self.assertRaisesRegex(BackupError, "integrity check failed"),
        ):
            backups.snapshot("mealie", [self.mealie], "manual", lambda _l: None, paths=self.paths)
        self.assertFalse(records.get("backup", "repository", self.paths)["ok"])

    def test_a_new_repository_gets_mu3labs_identity_and_keeps_everything_for_mu3lab_to_prune(self):
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
        self.assertIn("--keep-latest=1000000", policy)
        self.assertIn("--ignore-cache-dirs=false", policy)

    def test_a_repository_without_local_configuration_is_reconnected_not_recreated(self):
        (self.paths.state / "kopia" / "repository.config").unlink()
        with self.docker({"snapshot list": (0, "[]")}):
            backups.snapshots("mealie", paths=self.paths)
        self.assertEqual([self.command(argv) for argv in self.calls], ["repository connect", "snapshot list"])

    def test_folders_outside_the_data_root_are_refused(self):
        with self.assertRaises(BackupError):
            backups.snapshot("mealie", [Path("/etc")], "manual", lambda _l: None, paths=self.paths)

    def test_restore_stages_beside_the_live_folder_then_swaps(self):
        with self.docker({"snapshot list": (0, _snapshot_listing(["mealie"]))}):
            backups.restore("mealie", SNAP, [self.mealie], lambda _l: None, paths=self.paths, tag="test-tag")
        restore = next(argv for argv in self.calls if self.command(argv) == "snapshot restore")
        mounts = [restore[i + 1] for i, part in enumerate(restore) if part == "-v"]
        self.assertEqual(len(mounts), 3)  # repository, configuration, and this folder's staging place
        self.assertEqual(mounts[-1], f"{self.paths.data}/.mealie.mu3lab-staged-test-tag:/restore")
        self.assertEqual(restore[-2:], [f"{SNAP}/mealie", "/restore"])
        self.assertIn("--no-ignore-permission-errors", restore)
        self.assertEqual(sorted(path.name for path in self.mealie.iterdir()), ["new.db"])
        # Nothing is left beside it once it is in place.
        self.assertEqual([path.name for path in self.paths.data.iterdir()], ["mealie"])

    def test_restore_refuses_another_apps_or_layouts_backup(self):
        for listing in (_snapshot_listing(["paperless"]), _snapshot_listing(["mealie"], app="paperless"), "[]"):
            with (
                self.subTest(listing=listing),
                self.docker({"snapshot list": (0, listing)}),
                self.assertRaises(BackupError),
            ):
                backups.restore("mealie", SNAP, [self.mealie], lambda _l: None, paths=self.paths)
        self.assertFalse(any(self.command(argv) == "snapshot restore" for argv in self.calls))

    def test_snapshots_are_listed_with_their_reason_version_release_and_folders(self):
        with self.docker({"snapshot list": (0, _snapshot_listing(["mealie", "mealie-recipes"]))}):
            listed = backups.snapshots("mealie", paths=self.paths)
        self.assertEqual(
            {key: listed[0][key] for key in ("reason", "version", "release_id", "short_id", "paths")},
            {
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
            backups.snapshot("mealie", [self.mealie], "manual", lambda _l: None, paths=self.paths)
        create = next(argv for argv in self.calls if self.command(argv) == "snapshot create")
        name = create[create.index("--name") + 1]
        self.assertIn(["docker", "stop", "--time", str(backups.STOP_SECONDS), name], self.bookkeeping)

    def test_a_surviving_backup_container_blocks_a_data_change(self):
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
        outputs = {"snapshot create": (0, _created()), "snapshot list": (0, "[]")}

        def run(argv, _log, timeout=300):
            if backups.KOPIA_IMAGE not in argv:
                return next(answers, (0, ""))
            self.calls.append(argv)
            return outputs.get(self.command(argv), (0, ""))

        lines: list[str] = []
        with patch("ctl.backups.actions.docker_cmd", side_effect=run), patch("ctl.backups.time.sleep"):
            backups.snapshot("mealie", [self.mealie], "manual", lines.append, paths=self.paths)
        self.assertIn("still running; waiting", lines[0])
        self.assertEqual([self.command(argv) for argv in self.calls[:2]], ["policy set", "snapshot create"])


class RetentionTests(unittest.TestCase):
    """Mu3Lab applies retention itself: latest 3, then one per day, week and month."""

    @staticmethod
    def listing(times: list[datetime]) -> list[dict]:
        return [{"id": f"{index:032x}", "time": when.isoformat()} for index, when in enumerate(times)]

    def test_the_newest_three_and_one_per_period_are_kept(self):
        now = datetime(2026, 10, 8, 12, tzinfo=UTC)
        times = [now - timedelta(hours=hours) for hours in range(0, 24 * 40, 6)]
        listing = self.listing(times)
        expired = set(backups.expired(listing, Retention()))
        kept = [snap for snap in listing if snap["id"] not in expired]
        self.assertTrue(all(snap["id"] in {item["id"] for item in kept} for snap in listing[:3]))
        days = {snap["time"][:10] for snap in kept}
        self.assertGreaterEqual(len(days), 7)
        self.assertLess(len(kept), len(listing))

    def test_twenty_backups_in_one_day_keep_the_newest_three(self):
        now = datetime(2026, 10, 8, 12, tzinfo=UTC)
        listing = self.listing([now - timedelta(minutes=minutes) for minutes in range(20)])
        self.assertEqual(len(backups.expired(listing, Retention())), 17)

    def test_an_unreadable_time_is_never_a_reason_to_delete(self):
        listing = [*self.listing([datetime(2026, 10, 8, tzinfo=UTC)] * 4), {"id": "f" * 32, "time": "garbage"}]
        self.assertNotIn("f" * 32, backups.expired(listing, Retention()))

    def test_pruning_deletes_only_this_apps_expired_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.state.mkdir()
            _create_repository(paths)
            SecretStore(paths).put("platform", "backup-password", "p")
            listing = [{"id": f"{index:032x}", "time": f"2026-10-08T12:00:0{index}Z"} for index in range(5)]
            calls = []
            with (
                patch("ctl.backups.snapshots", return_value=listing),
                patch("ctl.backups._kopia", side_effect=lambda args, *_a, **_k: calls.append(args) or (0, "")),
            ):
                self.assertTrue(backups.prune("mealie", lambda _l: None, paths=paths))
        self.assertEqual(calls, [["snapshot", "delete", f"{1:032x}", f"{0:032x}", "--delete"]])


class StagedRestoreTests(KopiaTests):
    """R13: a restore never leaves old and new data side by side, whatever stops it."""

    def setUp(self):
        super().setUp()
        self.recipes = self.paths.data / "mealie-recipes"
        self.plans: list[dict] = []

    def plan(self, saved=("/data/mealie",), directories=None):
        with self.docker({"snapshot list": (0, _snapshot_listing([name.removeprefix("/data/") for name in saved]))}):
            return backups.plan_restore(
                "mealie", SNAP, directories or [self.mealie], lambda _l: None, tag="test-tag", paths=self.paths
            )

    def test_a_backup_missing_a_folder_the_app_uses_is_refused(self):
        self.recipes.mkdir()
        (self.recipes / "soup.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(BackupError, "does not include mealie-recipes"):
            self.plan(directories=[self.mealie, self.recipes])

    def test_a_missing_folder_with_no_data_here_either_is_recorded_not_refused(self):
        plan = self.plan(directories=[self.mealie, self.recipes])
        self.assertEqual(plan["not_in_backup"], ["mealie-recipes"])

    def test_a_linked_data_folder_is_refused(self):
        shutil.rmtree(self.mealie)
        os.symlink("/etc", self.mealie)
        with self.assertRaisesRegex(BackupError, "is a link"):
            self.plan()

    def test_a_failed_staging_leaves_the_live_data_untouched(self):
        plan = self.plan()
        with self.docker({"snapshot restore": (1, "no space left on device")}), self.assertRaises(BackupError):
            backups.stage("mealie", plan, lambda _l: None, self.plans.append, paths=self.paths)
        self.assertEqual([path.name for path in self.mealie.iterdir()], ["old.db"])

    def test_an_interrupted_swap_finishes_or_undoes_the_same_way_every_time(self):
        plan = self.plan()
        with self.docker({}):
            plan = backups.stage("mealie", plan, lambda _l: None, self.plans.append, paths=self.paths)
        # Stopped between the two renames: the live folder is aside, the new one not yet in place.
        os.rename(self.mealie, self.paths.data / ".mealie.mu3lab-previous-test-tag")
        swapped = backups.swap(plan, self.plans.append, paths=self.paths)
        self.assertEqual([path.name for path in self.mealie.iterdir()], ["new.db"])
        backups.undo(swapped, self.plans.append, paths=self.paths)
        self.assertEqual([path.name for path in self.mealie.iterdir()], ["old.db"])
        backups.undo(swapped, self.plans.append, paths=self.paths)  # repeating changes nothing
        self.assertEqual([path.name for path in self.mealie.iterdir()], ["old.db"])
        self.assertEqual(self.plans[-1]["roots"], {"mealie": "undone"})
