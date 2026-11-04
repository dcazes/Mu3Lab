"""Apps run the release this machine installed until the person moves them to the approved one."""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ctl.engine.project import _copy_folder
from ctl.lifecycle import app_releases
from ctl.lifecycle.app_releases import Release
from ctl.lifecycle.uninstall import _remove_project
from ctl.registry import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
OLD = "ghcr.io/mealie-recipes/mealie:v3.22.0@sha256:old"
NEW = "ghcr.io/mealie-recipes/mealie:v3.23.0@sha256:new"


class CheckedInApprovalTests(unittest.TestCase):
    def test_every_catalog_app_approves_one_digest_pinned_release(self):
        for service in load().services:
            if service.stage != "optional" or not service.update:
                continue
            with self.subTest(service=service.id):
                release = app_releases.approved(service, ROOT)
                self.assertTrue(release.version)
                self.assertTrue(release.images)
                for image in release.images.values():
                    self.assertIn("@sha256:", image)

    def test_versions_compare_numerically(self):
        self.assertTrue(app_releases.is_newer("v3.10.0", "v3.9.9"))
        self.assertTrue(app_releases.is_newer("2026.10.1", "2026.5.0"))
        self.assertFalse(app_releases.is_newer("v3.22.0", "3.22.0"))
        self.assertFalse(app_releases.is_newer("latest", "v1.0.0"))


