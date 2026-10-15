"""Rebuilding on a new computer brings Mu3Lab back from its backups, and never overwrites anything."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import backups, install, rebuild
from ctl.bootstrap import terminal
from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.provisioning import ProvisioningStore
from ctl.runtime import RuntimePaths
from ctl.store.secrets import SecretStore

PASSWORD = "correct-horse-battery-staple"


def _old_computer(root: Path) -> RuntimePaths:
    """What the old computer's backups held: Mu3Lab's state, projects, and one app's data."""
    old = RuntimePaths(root)
    old.state.mkdir(parents=True)
    SecretStore(old).put("platform", "backup-password", PASSWORD)
    SecretStore(old).put("app:mealie", "env:SECRET_KEY", "mealie-secret")
    store = JobStore(old.state / "mu3lab.db")
    store.create(kind="backup", service_id="mu3lab-platform", action="backup", actor="schedule")
    store.claim("old-worker")  # the backup that was running while its own copy was taken
    ProvisioningStore(old.state / "mu3lab.db").update("core", "verified")
    ControlState(old.state / "mu3lab.db").set_installation("mealie", "running")
    (old.state / "deployments" / "mealie").mkdir(parents=True)
    (old.state / "deployments" / "mealie" / "bundle.json").write_text("{}")
    (old.projects / "mealie").mkdir(parents=True)
    (old.projects / "mealie" / "docker-compose.yml").write_text(
        'services:\n  mealie:\n    volumes: ["${MU3LAB_DATA_ROOT}/mealie:/data"]\n'
    )
    (old.data / "mealie").mkdir(parents=True)
    (old.data / "mealie" / "recipes.db").write_text("pancakes")
    (old.state / "export.json").write_text("{}")
    return old


LISTING = [
    {"id": "aaaa1111", "startTime": "2026-10-04T07:30:00Z", "tags": {"tag:app": "mu3lab", "tag:folders": "control+projects"}},
    {"id": "aaaa0000", "startTime": "2026-09-27T07:30:00Z", "tags": {"tag:app": "mu3lab", "tag:folders": "control+projects"}},
    {"id": "bbbb1111", "startTime": "2026-10-04T07:40:00Z", "tags": {"tag:app": "mealie", "tag:folders": "mealie"}},
    {"id": "cccc", "startTime": "2026-10-04T07:41:00Z", "tags": {}},
]  # fmt: skip


