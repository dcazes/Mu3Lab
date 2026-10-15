"""Kopia's server is the backup service, reachable on loopback only, and Caddy signs in to it after Authentik."""

from __future__ import annotations

import base64
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from pydantic import ValidationError

from ctl import backups, routes
from ctl.manifest.catalog import load as load_catalog
from ctl.manifest.models import AppManifest
from ctl.registry import load
from ctl.rules.runs_backups import RunsBackups, RunsBackupsParams
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

ROOT = Path(__file__).resolve().parents[1]


class BackupServiceTests(unittest.TestCase):
    def setUp(self):
        compose = yaml.safe_load((ROOT / "apps/kopia-ui/docker-compose.yml").read_text(encoding="utf-8"))
        self.service = compose["services"]["kopia"]
        self.command = self.service["command"][0]

    def test_it_listens_on_loopback_only_so_its_actions_can_reach_mu3lab(self):
        self.assertEqual(self.service["network_mode"], "host")
        self.assertNotIn("ports", self.service)
        self.assertIn("--address=127.0.0.1:51515", self.command)

    def test_it_runs_actions_and_keeps_its_cache_on_disk(self):
        self.assertIn("--enable-actions", self.command.split("&&")[0])  # the connection allows actions
        self.assertIn("server start --insecure --enable-actions", self.command)
        self.assertIn(
            "${MU3LAB_STATE_ROOT:?MU3LAB_STATE_ROOT is required}/kopia-server:/cache", self.service["volumes"]
        )
        self.assertIn("./backup-hook.sh:/hooks/backup-hook.sh:ro", self.service["volumes"])
        self.assertIn("/etc/localtime:/etc/localtime:ro", self.service["volumes"])

    def test_no_app_data_is_mounted_here_and_no_secret_is_on_a_command_line(self):
        # App folders come from docker-compose.sources.yml, read-only, written by ctl.backup_service.
        self.assertFalse(any(volume.startswith("/srv/mu3lab/data") for volume in self.service["volumes"]))
        self.assertTrue(self.service["read_only"])
        self.assertEqual(self.service["image"], backups.KOPIA_IMAGE)
        self.assertNotIn("password", self.command.lower())

    def test_the_hook_only_posts_to_mu3labs_loopback_api_with_its_token(self):
        hook = (ROOT / "apps/kopia-ui/backup-hook.sh").read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:8787/api/v1/backup-hooks/${KOPIA_ACTION}", hook)
        self.assertIn("Authorization: Bearer ${MU3LAB_BACKUP_HOOK_TOKEN}", hook)
        self.assertTrue((ROOT / "apps/kopia-ui/backup-hook.sh").stat().st_mode & stat.S_IXUSR)

    def test_it_is_left_out_of_backups_and_generates_its_own_tokens(self):
        app = load_catalog().get("kopia-ui")
        self.assertFalse(app.manifest.backup.include)
        generated = {secret.env for secret in app.manifest.secrets}
        self.assertEqual(
            generated, {"KOPIA_SERVER_PASSWORD", "KOPIA_SERVER_CONTROL_PASSWORD", "MU3LAB_BACKUP_HOOK_TOKEN"}
        )


