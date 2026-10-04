"""Uninstalling an app undoes its install and deletes data only when asked twice."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from ctl.api import create_app
from ctl.authentik_blueprints import render_removal_blueprint
from ctl.lifecycle.uninstall import data_directories, uninstall_application
from ctl.registry import load
from ctl.runtime import RuntimePaths
from tests.support import runtime_paths

ROOT = Path(__file__).resolve().parents[1]


def _installed(paths: RuntimePaths, service_id: str, data_name: str) -> Path:
    project = paths.projects / service_id
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (project / "docker-compose.digest.yml").write_text("services: {}\n", encoding="utf-8")
    (project / ".env").write_text("NEXTCLOUD_DB_PASSWORD=keep-me\n", encoding="utf-8")
    (paths.data / data_name / "postgres").mkdir(parents=True)
    return project


class UninstallTests(unittest.TestCase):
    def _run(self, paths: RuntimePaths, service_id: str, *, delete: bool, docker_cmd=None):
        stages: list[str] = []
        with ExitStack() as stack:
            stack.enter_context(runtime_paths(paths))
            stack.enter_context(patch("ctl.lifecycle.uninstall._release_chat_connectors", return_value=True))
            self.authentik = stack.enter_context(patch("ctl.lifecycle.uninstall.Authentik.runtime")).return_value
            calendars = stack.enter_context(patch("ctl.lifecycle.uninstall._disconnect_calendars"))
            down = stack.enter_context(patch("ctl.lifecycle.uninstall.actions.compose_down", return_value=(0, "")))
            stack.enter_context(
                patch("ctl.lifecycle.uninstall.actions.compose_image_list", return_value=(0, ["nextcloud:33"]))
            )
            withdraw = stack.enter_context(patch("ctl.routes.withdraw", return_value=(True, "removed")))
            docker = stack.enter_context(
                patch(
                    "ctl.lifecycle.uninstall.actions.docker_cmd", side_effect=docker_cmd or (lambda *_a, **_k: (0, ""))
                )
            )
            result = uninstall_application(
                load().get(service_id),
                load(),
                ROOT,
                lambda _line: None,
                lambda step, _text: stages.append(step),
                delete=delete,
            )
        self.calendars = calendars
        return result, stages, down, withdraw, docker

    def test_keeping_data_leaves_data_and_database_passwords_for_a_reinstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            project = _installed(paths, "nextcloud", "nextcloud")
            (ok, _stage, detail), stages, down, withdraw, docker = self._run(paths, "nextcloud", delete=False)
            self.assertTrue(ok, detail)
            down.assert_called_once()
            withdraw.assert_called_once()
            docker.assert_not_called()
            self.assertNotIn("delete_data", stages)
            # Calendars are copied into Mu3Lab even when Nextcloud's data is kept.
            self.calendars.assert_called_once()
            # The release record says which release the kept data was migrated to.
            self.assertEqual(sorted(item.name for item in project.iterdir()), [".env", "docker-compose.digest.yml"])
            self.assertTrue((paths.data / "nextcloud" / "postgres").is_dir())
            self.assertIn("state: absent", self.authentik.apply_blueprint.call_args.args[1])

    def test_deleting_data_removes_project_data_and_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            project = _installed(paths, "nextcloud", "nextcloud")
            commands: list[list[str]] = []

            def docker_cmd(argv, _log, **_kwargs):
                commands.append(argv)
                if argv[1] == "run":
                    import shutil

                    shutil.rmtree(paths.data / "nextcloud")
                return 0, ""

            (ok, _stage, detail), stages, *_ = self._run(paths, "nextcloud", delete=True, docker_cmd=docker_cmd)
            self.assertTrue(ok, detail)
            self.assertEqual(stages[-1], "delete_data")
            self.assertFalse(project.exists())
            self.assertFalse((paths.data / "nextcloud").exists())
            run = commands[0]
            self.assertIn("--network", run)
            self.assertIn(f"{paths.data}:/mu3lab-data", run)
            self.assertEqual(run[-1], "/mu3lab-data/nextcloud")
            self.assertEqual(commands[1], ["docker", "image", "rm", "nextcloud:33"])

    def test_data_that_survives_deletion_fails_the_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            _installed(paths, "nextcloud", "nextcloud")
            (ok, stage, _detail), *_ = self._run(paths, "nextcloud", delete=True)
            self.assertFalse(ok)
            self.assertEqual(stage, "delete_data")

    def test_a_second_run_after_containers_are_gone_still_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            (ok, _stage, detail), _stages, down, *_ = self._run(paths, "mealie", delete=False)
            self.assertTrue(ok, detail)
            down.assert_not_called()

    def test_data_folders_come_from_the_apps_own_mounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            with runtime_paths(paths):
                self.assertEqual(data_directories(load().get("paperless-ngx"), ROOT), [paths.data / "paperless"])
                self.assertEqual(data_directories(load().get("nextcloud"), ROOT), [paths.data / "nextcloud"])


class RemovalBlueprintTests(unittest.TestCase):
    def test_oidc_removal_deletes_application_provider_and_claims(self):
        content = render_removal_blueprint("nextcloud", "Nextcloud", oidc=True)
        self.assertEqual(content.count("state: absent"), 4)
        self.assertIn("slug: mu3lab-nextcloud", content)
        self.assertIn("name: Mu3Lab Nextcloud provider", content)
        self.assertNotIn("state: present", content)

    def test_gated_removal_deletes_the_proxy_provider(self):
        content = render_removal_blueprint("baby-buddy", "Baby Buddy", oidc=False)
        self.assertIn("authentik_providers_proxy.proxyprovider", content)
        self.assertNotIn("scopemapping", content)


class UninstallApiTests(unittest.TestCase):
    def test_deleting_data_accepts_mixed_case_app_name(self):
        headers = {
            "x-mu3lab-proxy-token": "token",
            "x-authentik-username": "owner",
            "x-authentik-uid": "subject",
            "x-authentik-email": "owner@example.test",
            "x-authentik-groups": "mu3lab-operators",
            "host": "testserver",
            "origin": "https://testserver",
            "x-mu3lab-csrf": "bound",
        }
        jobs = MagicMock()
        jobs.by_idempotency_key.return_value = None
        jobs.create.return_value = {"id": "delete-job", "state": "queued"}
        with (
            patch("ctl.api.security.ingress_token", return_value="token"),
            patch("ctl.api.security.csrf_token", return_value="bound"),
            patch("ctl.api.routes.services._effective_state", return_value="ready"),
            patch("ctl.api.routes.services.runtime.job_store", return_value=jobs),
            patch("ctl.api.routes.services.ControlState.runtime"),
        ):
            response = TestClient(create_app()).post(
                "/api/v1/services/nextcloud/actions",
                headers=headers,
                json={"action": "uninstall_delete_data", "confirm": "  nExTcLoUd  "},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(jobs.create.call_args.kwargs["action"], "uninstall_delete_data")

    def test_deleting_data_requires_the_app_name(self):
        headers = {
            "x-mu3lab-proxy-token": "token",
            "x-authentik-username": "owner",
            "x-authentik-uid": "subject",
            "x-authentik-email": "owner@example.test",
            "x-authentik-groups": "mu3lab-operators",
            "host": "testserver",
            "origin": "https://testserver",
            "x-mu3lab-csrf": "bound",
        }
        with (
            patch("ctl.api.security.ingress_token", return_value="token"),
            patch("ctl.api.security.csrf_token", return_value="bound"),
        ):
            client = TestClient(create_app())
            for confirm in ("", "nextcloud-wrong", "Mealie"):
                with self.subTest(confirm=confirm):
                    response = client.post(
                        "/api/v1/services/nextcloud/actions",
                        headers=headers,
                        json={"action": "uninstall_delete_data", "confirm": confirm},
                    )
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIn("type Nextcloud", response.text)


if __name__ == "__main__":
    unittest.main()