class FakeKopia:
    """Answers the Kopia commands a rebuild runs, restoring from the old computer's folders."""

    def __init__(self, old: RuntimePaths, *, password: str = PASSWORD):
        self.old, self.password, self.calls = old, password, []
        self.listing = json.loads(json.dumps(LISTING))

    def __call__(self, args, log, *, paths, password="", timeout=0, mounts=()):
        self.calls.append((args, password))
        if password != self.password:
            return 1, "error connecting to repository: invalid repository password"
        if args[:2] == ["repository", "connect"]:
            return 0, "Connected to repository."
        if args[:2] == ["snapshot", "list"]:
            return 0, "progress...\n" + json.dumps(self.listing)
        if args[:2] == ["snapshot", "restore"]:
            _snapshot, folder = re.match(r"(\w+)/(.+)", args[3]).groups()
            source = {"control": self.old.state, "projects": self.old.projects}.get(folder, self.old.data / folder)
            target = mounts[0][0]
            shutil.copytree(source, target, dirs_exist_ok=True)
            return 0, ""
        return 1, f"unexpected {args}"


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.old = _old_computer(base / "old")
        self.new = RuntimePaths(base / "new")
        self.source = self.new.backups / "kopia"  # already copied into place
        self.source.mkdir(parents=True)
        (self.source / rebuild.REPOSITORY_FILE).write_text("{}")
        self.kopia = FakeKopia(self.old)
        self.shell = patch("ctl.rebuild._root_shell", return_value="")
        self.shell.start()
        self.addCleanup(self.shell.stop)
        self.digest = patch("ctl.rebuild._fingerprint", side_effect=self.fingerprint)
        self.digest.start()
        self.addCleanup(self.digest.stop)

    @staticmethod
    def fingerprint(target, _log):
        digest = hashlib.sha256()
        for path in [target, *sorted(target.rglob("*"))] if target.is_dir() else [target]:
            digest.update(str(path.relative_to(target.parent)).encode())
            digest.update(str(path.lstat().st_mode).encode())
            if path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def run_rebuild(self, password: str = PASSWORD):
        with patch("ctl.rebuild.backups._kopia", self.kopia):
            return rebuild.restore(self.source, password, lambda _line: None, self.new)

    def test_mu3lab_its_secrets_and_every_apps_data_come_back(self):
        result = self.run_rebuild()
        self.assertEqual(result["apps"], ["mealie"])
        self.assertEqual(result["platform_backup"], "aaaa1111")  # the newest one
        # The old encryption key opens the old secrets: every app's passwords are back.
        self.assertEqual(SecretStore(self.new).get("app:mealie", "env:SECRET_KEY"), "mealie-secret")
        self.assertEqual(backups.repository_password(self.new), PASSWORD)
        self.assertEqual((self.new.data / "mealie" / "recipes.db").read_text(), "pancakes")
        self.assertTrue((self.new.state / "deployments" / "mealie" / "bundle.json").is_file())
        self.assertTrue((self.new.projects / "mealie" / "docker-compose.yml").is_file())
        self.assertFalse((self.new.state / ".rebuild-control").exists())
        self.assertFalse((self.new.state / rebuild.STARTED).exists())
        self.assertEqual(rebuild.status(self.new)["restored_at"], result["restored_at"])
        # Every command used the typed password: the store did not exist yet.
        self.assertTrue(all(password == PASSWORD for _args, password in self.kopia.calls))

    def test_the_old_computers_unfinished_work_and_setup_progress_do_not_carry_over(self):
        result = self.run_rebuild()
        self.assertEqual(result["cancelled_jobs"], 1)
        store = JobStore(self.new.state / "mu3lab.db")
        self.assertEqual(store.active_jobs(), [])
        self.assertTrue(any(event["event"] == "platform.rebuilt" for event in store.audit()))
        phases = {
            phase["phase_id"]: phase["actual_state"]
            for phase in ProvisioningStore(self.new.state / "mu3lab.db").summary()["phases"]
        }
        self.assertEqual(phases["core"], "pending")

    def test_a_wrong_password_stops_before_anything_is_restored(self):
        with self.assertRaises(rebuild.RebuildError) as error:
            self.run_rebuild("wrong-password")
        self.assertIn("did not accept that password", str(error.exception))
        self.assertFalse((self.new.state / "mu3lab.db").exists())
        self.assertFalse((self.new.state / "secrets.key").exists())
        self.assertTrue(rebuild.interrupted(self.new))

    def test_a_computer_that_already_has_mu3lab_data_is_never_overwritten(self):
        (self.new.data / "immich").mkdir(parents=True)
        (self.new.data / "immich" / "photo.jpg").write_text("x")
        with self.assertRaises(rebuild.RebuildError) as error:
            self.run_rebuild()
        self.assertIn("never overwrites", str(error.exception))
        self.assertEqual(self.kopia.calls, [])

    def test_backups_without_mu3lab_itself_are_refused_with_what_to_do(self):
        self.kopia.listing = [item for item in LISTING if item["tags"].get("tag:app") != "mu3lab"]
        with self.assertRaises(rebuild.RebuildError) as error:
            self.run_rebuild()
        self.assertIn("Back up now", str(error.exception))
        self.assertFalse((self.new.state / "mu3lab.db").exists())

    def test_an_interrupted_rebuild_starts_again_from_a_clean_slate(self):
        with self.assertRaises(rebuild.RebuildError):
            self.run_rebuild("wrong-password")
        with patch("ctl.rebuild._clear_partial") as clear:
            self.run_rebuild()
        clear.assert_called_once()
        self.assertFalse(rebuild.interrupted(self.new))

    def test_clearing_an_interrupted_rebuild_never_deletes_backups_the_owner_put_in_place(self):
        with self.assertRaises(rebuild.RebuildError):
            self.run_rebuild("wrong-password")
        with patch("ctl.rebuild._root_shell") as shell:
            rebuild._clear_partial(self.new, lambda _line: None)
        shell.assert_not_called()  # No restored paths exist after this password failure.
        self.assertTrue(self.source.is_dir())

    def test_changed_owned_files_stop_cleanup_before_any_delete(self):
        with self.assertRaises(rebuild.RebuildError):
            self.run_rebuild("wrong-password")
        target = self.new.data / "mealie"
        rebuild._own(self.new, target)
        target.mkdir()
        (target / "recipes.db").write_text("restored")
        rebuild._seal(self.new, [target], print)
        (target / "personal.txt").write_text("keep me")
        with (
            patch("ctl.rebuild._root_shell") as shell,
            self.assertRaisesRegex(rebuild.RebuildError, "Files were preserved"),
        ):
            rebuild._clear_partial(self.new, print, self.source)
        shell.assert_not_called()
        self.assertEqual((target / "personal.txt").read_text(), "keep me")

    def test_cleanup_names_only_owned_paths_and_preserves_unrelated_data(self):
        with self.assertRaises(rebuild.RebuildError):
            self.run_rebuild("wrong-password")
        target = self.new.data / "mealie"
        rebuild._own(self.new, target)
        target.mkdir()
        rebuild._seal(self.new, [target], print)
        unrelated = self.new.data / "photos"
        unrelated.mkdir()
        with patch("ctl.rebuild._root_shell") as shell:
            rebuild._clear_partial(self.new, print, self.source)
        script = shell.call_args.args[0]
        self.assertEqual(script, "rm -rf -- /runtime/data/mealie")
        self.assertTrue(unrelated.exists())

    def test_source_change_and_uninventoried_restore_never_clean_up(self):
        with self.assertRaises(rebuild.RebuildError):
            self.run_rebuild("wrong-password")
        with self.assertRaisesRegex(rebuild.RebuildError, "source changed"):
            rebuild._clear_partial(self.new, print, self.source.parent)
        target = self.new.data / "mealie"
        rebuild._own(self.new, target)
        target.mkdir()
        with self.assertRaisesRegex(rebuild.RebuildError, "ownership could be verified"):
            rebuild._clear_partial(self.new, print, self.source)
        self.assertTrue(target.exists())

    def test_source_inside_destinations_is_refused_without_deletion(self):
        source = self.new.data / "copied-backups"
        source.mkdir(parents=True)
        (source / rebuild.REPOSITORY_FILE).write_text("keep")
        with self.assertRaisesRegex(rebuild.RebuildError, "overlaps rebuild destinations"):
            rebuild.restore(source, PASSWORD, print, self.new)
        self.assertEqual((source / rebuild.REPOSITORY_FILE).read_text(), "keep")

    def test_replacing_repository_at_the_same_source_path_stops_cleanup(self):
        with self.assertRaises(rebuild.RebuildError):
            self.run_rebuild("wrong-password")
        (self.source / rebuild.REPOSITORY_FILE).write_text("different repository")
        with self.assertRaisesRegex(rebuild.RebuildError, "repository changed"):
            rebuild._clear_partial(self.new, print, self.source)

    def test_corrupt_legacy_and_unsafe_cleanup_markers_preserve_all_files(self):
        self.new.state.mkdir(parents=True)
        for contents in ("{", "{}", "[]", json.dumps({"format": 1, "owned": {"../personal": "abc"}})):
            with self.subTest(contents=contents):
                (self.new.state / rebuild.STARTED).write_text(contents)
                with self.assertRaisesRegex(rebuild.RebuildError, "preserved"):
                    rebuild._clear_partial(self.new, print)

    def test_running_it_again_after_it_finished_changes_nothing(self):
        first = self.run_rebuild()
        calls = len(self.kopia.calls)
        self.assertEqual(self.run_rebuild(), first)
        self.assertEqual(len(self.kopia.calls), calls)


