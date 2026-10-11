"""C-C1: every start of the sign-in service and the password vault gets what the installer gives them."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.lifecycle import app_control, compose_settings
from ctl.registry import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]


class ComposeSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.paths = RuntimePaths(Path(temp.name))
        self.registry = load()

    def _authentik_env(self, password="db-secret"):
        folder = self.paths.projects / "authentik"
        folder.mkdir(parents=True)
        (folder / ".env").write_text(f"AUTHENTIK_POSTGRESQL__PASSWORD={password}\nAUTHENTIK_SECRET_KEY=k\n")

    def test_authentik_gets_its_settings_file_and_database_password(self):
        self._authentik_env()
        env, _extra = compose_settings.settings(self.registry.get("authentik"), ROOT / "apps/authentik", self.paths)
        self.assertEqual(env["AUTHENTIK_ENV_FILE"], str(self.paths.projects / "authentik" / ".env"))
        self.assertEqual(env["AUTHENTIK_POSTGRESQL__PASSWORD"], "db-secret")
        self.assertEqual(env["MU3LAB_DATA_ROOT"], str(self.paths.data))

    def test_authentik_without_generated_settings_is_refused_rather_than_started_wrong(self):
        with self.assertRaises(RuntimeError):
            compose_settings.settings(self.registry.get("authentik"), ROOT / "apps/authentik", self.paths)

    def test_vaultwarden_keeps_its_private_address(self):
        project = self.paths.projects / "vaultwarden"
        project.mkdir(parents=True)
        (project / "docker-compose.tailnet.yml").write_text("services: {}\n")
        with patch.object(compose_settings, "_tailnet_name", return_value="box.example.ts.net."):
            env, extra = compose_settings.settings(self.registry.get("vaultwarden"), project, self.paths)
        self.assertEqual(env["VAULTWARDEN_DOMAIN"], "https://box.example.ts.net:8443")
        self.assertEqual(env["MU3LAB_VAULT_ENV_FILE"], str(project / ".env"))
        self.assertIn(project / "docker-compose.tailnet.yml", extra)

    def test_the_backup_service_always_sees_every_apps_folders(self):
        project = self.paths.projects / "kopia-ui"
        project.mkdir(parents=True)
        (project / "docker-compose.sources.yml").write_text("services: {}\n")
        _env, extra = compose_settings.settings(self.registry.get("kopia-ui"), project, self.paths)
        self.assertIn(project / "docker-compose.sources.yml", extra)

    def test_other_apps_need_nothing_extra(self):
        env, _extra = compose_settings.settings(self.registry.get("mealie"), self.paths.projects / "mealie", self.paths)
        self.assertEqual(env, {})

    def test_the_start_used_by_backups_and_restores_passes_authentik_its_settings(self):
        self._authentik_env()
        service = self.registry.get("authentik")
        with (
            patch.object(compose_settings, "RuntimePaths", return_value=self.paths),
            patch("ctl.lifecycle.app_control.actions.compose_up", return_value=(0, "")) as up,
            patch("ctl.lifecycle.app_control.wait_healthy", return_value=(True, "")),
            patch("ctl.mcp_ops.sync_application", return_value=True),
        ):
            started, _detail = app_control.start(service, ROOT, lambda _line: None)
        self.assertTrue(started)
        self.assertEqual(up.call_args.args[0], ROOT / "apps/authentik")
        self.assertEqual(up.call_args.kwargs["env"]["AUTHENTIK_POSTGRESQL__PASSWORD"], "db-secret")


if __name__ == "__main__":
    unittest.main()
