"""The backup browser reads backups only, and Caddy signs in to it after Authentik."""

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
from ctl.rules.reads_backups import ReadsBackups, ReadsBackupsParams
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

ROOT = Path(__file__).resolve().parents[1]


class ReadOnlyTests(unittest.TestCase):
    def test_the_repository_is_mounted_and_opened_read_only(self):
        compose = yaml.safe_load((ROOT / "apps/kopia-ui/docker-compose.yml").read_text(encoding="utf-8"))
        service = compose["services"]["kopia"]
        self.assertIn("${MU3LAB_BACKUPS_ROOT:?MU3LAB_BACKUPS_ROOT is required}/kopia:/repo:ro", service["volumes"])
        command = service["command"][0]
        self.assertIn("repository connect filesystem --path=/repo --readonly", command)
        self.assertIn("server start --readonly", command)
        # Only the restores folder is writable, and no app data or media is mounted.
        writable = [volume for volume in service["volumes"] if not volume.endswith(":ro")]
        self.assertEqual(writable, ["${MU3LAB_BACKUPS_ROOT:?MU3LAB_BACKUPS_ROOT is required}/restores:/restores"])
        self.assertTrue(service["read_only"])
        self.assertEqual(service["ports"], ["127.0.0.1:51515:51515"])
        # Same engine version as Mu3Lab's own backups.
        self.assertEqual(service["image"], backups.KOPIA_IMAGE)
        # No secret on a command line: Kopia reads both from the environment.
        self.assertNotIn("password", command.lower())

    def test_it_is_left_out_of_backups(self):
        app = load_catalog().get("kopia-ui")
        self.assertFalse(app.manifest.backup.include)


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


class ReadsBackupsRuleTests(unittest.TestCase):
    def test_the_app_gets_the_repository_password_and_where_backups_are(self):
        env: dict[str, str] = {}
        ReadsBackups(ReadsBackupsParams()).prepare_env(SimpleNamespace(id="kopia-ui"), env, None)
        paths = RuntimePaths()
        self.assertEqual(env["KOPIA_PASSWORD"], backups.repository_password(paths))
        self.assertEqual(env["MU3LAB_BACKUPS_ROOT"], str(paths.backups))

    def test_the_repository_and_restores_folder_exist_before_it_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.backups.mkdir(parents=True)
            ctx = SimpleNamespace(facts=SimpleNamespace(paths=paths), log=lambda _line: None, fail=None)
            with patch("ctl.rules.reads_backups.backups.ensure_repository") as ensure:
                ReadsBackups(ReadsBackupsParams()).before_start(ctx)
            ensure.assert_called_once_with(ctx.log, paths)
            self.assertTrue((paths.backups / "restores").is_dir())

    def test_a_repository_that_cannot_be_opened_stops_the_install(self):
        failed = []
        ctx = SimpleNamespace(
            facts=SimpleNamespace(paths=RuntimePaths(Path("/nonexistent"))),
            log=lambda _line: None,
            fail=lambda *args: failed.append(args) or (_ for _ in ()).throw(RuntimeError("stop")),
        )
        with (
            patch("ctl.rules.reads_backups.backups.ensure_repository", side_effect=backups.BackupError("no")),
            self.assertRaises(RuntimeError),
        ):
            ReadsBackups(ReadsBackupsParams()).before_start(ctx)
        self.assertEqual(failed[0][1], "backup_repository_unavailable")


if __name__ == "__main__":
    unittest.main()