class _Machine(unittest.TestCase):
    """A checkout approving ``approved`` and a machine with Mealie installed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "checkout"
        self.paths = RuntimePaths(base / "runtime")
        self.project = self.paths.projects / "mealie"
        self.project.mkdir(parents=True)
        self.approve("v3.22.0", {"mealie": OLD})
        patcher = patch("ctl.lifecycle.app_releases.RuntimePaths", return_value=self.paths)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def approve(self, version: str, images: dict[str, str]) -> None:
        """What `make approve` and a `git pull` of it do to the checkout."""
        definition = self.root / "apps" / "mealie"
        definition.mkdir(parents=True, exist_ok=True)
        compose = {"services": {name: {"image": image} for name, image in images.items()}}
        (definition / "docker-compose.yml").write_text(yaml.safe_dump(compose), encoding="utf-8")
        self.service = dataclasses.replace(
            load().get("mealie"), update={"repository": "mealie-recipes/mealie", "approved_version": version}
        )

    def copy_definition(self) -> None:
        """What materializing the project does."""
        source = self.root / "apps" / "mealie" / "docker-compose.yml"
        (self.project / "docker-compose.yml").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def install(self) -> None:
        self.copy_definition()
        app_releases.pin(self.service, self.root, lambda _line: None)

    def status(self) -> dict:
        return app_releases.status(self.service, self.root)


class InstallTests(_Machine):
    def test_a_new_install_runs_and_records_the_approved_release(self):
        self.install()
        self.assertEqual(app_releases.installed(self.service, self.root), Release("v3.22.0", {"mealie": OLD}))
        self.assertEqual(app_releases.from_history("mealie", "v3.22.0"), Release("v3.22.0", {"mealie": OLD}))
        self.assertFalse(self.status()["update_available"])

    def test_reinstalling_over_kept_data_keeps_the_release_that_data_uses(self):
        self.install()
        (self.project / ".env").write_text("A=1\n", encoding="utf-8")
        _remove_project(self.project, keep_env=True)
        self.approve("v3.23.0", {"mealie": NEW})
        self.install()
        self.assertEqual(app_releases.installed_version(self.service, self.root), "v3.22.0")
        self.assertTrue(self.status()["update_available"])

    def test_unpinned_images_are_resolved_to_their_digest(self):
        self.approve("v3.22.0", {"mealie": "ghcr.io/mealie-recipes/mealie:v3.22.0"})
        with patch("ctl.lifecycle.app_releases.actions.docker_image_digest", return_value=(0, OLD)):
            self.install()
        self.assertEqual(app_releases.installed(self.service, self.root).images, {"mealie": OLD})


class ApprovalTests(_Machine):
    def test_pulling_a_newer_approval_changes_nothing_until_the_person_updates(self):
        self.install()
        self.approve("v3.23.0", {"mealie": NEW})
        self.copy_definition()  # a repair copies the new definition
        app_releases.align(self.service, self.root)
        self.assertEqual(app_releases.installed(self.service, self.root).images, {"mealie": OLD})
        self.assertEqual(
            self.status(),
            {
                "installed_version": "v3.22.0",
                "approved_version": "v3.23.0",
                "update_available": True,
                "supporting_only": False,
                "files_only": False,
                "added_services": [],
                "removed_services": [],
            },
        )

    def test_new_database_images_under_the_same_release_are_offered_too(self):
        self.approve("v3.22.0", {"mealie": OLD, "postgres": "postgres:16@sha256:a"})
        self.install()
        self.approve("v3.22.0", {"mealie": OLD, "postgres": "postgres:16@sha256:b"})
        self.assertTrue(self.status()["update_available"])
        self.assertTrue(self.status()["supporting_only"])

    def test_an_app_already_past_the_approved_release_is_never_moved_back(self):
        self.install()
        app_releases.write("mealie", Release("v3.24.0", {"mealie": "ghcr.io/x:v3.24.0@sha256:c"}))
        self.assertFalse(self.status()["update_available"])

    def test_a_container_the_approved_release_dropped_is_not_brought_back(self):
        self.approve("v3.22.0", {"mealie": OLD, "worker": "ghcr.io/x:w@sha256:w"})
        self.install()
        self.approve("v3.23.0", {"mealie": NEW})
        self.copy_definition()
        app_releases.align(self.service, self.root)
        self.assertEqual(app_releases.installed(self.service, self.root), Release("v3.22.0", {"mealie": OLD}))


class FileChangeTests(_Machine):
    """Mu3Lab changes an app's own files (a voice agent it builds) without a new release of the app."""

    def setUp(self):
        super().setUp()
        self.agent = self.root / "apps" / "mealie" / "agent" / "main.py"
        self.agent.parent.mkdir(parents=True)
        self.agent.write_text("print(1)\n", encoding="utf-8")

    def copy_folder(self) -> None:
        """What installing or updating does with the app's folder."""
        _copy_folder(self.root / "apps" / "mealie", self.project)

    def test_a_changed_file_is_offered_as_an_update_of_the_same_release(self):
        self.copy_folder()
        self.install()
        self.assertFalse(self.status()["update_available"])
        self.agent.write_text("print(22)\n", encoding="utf-8")
        state = self.status()
        self.assertTrue(state["update_available"])
        self.assertTrue(state["files_only"])
        self.assertFalse(state["supporting_only"])
        self.copy_folder()
        self.assertFalse(self.status()["update_available"])

    def test_a_new_file_is_a_change_and_a_file_rewritten_unchanged_is_not(self):
        self.copy_folder()
        self.install()
        self.agent.write_text("print(1)\n", encoding="utf-8")  # a checkout that touched it
        self.assertFalse(self.status()["update_available"])
        (self.agent.parent / "search.py").write_text("", encoding="utf-8")
        self.assertEqual(app_releases.changed_files(self.service, self.root), ["agent/search.py"])

    def test_a_developers_environment_is_neither_copied_nor_compared(self):
        for name in (".venv/lib/site.py", "__pycache__/main.pyc", "node_modules/x/index.js"):
            (self.agent.parent / name).parent.mkdir(parents=True)
            (self.agent.parent / name).write_text("x", encoding="utf-8")
        self.copy_folder()
        self.install()
        self.assertEqual(sorted(p.name for p in (self.project / "agent").iterdir()), ["main.py"])
        self.assertFalse(self.status()["update_available"])

    def test_a_new_release_with_changed_files_is_an_ordinary_update(self):
        self.copy_folder()
        self.install()
        self.agent.write_text("print(22)\n", encoding="utf-8")
        self.approve("v3.23.0", {"mealie": NEW})
        state = self.status()
        self.assertTrue(state["update_available"])
        self.assertFalse(state["files_only"])

    def test_an_app_already_past_the_approved_release_is_not_offered_older_files(self):
        self.copy_folder()
        self.install()
        app_releases.write("mealie", Release("v3.24.0", {"mealie": OLD}))
        self.agent.write_text("print(22)\n", encoding="utf-8")
        self.assertFalse(self.status()["update_available"])


class LegacyRecordTests(_Machine):
    """Records written before they named their release."""

    def legacy(self, image: str) -> None:
        (self.project / app_releases.RECORD).write_text(
            yaml.safe_dump({"services": {"mealie": {"image": image}}}), encoding="utf-8"
        )

    def test_a_record_matching_the_approval_is_that_release(self):
        self.legacy(OLD)
        self.assertEqual(app_releases.installed_version(self.service, self.root), "v3.22.0")

    def test_an_older_record_is_named_from_its_image_tag(self):
        self.approve("v3.23.0", {"mealie": NEW})
        self.legacy(OLD)
        self.assertEqual(app_releases.installed_version(self.service, self.root), "v3.22.0")
        self.assertTrue(self.status()["update_available"])


