"""Freeing the disk that removed apps and old app versions hold, and nothing else."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import disk_cleanup
from ctl.backups import KOPIA_IMAGE
from ctl.lifecycle.app_releases import Release
from ctl.registry import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
OLD = "ghcr.io/immich-app/immich-server:v1@sha256:old"
PREVIOUS = "ghcr.io/immich-app/immich-server:v2@sha256:previous"
CURRENT = "ghcr.io/immich-app/immich-server:v3@sha256:current"


def result(stdout: str = "", returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name))
        self.paths.projects.mkdir()
        self.paths.data.mkdir()
        (self.paths.projects / "immich").mkdir()
        removed = self.paths.projects / "unsloth"
        removed.mkdir()
        (removed / "docker-compose.yml").write_text(
            "services:\n  studio:\n    image: unsloth/unsloth:v1@sha256:unsloth\n"
        )
        (self.paths.data / "unsloth-models").mkdir()
        (self.paths.data / "immich").mkdir()
        history = {
            "releases": {
                release.id: release.as_dict()
                for release in (Release("1", {"server": OLD}), Release("2", {"server": PREVIOUS}))
            }
        }
        patchers = [
            patch("ctl.store.records.get", side_effect=lambda scope, name, paths: history if name == "immich" else {}),
            patch(
                "ctl.lifecycle.app_releases.installed",
                side_effect=lambda service, root, paths: (
                    Release("3", {"server": CURRENT}) if service.id == "immich" else None
                ),
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.registry = load()

    def test_earlier_versions_and_removed_apps_are_reclaimable_but_current_and_helpers_stay(self):
        found = disk_cleanup.reclaimable(self.registry, ROOT, self.paths)
        self.assertIn("sha256:old", found)
        self.assertIn("sha256:unsloth", found)
        self.assertNotIn("sha256:current", found)
        # Going back downloads the earlier release again, so it is not kept.
        self.assertIn("sha256:previous", found)
        self.assertNotIn(disk_cleanup.identity(KOPIA_IMAGE), found)

    def test_only_unused_reclaimable_images_are_removed_and_never_the_owners_own(self):
        images = (
            "sha256:old\tghcr.io/immich-app/immich-server:<none>\nsha256:mine\tmy/own:latest\nsha256:busy\tbusy:1\n"
        )
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            if argv[:2] == ["docker", "images"]:
                return result(images)
            if argv[:2] == ["docker", "ps"] and "{{.Image}}" in argv:
                return result("busy:1\n")
            return result()

        found = disk_cleanup._unused_images({"sha256:old", "sha256:busy"}, run)
        self.assertEqual(found, ["sha256:old"])

    def test_free_up_removes_containers_and_images_but_data_only_when_asked(self):
        working = str((self.paths.projects / "unsloth").resolve())
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            if argv[:2] == ["docker", "ps"] and "{{.Image}}" not in argv:
                return result(f"c1\t{working}\nc2\t/elsewhere/mine\n")
            if argv[:2] == ["docker", "images"]:
                return result("sha256:unsloth\tunsloth/unsloth:<none>\n")
            return result()

        with patch("ctl.lifecycle.uninstall.delete_data", return_value=(True, "ok")) as delete:
            done = disk_cleanup.free_up(self.registry, ROOT, lambda _line: None, paths=self.paths, run=run)
            self.assertEqual((done["containers"], done["images"], done["folders"]), (1, 1, []))
            delete.assert_not_called()
            # A container outside Mu3Lab's projects is never removed.
            self.assertIn(["docker", "rm", "--force", "c1"], calls)
            self.assertTrue((self.paths.projects / "unsloth").exists())

            done = disk_cleanup.free_up(
                self.registry, ROOT, lambda _line: None, delete_data=True, paths=self.paths, run=run
            )
        self.assertEqual([path.name for path in delete.call_args.args[0]], ["unsloth-models"])
        self.assertEqual(done["folders"], ["unsloth-models", "projects/unsloth"])
        self.assertFalse((self.paths.projects / "unsloth").exists())

    def test_a_connector_of_an_app_that_is_not_installed_loses_its_containers(self):
        (self.paths.projects / "mcp-firecrawl-official").mkdir()
        (self.paths.projects / "mcp-immich-photo-manager").mkdir()
        (self.paths.projects / "immich" / "docker-compose.yml").write_text("services: {}\n")
        leftover = str((self.paths.projects / "mcp-firecrawl-official").resolve())
        connected = str((self.paths.projects / "mcp-immich-photo-manager").resolve())

        def run(argv, **_kwargs):
            if argv[:2] == ["docker", "ps"]:
                return result(f"c1\t{leftover}\nc2\t{connected}\n")
            return result()

        self.assertEqual(disk_cleanup._removed_containers(self.registry, self.paths, run), ["c1"])
        # Its folder keeps the credentials a reinstall that kept the app's data uses again.
        self.assertNotIn(Path(leftover), disk_cleanup.removed_projects(self.registry, self.paths))

    def test_a_connector_folder_goes_once_its_apps_data_is_gone(self):
        for name in ("mcp-firecrawl-official", "mcp-paperless-community"):
            (self.paths.projects / name).mkdir()
            (self.paths.projects / name / ".env").write_text("TOKEN=issued-by-the-app\n")
        # Paperless-ngx was uninstalled keeping its data, so a reinstall still needs that login.
        (self.paths.data / "paperless").mkdir()
        done = disk_cleanup.free_up(
            self.registry, ROOT, lambda _line: None, paths=self.paths, run=lambda *_a, **_k: result()
        )
        self.assertEqual(done["folders"], ["projects/mcp-firecrawl-official"])
        self.assertFalse((self.paths.projects / "mcp-firecrawl-official").exists())
        self.assertTrue((self.paths.projects / "mcp-paperless-community" / ".env").is_file())

    def test_build_cache_is_cleared_whole_and_reports_what_it_freed(self):
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            return result("ID\tRECLAIMABLE\tSIZE\nabc\ttrue\t1.2GB\nTotal:\t24.41GB\n")

        self.assertEqual(disk_cleanup.clear_build_cache(lambda _line: None, run), "24.41GB")
        self.assertEqual(calls, [["docker", "builder", "prune", "--all", "--force"]])
        self.assertEqual(
            disk_cleanup.clear_build_cache(lambda _line: None, lambda *_a, **_k: result("Total:\t0B\n")), ""
        )
        self.assertTrue(disk_cleanup.builds({"action": "update"}))
        self.assertTrue(disk_cleanup.builds({"action": "enable", "service_id": "mcp:immich-control"}))
        self.assertFalse(disk_cleanup.builds({"action": "start", "service_id": "immich"}))

    def test_nothing_is_removed_while_work_is_running_or_downloading(self):
        idle = SimpleNamespace(active_jobs=list)
        self.assertFalse(disk_cleanup.busy(idle, SimpleNamespace(download_queue=list)))
        self.assertTrue(disk_cleanup.busy(SimpleNamespace(active_jobs=lambda: [{"id": "1"}])))
        self.assertTrue(disk_cleanup.busy(idle, SimpleNamespace(download_queue=lambda: [{"id": "1"}])))


if __name__ == "__main__":
    unittest.main()
