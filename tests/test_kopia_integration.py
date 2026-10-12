"""Opt-in: Mu3Lab's backup functions against the real pinned Kopia image, including its server.

Run with ``MU3LAB_KOPIA_INTEGRATION=1``. It needs Docker and pulls nothing
beyond ``backups.KOPIA_IMAGE``. Everything happens in a temporary runtime root;
the server listens on a free loopback port.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import backup_service, backups, platform_backup, rebuild
from ctl.lifecycle import in_progress, restore
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.store.secrets import SecretStore

PASSWORD = "integration-test-password"
CONTROL = "integration-control"


def _as_root(paths: RuntimePaths, script: str) -> str:
    """Run a shell snippet as root in the Kopia image, with the runtime root at /r."""
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", "-v", f"{paths.root}:/r",
         backups.KOPIA_IMAGE, "-c", script],
        capture_output=True, text=True, timeout=120, check=True,
    )  # fmt: skip
    return result.stdout.strip()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipUnless(os.environ.get("MU3LAB_KOPIA_INTEGRATION") == "1", "set MU3LAB_KOPIA_INTEGRATION=1")
class KopiaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.temp.name))
        for folder in (self.paths.data, self.paths.backups, self.paths.state, self.paths.runtime, self.paths.projects):
            folder.mkdir(parents=True, exist_ok=True)
        SecretStore(self.paths).put("platform", "backup-password", PASSWORD)
        self.mealie = self.paths.data / "mealie"
        _as_root(
            self.paths,
            "mkdir -p /r/data/mealie/pg && echo original > /r/data/mealie/recipes.db "
            "&& echo rows > /r/data/mealie/pg/data && chown -R 999:999 /r/data/mealie/pg && chmod 700 /r/data/mealie/pg",
        )
        self.lines: list[str] = []
        self.service = load().get("mealie")
        self.server = ""

    def tearDown(self):
        if self.server:
            subprocess.run(["docker", "rm", "-f", self.server], capture_output=True, timeout=60)
        # The repository and restored files belong to root and container users.
        _as_root(self.paths, "rm -rf /r/data /r/backups /r/state/kopia /r/state/platform-export")
        self.temp.cleanup()

    def restore(self, snapshot_id: str) -> str:
        with (
            patch.object(restore, "data_directories", return_value=[self.mealie]),
            patch("ctl.lifecycle.app_control.stop"),
            patch("ctl.lifecycle.app_control.start", return_value=(True, "")),
            patch.object(restore, "_target", return_value=None),  # same release: no images to switch
            patch.object(restore.app_releases, "installed", return_value=None),
            patch.object(restore.app_releases, "remember", return_value=""),
            patch("ctl.lifecycle.deployments.capture", return_value=""),
        ):
            return restore.restore(
                self.service, Path(__file__).resolve().parents[1], snapshot_id, self.lines.append, lambda *_a: None,
                job_id="integration", running=True, paths=self.paths,
            )  # fmt: skip

    def test_an_updates_own_backup_restores_exactly_with_owners_kept(self):
        saved = backups.snapshot(
            "mealie", [self.mealie], "pre-update", self.lines.append, version="v3.22.0", paths=self.paths
        )
        listed = backups.snapshots("mealie", paths=self.paths)
        self.assertEqual(
            [(snap["id"], snap["paths"], snap["version"]) for snap in listed],
            [(saved["snapshot_id"], ["/data/mealie"], "v3.22.0")],
        )
        self.assertGreater(listed[0]["size"], 0)  # what a restore checks free space against
        # Change the live data, then restore: the change and the extra file must go.
        _as_root(self.paths, "echo changed > /r/data/mealie/recipes.db && echo junk > /r/data/mealie/extra")
        self.restore(saved["snapshot_id"])
        self.assertEqual(
            _as_root(self.paths, "cat /r/data/mealie/recipes.db; ls /r/data/mealie; stat -c '%u %a' /r/data/mealie/pg"),
            "original\npg\nrecipes.db\n999 700",
        )
        self.assertEqual(sorted(path.name for path in self.paths.data.iterdir()), ["mealie"])
        self.assertEqual(in_progress.get("mealie", self.paths), {})
        backups.check(self.lines.append, read_data="100%", paths=self.paths)
        self.assertEqual(backups.protection("mealie", self.paths)["verification"], "data")

    def start_server(self) -> int:
        """Kopia's server as the backup service runs it, with mealie and Mu3Lab's own state as sources."""
        backups.ensure_repository(self.lines.append, self.paths)
        export = platform_backup.export_folder(self.paths)
        port = _free_port()
        self.server = f"mu3lab-kopia-it-{port}"
        mounts = [
            f"{self.paths.backups / 'kopia'}:/repo",
            f"{self.mealie}:/data/mealie/mealie:ro",
            f"{export}:/data/mu3lab/control:ro",
            f"{self.paths.projects}:/data/mu3lab/projects:ro",
        ]
        command = (
            "kopia repository connect filesystem --path=/repo --override-username=mu3lab "
            "--override-hostname=mu3lab --enable-actions && exec kopia server start --insecure --enable-actions "
            "--address=0.0.0.0:51515 --disable-file-logging --disable-content-log"
        )
        subprocess.run(
            ["docker", "run", "-d", "--name", self.server, "-p", f"127.0.0.1:{port}:51515",
             "-e", f"KOPIA_PASSWORD={PASSWORD}", "-e", "KOPIA_SERVER_CONTROL_USER=mu3lab-control",
             "-e", f"KOPIA_SERVER_CONTROL_PASSWORD={CONTROL}", "-e", "KOPIA_SERVER_PASSWORD=ui-password",
             "--tmpfs", "/app/config", *(part for mount in mounts for part in ("-v", mount)),
             "--entrypoint", "/bin/sh", backups.KOPIA_IMAGE, "-c", command],
            capture_output=True, text=True, timeout=120, check=True,
        )  # fmt: skip
        return port

    def wait_for(self, source: str, count: int, port: int) -> None:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                item = backup_service.source_status(self.paths).get(source) or {}
                if item.get("status") == "IDLE" and item.get("lastSnapshot") and count <= 1:
                    return
            except OSError:
                pass
            time.sleep(2)
        self.fail(f"Kopia's server did not back up {source}")

    def test_the_server_backs_up_untagged_sources_that_restore_and_rebuild_a_new_computer(self):
        from ctl.jobs import JobStore

        (self.paths.projects / "mealie").mkdir()
        (self.paths.projects / "mealie" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        SecretStore(self.paths).put("app:mealie", "env:SECRET_KEY", "mealie-secret")
        JobStore(self.paths.state / "mu3lab.db").create(
            kind="lifecycle", service_id="mealie", action="start", actor="o"
        )
        port = self.start_server()
        secrets = {"KOPIA_SERVER_CONTROL_PASSWORD": CONTROL}
        with (
            patch.object(backup_service, "ADDRESS", f"http://127.0.0.1:{port}"),
            patch.object(backup_service, "_secrets", return_value=secrets),
        ):
            deadline = time.monotonic() + 60
            while True:
                try:
                    backup_service.source_status(self.paths)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(1)
            # What the worker's upkeep does first: give each source its own policy, so the server knows it.
            for source in ("mealie", "mu3lab"):
                backup_service._exec(self.server, ["policy", "set", f"/data/{source}", "--compression=zstd"], print)
            backup_service._control("POST", "refresh", paths=self.paths)
            # What the before-actions would do: note the release, write Mu3Lab's own state.
            backups.note_release("mealie", "0123456789abcdef", "v3.22.0", ["mealie"], self.paths)
            platform_backup.export_control_plane(self.paths)
            backup_service.trigger(["mealie", "mu3lab"], self.paths)
            self.wait_for("mealie", 1, port)
            self.wait_for("mu3lab", 1, port)
        listed = backups.snapshots("mealie", paths=self.paths)
        self.assertEqual(len(listed), 1)
        self.assertEqual(
            (listed[0]["reason"], listed[0]["version"], listed[0]["paths"]), ("backup", "v3.22.0", ["/data/mealie"])
        )
        self.assertEqual(backups.folders_in(listed[0]["id"], self.lines.append, self.paths), ["mealie"])
        _as_root(self.paths, "echo changed > /r/data/mealie/recipes.db")
        self.restore(listed[0]["id"])
        self.assertEqual(_as_root(self.paths, "cat /r/data/mealie/recipes.db"), "original")

        with tempfile.TemporaryDirectory() as new_root:
            new = RuntimePaths(Path(new_root))
            try:
                result = rebuild.restore(self.paths.backups / "kopia", PASSWORD, self.lines.append, new)
                self.assertEqual(result["apps"], ["mealie"])
                self.assertEqual(SecretStore(new).get("app:mealie", "env:SECRET_KEY"), "mealie-secret")
                self.assertEqual((new.data / "mealie" / "recipes.db").read_text(), "original\n")
                self.assertEqual(_as_root(new, "stat -c '%u %a' /r/data/mealie/pg"), "999 700")
                self.assertTrue((new.projects / "mealie" / "docker-compose.yml").is_file())
                self.assertEqual(JobStore(new.state / "mu3lab.db").active_jobs(), [])
                with self.assertRaises(rebuild.RebuildError):
                    fresh = RuntimePaths(Path(new_root) / "again")
                    rebuild.restore(self.paths.backups / "kopia", "wrong-password", self.lines.append, fresh)
            finally:
                _as_root(new, "rm -rf /r/data /r/backups /r/state /r/projects /r/again")


if __name__ == "__main__":
    unittest.main()
