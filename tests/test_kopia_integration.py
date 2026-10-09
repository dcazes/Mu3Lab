"""Opt-in: Mu3Lab's backup functions against the real pinned Kopia image.

Run with ``MU3LAB_KOPIA_INTEGRATION=1``. It needs Docker and pulls nothing
beyond ``backups.KOPIA_IMAGE``. Everything happens in a temporary runtime root.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import backups
from ctl.runtime import RuntimePaths
from ctl.store.secrets import SecretStore


def _as_root(paths: RuntimePaths, script: str) -> str:
    """Run a shell snippet as root in the Kopia image, with the runtime root at /r."""
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", "-v", f"{paths.root}:/r",
         backups.KOPIA_IMAGE, "-c", script],
        capture_output=True, text=True, timeout=120, check=True,
    )  # fmt: skip
    return result.stdout.strip()


@unittest.skipUnless(os.environ.get("MU3LAB_KOPIA_INTEGRATION") == "1", "set MU3LAB_KOPIA_INTEGRATION=1")
class KopiaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.temp.name))
        for folder in (self.paths.data, self.paths.backups, self.paths.state, self.paths.runtime):
            folder.mkdir(parents=True, exist_ok=True)
        SecretStore(self.paths).put("platform", "backup-password", "integration-test-password")
        self.mealie = self.paths.data / "mealie"
        _as_root(
            self.paths,
            "mkdir -p /r/data/mealie/pg && echo original > /r/data/mealie/recipes.db "
            "&& echo rows > /r/data/mealie/pg/data && chown -R 999:999 /r/data/mealie/pg && chmod 700 /r/data/mealie/pg",
        )
        self.lines: list[str] = []

    def tearDown(self):
        # The repository and restored files belong to root and container users.
        _as_root(self.paths, "rm -rf /r/data /r/backups /r/state/kopia")
        self.temp.cleanup()

    def test_backup_list_staged_restore_retention_and_check(self):
        saved = backups.snapshot(
            "mealie", [self.mealie], "manual", self.lines.append, version="v3.22.0", paths=self.paths
        )
        self.assertTrue(backups.repository_present(self.paths))
        listed = backups.snapshots("mealie", paths=self.paths)
        self.assertEqual([snap["id"] for snap in listed], [saved["snapshot_id"]])
        self.assertEqual(listed[0]["paths"], ["/data/mealie"])
        self.assertEqual(listed[0]["version"], "v3.22.0")

        # Change the live data, then restore: the change and the extra file must go.
        _as_root(self.paths, "echo changed > /r/data/mealie/recipes.db && echo junk > /r/data/mealie/extra")
        backups.restore("mealie", saved["snapshot_id"], [self.mealie], self.lines.append, paths=self.paths, tag="it-1")
        self.assertEqual(
            _as_root(self.paths, "cat /r/data/mealie/recipes.db; ls /r/data/mealie; stat -c '%u %a' /r/data/mealie/pg"),
            "original\npg\nrecipes.db\n999 700",
        )
        self.assertEqual(sorted(path.name for path in self.paths.data.iterdir()), ["mealie"])

        # Retention: four more today keeps the newest three.
        for _ in range(4):
            backups.snapshot("mealie", [self.mealie], "manual", self.lines.append, prune_old=False, paths=self.paths)
        self.assertEqual(len(backups.snapshots("mealie", paths=self.paths)), 5)
        self.assertTrue(backups.prune("mealie", self.lines.append, paths=self.paths))
        self.assertEqual(len(backups.snapshots("mealie", paths=self.paths)), 3)

        backups.check(self.lines.append, read_data="100%", paths=self.paths)
        backups.maintain(self.lines.append, self.paths)
        self.assertEqual(backups.protection("mealie", self.paths)["verification"], "data")

    def test_mu3labs_own_state_is_backed_up_as_its_own_source(self):
        import sqlite3

        from ctl import platform_backup

        with sqlite3.connect(self.paths.state / "mu3lab.db") as database:
            database.execute("CREATE TABLE people (name TEXT)")
            database.execute("INSERT INTO people VALUES ('owner')")
        # The secret store already created this install's own key; the export carries it.
        self.paths.projects.mkdir()
        (self.paths.projects / "mealie").mkdir()
        (self.paths.projects / "mealie" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        with patch("ctl.platform_backup.RuntimePaths", return_value=self.paths):
            saved = platform_backup._back_up_platform(self.paths, self.lines.append)
        listed = backups.snapshots(backups.PLATFORM_SOURCE, paths=self.paths)
        self.assertEqual([snap["id"] for snap in listed], [saved["snapshot_id"]])
        self.assertEqual(listed[0]["paths"], ["/data/control", "/data/projects"])
        self.assertFalse((self.paths.state / platform_backup.EXPORT).exists())
        self.assertEqual(backups.snapshots("mealie", paths=self.paths), [])


if __name__ == "__main__":
    unittest.main()
