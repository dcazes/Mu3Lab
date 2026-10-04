"""Mu3Lab updates itself only from a clean GitHub copy, and undoes a pull it can't finish."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import self_update
from ctl.api import create_app
from ctl.jobs import JobStore


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class _Checkout(unittest.TestCase):
    """An installed copy cloned from a remote, plus the maintainer's copy that pushes to it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(self.remote)], check=True)
        self.maintainer = base / "maintainer"
        subprocess.run(
            ["git", "clone", "--quiet", str(self.remote), str(self.maintainer)], check=True, capture_output=True
        )
        self.commit("first")
        self.copy = base / "installed"
        subprocess.run(["git", "clone", "--quiet", str(self.remote), str(self.copy)], check=True, capture_output=True)
        self_update._cache.clear()

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, message: str) -> None:
        (self.maintainer / "file.txt").write_text(message, encoding="utf-8")
        git(self.maintainer, "add", "file.txt")
        git(self.maintainer, "commit", "--quiet", "-m", message)
        tag = "v1.0." + git(self.maintainer, "rev-list", "--count", "HEAD")
        git(self.maintainer, "tag", tag)
        git(self.maintainer, "push", "--quiet", "--tags", "origin", "HEAD:main")

    def status(self) -> dict:
        return self_update.status(self.copy, refresh=True)


class StatusTests(_Checkout):
    def test_an_up_to_date_copy_has_nothing_to_offer(self):
        result = self.status()
        self.assertFalse(result["available"])
        self.assertEqual(result["blocked_reason"], "")

    def test_new_commits_on_github_are_offered_with_what_changed(self):
        self.commit("Approve Mealie v3.28.0")
        result = self.status()
        self.assertTrue(result["available"])
        self.assertEqual(result["changes"], ["Approve Mealie v3.28.0"])
        self.assertEqual(result["blocked_reason"], "")

    def test_preview_tags_and_untagged_changes_are_not_offered(self):
        (self.maintainer / "file.txt").write_text("preview")
        git(self.maintainer, "add", "file.txt")
        git(self.maintainer, "commit", "--quiet", "-m", "Preview UI")
        git(self.maintainer, "tag", "v9.0.0-rc.1")
        git(self.maintainer, "push", "--quiet", "--tags", "origin", "HEAD:main")
        self.assertFalse(self.status()["available"])

    def test_a_copy_with_its_own_commits_is_updated_by_hand(self):
        self.commit("upstream")
        (self.copy / "local.txt").write_text("x", encoding="utf-8")
        git(self.copy, "add", "local.txt")
        git(self.copy, "commit", "--quiet", "-m", "local")
        self.assertIn("development checkout", self.status()["blocked_reason"])

    def test_a_copy_with_edited_files_is_updated_by_hand(self):
        self.commit("upstream")
        (self.copy / "file.txt").write_text("edited", encoding="utf-8")
        self.assertIn("edited files", self.status()["blocked_reason"])

    def test_a_copy_without_a_github_branch_cannot_update_itself(self):
        git(self.copy, "remote", "remove", "origin")
        self.assertIn("no release remote", self.status()["blocked_reason"])


class UpdateTests(_Checkout):
    def run_update(self, plan: dict, finish: dict | None = None):
        calls: list[str] = []

        def new_code(_root, command, _log):
            calls.append(command)
            return plan if command == "plan" else (finish or {"ok": True})

        store = JobStore(Path(self.temp.name) / "jobs.sqlite3")
        job = store.create(kind="update", service_id="mu3lab", action="self_update", actor="owner")
        with (
            patch.object(self_update, "_new_code", side_effect=new_code),
            patch.object(self_update, "_python_packages"),
            patch("ctl.platform_releases.assets"),
            patch.object(self_update, "restart") as restart,
        ):
            self_update.execute_claimed(store, store.get(job["id"]), "worker", self.copy)
        return store.get(job["id"]), calls, restart

    def test_an_update_pulls_installs_then_restarts(self):
        self.commit("second")
        job, calls, restart = self.run_update({"ok": True, "unattended": ["service"], "terminal": []})
        self.assertEqual(job["state"], "succeeded", job["detail"])
        self.assertEqual((self.copy / "file.txt").read_text(encoding="utf-8"), "second")
        self.assertEqual(calls, ["plan", "finish"])
        restart.assert_called_once()

    def test_an_update_needing_administrator_access_changes_nothing(self):
        before = git(self.copy, "rev-parse", "HEAD")
        self.commit("second")
        job, calls, restart = self.run_update({"ok": True, "unattended": [], "terminal": ["Docker"]})
        self.assertEqual(job["error_code"], "needs_terminal")
        self.assertIn("Docker", job["detail"])
        self.assertIn("./install.sh", job["detail"])
        self.assertEqual(git(self.copy, "rev-parse", "HEAD"), before)
        self.assertEqual(calls, ["plan"])
        restart.assert_not_called()

    def test_an_install_step_that_fails_points_to_the_terminal_without_restarting(self):
        self.commit("second")
        job, _calls, restart = self.run_update(
            {"ok": True, "unattended": [], "terminal": []}, {"ok": False, "error": "npm failed"}
        )
        self.assertEqual(job["error_code"], "install_failed")
        self.assertIn("npm failed", job["detail"])
        restart.assert_not_called()

    def test_nothing_to_do_when_already_up_to_date(self):
        job, calls, restart = self.run_update({"ok": True})
        self.assertEqual(job["state"], "succeeded")
        self.assertIn("already up to date", job["detail"])
        self.assertEqual(calls, [])
        restart.assert_not_called()


class ApiTests(unittest.TestCase):
    headers: ClassVar[dict[str, str]] = {
        "x-mu3lab-proxy-token": "token",
        "x-authentik-username": "owner",
        "x-authentik-uid": "subject",
        "x-authentik-email": "owner@example.test",
        "x-authentik-groups": "mu3lab-operators",
        "host": "testserver",
        "origin": "https://testserver",
        "x-mu3lab-csrf": "bound",
    }

    def setUp(self):
        self.patches = [
            patch("ctl.api.security.ingress_token", return_value="token"),
            patch("ctl.api.security.csrf_token", return_value="bound"),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(create_app())

    def tearDown(self):
        for item in self.patches:
            item.stop()

    def post(self, status: dict, active: list[dict]):
        with (
            patch.object(self_update, "status", return_value=status),
            patch("ctl.api.runtime.job_store") as store,
        ):
            store.return_value.by_idempotency_key.return_value = None
            store.return_value.active_jobs.return_value = active
            store.return_value.create.return_value = {"id": "j", "state": "queued"}
            response = self.client.post("/api/v1/system/update", headers=self.headers, json={})
        return response, store.return_value.create

    def test_the_update_is_queued_for_the_worker(self):
        response, create = self.post({"available": True, "blocked_reason": ""}, [])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(create.call_args.kwargs["service_id"], "mu3lab")

    def test_running_tasks_must_finish_first(self):
        response, create = self.post({"available": True, "blocked_reason": ""}, [{"kind": "lifecycle"}])
        self.assertEqual(response.status_code, 409)
        create.assert_not_called()

    def test_a_copy_updated_by_hand_is_refused(self):
        response, create = self.post({"available": True, "blocked_reason": "This copy has edited files"}, [])
        self.assertEqual(response.status_code, 409)
        self.assertIn("edited files", response.text)
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