class SourceTests(unittest.TestCase):
    def test_the_backups_folder_is_checked_without_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertIsNone(rebuild.check_source("")[0])
            self.assertIn("not a folder", rebuild.check_source(str(folder / "nope"))[1])
            self.assertIn("does not look like", rebuild.check_source(str(folder))[1])
            (folder / "kopia").mkdir()
            (folder / "kopia" / rebuild.REPOSITORY_FILE).write_text("{}")
            # The parent of the kopia folder works too.
            self.assertEqual(rebuild.check_source(str(folder))[0], folder / "kopia")

    def test_the_password_can_be_typed_in_its_printed_groups(self):
        # Review C-L6: the sheet prints eight groups of eight; spaces between them are dropped.
        password = "abcdefgh" * 8
        grouped = " ".join(password[i : i + 8] for i in range(0, 64, 8))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / rebuild.REPOSITORY_FILE).write_text("{}")
            answers = iter([str(source), grouped, "y"])
            with patch.object(terminal, "_ask", side_effect=lambda *_a: next(answers)):
                asked = terminal.ask_restore(SimpleNamespace(pause=lambda: None, tty=False))
        self.assertEqual(asked["password"], password)

    def test_the_newest_backup_of_each_source_is_used(self):
        newest = rebuild.latest_backups(LISTING)
        self.assertEqual(newest["mu3lab"]["id"], "aaaa1111")
        self.assertEqual(newest["mealie"]["folders"], ["mealie"])
        self.assertNotIn("", newest)

    def test_the_typed_password_reaches_kopia_only_through_its_env_file(self):
        captured = {}

        def docker(command, _log, timeout=0):
            env_file = command[command.index("--env-file") + 1]
            captured["env"] = Path(env_file).read_text()
            captured["command"] = command
            return 0, ""

        with tempfile.TemporaryDirectory() as tmp, patch("ctl.backups.actions.docker_cmd", side_effect=docker):
            backups._run_kopia(
                ["snapshot", "list"], print, mounts=(), paths=RuntimePaths(Path(tmp)), timeout=5, password="typed"
            )
            # No encrypted store was created to look the password up.
            self.assertFalse((Path(tmp) / "state" / "secrets.key").exists())
        self.assertEqual(captured["env"], "KOPIA_PASSWORD=typed\n")
        self.assertNotIn("typed", " ".join(captured["command"]))