class UpstreamAuthTests(unittest.TestCase):
    def test_caddy_adds_the_login_only_after_authentik_and_from_the_environment(self):
        block = routes._block(load().get("kopia-ui"))
        gate, upstream = block.split("reverse_proxy 127.0.0.1:51515", 1)
        self.assertIn("forward_auth", gate)
        self.assertIn('header_up Authorization "Basic {env.MU3LAB_UPSTREAM_AUTH_KOPIA_UI}"', upstream)

    def test_the_login_file_is_private_and_holds_the_basic_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            (paths.projects / "kopia-ui").mkdir(parents=True)
            (paths.projects / "kopia-ui" / ".env").write_text(
                "KOPIA_SERVER_USERNAME=mu3lab-ui\nKOPIA_SERVER_PASSWORD=s3cret\n"
            )
            target = routes.write_upstream_auth(load(), paths)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            value = read_runtime_env(target)["MU3LAB_UPSTREAM_AUTH_KOPIA_UI"]
            self.assertEqual(base64.b64decode(value).decode(), "mu3lab-ui:s3cret")

    def test_an_app_not_installed_yet_adds_no_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = routes.write_upstream_auth(load(), RuntimePaths(Path(tmp)))
            self.assertEqual(target.read_text(), "")

    def test_an_open_route_cannot_carry_a_login(self):
        manifest = yaml.safe_load((ROOT / "apps/kopia-ui/app.yaml").read_text(encoding="utf-8"))
        manifest["route"]["access"] = "open"
        manifest["sign_in"] = {"method": "none"}
        with self.assertRaises(ValidationError):
            AppManifest.model_validate(manifest)

    def test_every_start_of_caddy_passes_the_login_file(self):
        compose = yaml.safe_load((ROOT / "apps/ingress/docker-compose.yml").read_text(encoding="utf-8"))
        env_file = compose["services"]["caddy"]["env_file"]
        self.assertEqual(env_file, [{"path": "${MU3LAB_INGRESS_SECRETS:-/dev/null}", "required": False}])
        for source in (ROOT / "ctl/routes.py", ROOT / "ctl/install.py"):
            text = source.read_text(encoding="utf-8")
            self.assertNotIn('"MU3LAB_INGRESS_TOKEN": ingress_token}', text, f"{source.name} starts Caddy directly")
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("ctl.routes.RuntimePaths", return_value=RuntimePaths(Path(tmp))),
        ):
            env = routes.ingress_env(Path("/Caddyfile"), "token", load())
        self.assertEqual(set(env), {"MU3LAB_CADDYFILE", "MU3LAB_INGRESS_TOKEN", "MU3LAB_INGRESS_SECRETS"})


class RunsBackupsRuleTests(unittest.TestCase):
    def test_the_app_gets_the_repository_password_and_where_backups_are(self):
        env: dict[str, str] = {}
        RunsBackups(RunsBackupsParams()).prepare_env(SimpleNamespace(id="kopia-ui"), env, None)
        paths = RuntimePaths()
        self.assertEqual(env["KOPIA_PASSWORD"], backups.repository_password(paths))
        self.assertEqual(env["MU3LAB_BACKUPS_ROOT"], str(paths.backups))
        self.assertEqual(env["MU3LAB_STATE_ROOT"], str(paths.state))

    def test_the_repository_and_restores_folder_exist_before_it_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.backups.mkdir(parents=True)
            paths.state.mkdir(parents=True)
            ctx = SimpleNamespace(facts=SimpleNamespace(paths=paths), log=lambda _line: None, fail=None)
            with patch("ctl.rules.runs_backups.backups.ensure_repository") as ensure:
                RunsBackups(RunsBackupsParams()).before_start(ctx)
            ensure.assert_called_once_with(ctx.log, paths)
            self.assertTrue((paths.backups / "restores").is_dir())
            self.assertTrue((paths.state / "kopia-server").is_dir())
            self.assertTrue((paths.state / "platform-export").is_dir())

    def test_a_repository_that_cannot_be_opened_stops_the_install(self):
        failed = []
        ctx = SimpleNamespace(
            facts=SimpleNamespace(paths=RuntimePaths(Path("/nonexistent"))),
            log=lambda _line: None,
            fail=lambda *args: failed.append(args) or (_ for _ in ()).throw(RuntimeError("stop")),
        )
        with (
            patch("ctl.rules.runs_backups.backups.ensure_repository", side_effect=backups.BackupError("no")),
            self.assertRaises(RuntimeError),
        ):
            RunsBackups(RunsBackupsParams()).before_start(ctx)
        self.assertEqual(failed[0][1], "backup_repository_unavailable")


if __name__ == "__main__":
    unittest.main()