class RecordTests(_Machine):
    def test_restore_previous_puts_back_exactly_what_was_there(self):
        app_releases.restore_previous("mealie", None)
        self.assertFalse((self.project / app_releases.RECORD).exists())
        before = Release("v3.22.0", {"mealie": OLD})
        app_releases.write("mealie", Release("v3.23.0", {"mealie": NEW}))
        app_releases.restore_previous("mealie", before)
        self.assertEqual(app_releases.installed(self.service, self.root), before)

    def test_keeping_data_on_uninstall_keeps_the_release_record_and_history(self):
        self.install()
        (self.project / ".env").write_text("A=1\n", encoding="utf-8")
        _remove_project(self.project, keep_env=True)
        self.assertEqual(
            sorted(path.name for path in self.project.iterdir()),
            [".env", app_releases.RECORD],
        )

    def test_an_unpublished_image_stops_the_download(self):
        with (
            patch("ctl.lifecycle.app_releases.actions.docker_cmd", return_value=(1, "manifest unknown")),
            self.assertRaisesRegex(RuntimeError, "could not be downloaded"),
        ):
            app_releases.download({"mealie": NEW}, lambda _line: None)


if __name__ == "__main__":
    unittest.main()


class ReleaseIdentityTests(_Machine):
    """R11: the deployment, not its version label, is what history and backups name."""

    PG_A = "postgres:16@sha256:aaa"
    PG_B = "postgres:16@sha256:bbb"

    def test_same_version_with_different_supporting_images_are_two_releases(self):
        first = Release("v3.22.0", {"mealie": OLD, "postgres": self.PG_A})
        second = Release("v3.22.0", {"mealie": OLD, "postgres": self.PG_B})
        self.assertNotEqual(first.id, second.id)
        app_releases.write("mealie", first)
        app_releases.write("mealie", second)
        self.assertEqual(app_releases.from_history("mealie", release_id=first.id), first)
        self.assertEqual(app_releases.from_history("mealie", release_id=second.id), second)
        with self.assertRaises(app_releases.AmbiguousRelease):
            app_releases.from_history("mealie", "v3.22.0")

    def test_the_variant_and_definition_are_part_of_the_identity(self):
        cpu = Release("v1", {"app": OLD})
        self.assertNotEqual(cpu.id, Release("v1", {"app": OLD}, variant="nvidia").id)
        self.assertNotEqual(cpu.id, Release("v1", {"app": OLD}, definition="changed").id)
        self.assertEqual(cpu.id, Release("v2-label-only", {"app": OLD}).id)

    def test_a_gpu_install_records_its_override_images_and_variant(self):
        self.approve("v3.22.0", {"mealie": OLD})
        gpu = {"services": {"mealie": {"image": "ghcr.io/mealie-recipes/mealie:v3.22.0-cuda@sha256:gpu"}}}
        for directory in (self.root / "apps" / "mealie", self.project):
            (directory / "docker-compose.nvidia.yml").write_text(yaml.safe_dump(gpu), encoding="utf-8")
        self.copy_definition()
        app_releases.pin(self.service, self.root, lambda _line: None, gpu_mode="nvidia")
        installed = app_releases.installed(self.service, self.root)
        self.assertEqual(installed.variant, "nvidia")
        self.assertEqual(installed.images["mealie"], gpu["services"]["mealie"]["image"])
        # The approved release for that variant is the same deployment, so nothing is offered.
        self.assertEqual(app_releases.approved(self.service, self.root, "nvidia").id, installed.id)
        self.assertFalse(self.status()["update_available"])

    def test_an_update_that_drops_a_service_says_so(self):
        app_releases.write("mealie", Release("v3.22.0", {"mealie": OLD, "redis": "redis:7@sha256:r"}))
        self.approve("v3.23.0", {"mealie": NEW})
        status = self.status()
        self.assertTrue(status["update_available"])
        self.assertEqual(status["removed_services"], ["redis"])

    def test_history_written_before_release_ids_is_still_read(self):
        from ctl.store import records

        records.put("app-releases", "mealie", {"v3.21.0": {"mealie": OLD}}, self.paths)
        legacy = app_releases.from_history("mealie", "v3.21.0")
        self.assertEqual(legacy, Release("v3.21.0", {"mealie": OLD}))
        self.assertEqual(app_releases.from_history("mealie", release_id=legacy.id), legacy)