class InstallerTests(unittest.TestCase):
    def test_the_step_runs_only_when_asked_and_before_new_secrets_are_made(self):
        order = [step["id"] for step in install.STEPS]
        self.assertLess(order.index("runtime_layout"), order.index("restore_backup"))
        self.assertLess(order.index("restore_backup"), order.index("root_env"))
        self.assertLess(order.index("restore_backup"), order.index("service"))
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("ctl.install.RuntimePaths", return_value=RuntimePaths(Path(tmp))),
        ):
            self.assertEqual(install._restore_check({})["state"], "not_requested")
            self.assertEqual(install._restore_check({"restore": {"source": "x"}})["state"], "missing")
            (Path(tmp) / "state").mkdir()
            (Path(tmp) / "state" / rebuild.STARTED).write_text("{}")
            # A plain ./install.sh never carries on with half the old data.
            check = install._restore_check({})
            self.assertEqual(check["state"], "interrupted")
            self.assertIn("--restore", install.fix_restore_backup(check, {})["error"])

    def test_the_password_is_forgotten_once_the_step_has_used_it(self):
        request = {"source": Path("/b"), "password": PASSWORD}
        ctx = {"restore": request, "log_fn": lambda _id: print, "progress": lambda *_a: None}
        with patch("ctl.rebuild.restore", return_value={"apps": []}), patch("ctl.install._update_progress"):
            self.assertTrue(install.fix_restore_backup({"state": "missing"}, ctx)["ok"])
        self.assertEqual(request["password"], "")

    def test_install_sh_passes_restore_to_the_terminal(self):
        with patch("ctl.bootstrap.terminal.run", return_value=0) as run:
            terminal.main(["--restore"])
            run.assert_called_with(restoring=True)
            terminal.main([])
            run.assert_called_with(restoring=False)
        script = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        self.assertIn("--restore) ;;", script)
        self.assertIn('-m ctl.bootstrap.terminal "$@"', script)


