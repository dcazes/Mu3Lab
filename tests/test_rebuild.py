"""Rebuilding on a new computer brings Mu3Lab back from its backups, and never overwrites anything."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import backups, install, rebuild
from ctl.bootstrap import terminal
from ctl.control_state import ControlState
from ctl.jobs import JobStore
from ctl.runtime import RuntimePaths

PASSWORD = "correct-horse-battery-staple"


LISTING = [
    {"id": "aaaa1111", "startTime": "2026-10-04T07:30:00Z", "tags": {"tag:app": "mu3lab", "tag:folders": "control+projects"}},
    {"id": "aaaa0000", "startTime": "2026-09-27T07:30:00Z", "tags": {"tag:app": "mu3lab", "tag:folders": "control+projects"}},
    {"id": "bbbb1111", "startTime": "2026-10-04T07:40:00Z", "tags": {"tag:app": "mealie", "tag:folders": "mealie"}},
    {"id": "cccc", "startTime": "2026-10-04T07:41:00Z", "tags": {}},
]  # fmt: skip


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
