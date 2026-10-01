"""An update swaps only an app's own images and survives later lifecycle runs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ctl.compute import compose_overrides
from ctl.lifecycle import image_updates
from ctl.lifecycle.uninstall import _remove_project
from ctl.registry import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.registry = load()

    def plan(self, service_id: str, target: str) -> dict[str, str]:
        return image_updates.plan(self.registry.get(service_id), ROOT, target)

    def test_only_the_apps_own_images_move_to_the_new_release(self):
        immich = self.plan("immich", "v3.3.0")
        self.assertEqual(
            immich,
            {
                "immich-server": "ghcr.io/immich-app/immich-server:v3.3.0",
                "immich-machine-learning": "ghcr.io/immich-app/immich-machine-learning:v3.3.0",
            },
        )

    def test_tag_prefixes_and_suffixes_are_kept(self):
        self.assertEqual(set(self.plan("nextcloud", "v34.0.1").values()), {"nextcloud:34.0.1-apache"})
        self.assertEqual(
            self.plan("baby-buddy", "v2.8.0"), {"baby-buddy": "lscr.io/linuxserver/babybuddy:version-v2.8.0"}
        )
        self.assertEqual(self.plan("mealie", "v3.23.0"), {"mealie": "ghcr.io/mealie-recipes/mealie:v3.23.0"})

    def test_an_app_whose_tags_do_not_follow_its_releases_cannot_be_planned(self):
        # Firecrawl publishes v2.11.0 but tags its image 2.11.334-production.
        self.assertEqual(self.plan("firecrawl", "v2.12.0"), {})

    def test_unsafe_target_tags_are_rejected(self):
        self.assertEqual(self.plan("mealie", "v3.23.0 --privileged"), {})

    def test_versions_compare_numerically(self):
        self.assertTrue(image_updates.is_newer("v3.10.0", "v3.9.9"))
        self.assertTrue(image_updates.is_newer("2026.10.1", "2026.5.0"))
        self.assertFalse(image_updates.is_newer("v3.22.0", "3.22.0"))
        self.assertFalse(image_updates.is_newer("v3.21.0", "v3.22.0"))
        self.assertFalse(image_updates.is_newer("latest", "v1.0.0"))


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.temp.name))
        self.project = self.paths.projects / "mealie"
        self.project.mkdir(parents=True)
        self.service = load().get("mealie")

    def tearDown(self):
        self.temp.cleanup()

    def test_an_override_reports_the_installed_release(self):
        image_updates.write("mealie", "v3.23.0", {"mealie": "ghcr.io/x:v3.23.0@sha256:abc"}, self.paths)
        self.assertEqual(image_updates.installed_version(self.service, self.paths), "v3.23.0")
        document = yaml.safe_load((self.project / image_updates.OVERRIDE).read_text(encoding="utf-8"))
        self.assertEqual(document["services"], {"mealie": {"image": "ghcr.io/x:v3.23.0@sha256:abc"}})

    def test_an_override_mu3lab_has_caught_up_with_is_ignored(self):
        image_updates.write("mealie", "v3.22.0", {"mealie": "ghcr.io/x:v3.22.0@sha256:abc"}, self.paths)
        self.assertIsNone(image_updates.active(self.service, self.paths))
        self.assertEqual(image_updates.installed_version(self.service, self.paths), "v3.22.0")
        self.assertIsNone(image_updates.override_file(self.service, self.paths))

    def test_restore_previous_puts_back_exactly_what_was_there(self):
        image_updates.restore_previous("mealie", None, self.paths)
        self.assertFalse((self.project / image_updates.OVERRIDE).exists())
        before = {"version": "v3.23.0", "images": {"mealie": "ghcr.io/x:v3.23.0@sha256:abc"}}
        image_updates.write("mealie", "v3.24.0", {"mealie": "ghcr.io/x:v3.24.0@sha256:def"}, self.paths)
        image_updates.restore_previous("mealie", before, self.paths)
        self.assertEqual(image_updates.read("mealie", self.paths), before)

    def test_every_lifecycle_command_layers_the_override(self):
        image_updates.write("mealie", "v3.23.0", {"mealie": "ghcr.io/x:v3.23.0@sha256:abc"}, self.paths)
        with (
            patch("ctl.compute.resolved_mode", return_value="cpu"),
            patch("ctl.lifecycle.image_updates.RuntimePaths", return_value=self.paths),
        ):
            self.assertEqual(compose_overrides("mealie", self.project), [self.project / image_updates.OVERRIDE])

    def test_keeping_data_on_uninstall_keeps_the_release_it_was_migrated_to(self):
        (self.project / ".env").write_text("A=1\n", encoding="utf-8")
        (self.project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        image_updates.write("mealie", "v3.23.0", {"mealie": "ghcr.io/x:v3.23.0@sha256:abc"}, self.paths)
        _remove_project(self.project, keep_env=True)
        self.assertEqual(sorted(path.name for path in self.project.iterdir()), [".env", image_updates.OVERRIDE])


class DownloadTests(unittest.TestCase):
    def test_new_images_are_pinned_to_their_digest(self):
        with (
            patch("ctl.lifecycle.image_updates.actions.docker_cmd", return_value=(0, "")) as pull,
            patch(
                "ctl.lifecycle.image_updates.actions.docker_image_digest",
                return_value=(0, "ghcr.io/x@sha256:abc\n"),
            ),
        ):
            pinned = image_updates.download({"app": "ghcr.io/x:v2"}, lambda _line: None)
        self.assertEqual(pinned, {"app": "ghcr.io/x:v2@sha256:abc"})
        self.assertEqual(pull.call_args.args[0], ["docker", "pull", "ghcr.io/x:v2"])

    def test_an_unpublished_image_stops_the_update_before_anything_changes(self):
        with (
            patch("ctl.lifecycle.image_updates.actions.docker_cmd", return_value=(1, "manifest unknown")),
            self.assertRaisesRegex(RuntimeError, "not be published yet"),
        ):
            image_updates.download({"app": "ghcr.io/x:v2"}, lambda _line: None)


if __name__ == "__main__":
    unittest.main()