class ResumeTests(unittest.TestCase):
    def test_apps_that_were_running_start_once_core_setup_has_succeeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.state.mkdir(parents=True)
            (paths.projects / "mealie").mkdir(parents=True)
            (paths.projects / "mealie" / "docker-compose.yml").write_text("services: {}\n")
            store = JobStore(paths.state / "mu3lab.db")
            ControlState(paths.state / "mu3lab.db").set_installation("mealie", "running")
            (paths.state / rebuild.MARKER).write_text(json.dumps({"restored_at": "2026-10-09T00:00:00+00:00"}))
            apps = SimpleNamespace(services=[SimpleNamespace(id="mealie", name="Mealie", stage="optional")])
            with patch("ctl.registry.load", return_value=apps):
                self.assertEqual(rebuild.resume(store, Path("/repo"), print, paths), [])  # core not done yet
                core = store.create(kind="lifecycle", service_id="core-suite", action="install", actor="bootstrap")
                store.claim("w")
                store.transition(core["id"], "succeeded", actor="w")
                self.assertEqual(rebuild.resume(store, Path("/repo"), print, paths), ["mealie"])
                self.assertEqual(rebuild.resume(store, Path("/repo"), print, paths), [])  # only once
            started = [job for job in store.active_jobs() if job["service_id"] == "mealie"]
            self.assertEqual([(job["action"], job["actor"]) for job in started], [("start", "rebuild")])

    def test_an_app_busy_with_another_job_is_started_once_that_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.state.mkdir(parents=True)
            (paths.projects / "mealie").mkdir(parents=True)
            (paths.projects / "mealie" / "docker-compose.yml").write_text("services: {}\n")
            store = JobStore(paths.state / "mu3lab.db")
            ControlState(paths.state / "mu3lab.db").set_installation("mealie", "running")
            (paths.state / rebuild.MARKER).write_text(json.dumps({"restored_at": "2026-10-09T00:00:00+00:00"}))
            apps = SimpleNamespace(services=[SimpleNamespace(id="mealie", name="Mealie", stage="optional")])
            core = store.create(kind="lifecycle", service_id="core-suite", action="install", actor="bootstrap")
            store.claim("w")
            store.transition(core["id"], "succeeded", actor="w")
            other = store.create(kind="lifecycle", service_id="mealie", action="stop", actor="owner")
            with patch("ctl.registry.load", return_value=apps):
                self.assertEqual(rebuild.resume(store, Path("/repo"), print, paths), [])  # busy: not forgotten
                self.assertFalse(rebuild.status(paths).get("apps_started"))
                store.claim("w")
                store.transition(other["id"], "succeeded", actor="w")
                self.assertEqual(rebuild.resume(store, Path("/repo"), print, paths), ["mealie"])
                self.assertEqual(rebuild.resume(store, Path("/repo"), print, paths), [])
            self.assertTrue(rebuild.status(paths)["apps_started"])


if __name__ == "__main__":
    unittest.main()
