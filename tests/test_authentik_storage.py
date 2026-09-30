"""Authentik's data moves into Mu3Lab's data folder without risking sign-in."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.lifecycle import authentik_storage
from ctl.runtime import RuntimePaths


class AuthentikStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = RuntimePaths(Path(self.tmp.name))
        self.project = Path(self.tmp.name) / "core" / "authentik"

    def test_old_volumes_mean_a_move_is_needed_until_it_is_recorded(self):
        with patch("ctl.lifecycle.authentik_storage._volume_exists", return_value=True):
            self.assertEqual(authentik_storage.status(self.paths), "needs_migration")
            authentik_storage.mark_fresh(self.paths)
            self.assertEqual(authentik_storage.status(self.paths), "ready")

    def test_a_fresh_install_needs_no_move(self):
        with patch("ctl.lifecycle.authentik_storage._volume_exists", return_value=False):
            self.assertEqual(authentik_storage.status(self.paths), "ready")

    def _migrate(self, helper_results, up_results=((0, ""),)):
        with (
            patch("ctl.lifecycle.authentik_storage._volume_exists", return_value=True),
            patch("ctl.lifecycle.authentik_storage.actions.compose_action", return_value=(0, "")),
            patch("ctl.lifecycle.authentik_storage._helper", side_effect=list(helper_results)),
            patch("ctl.lifecycle.authentik_storage.actions.compose_up", side_effect=list(up_results)) as up,
        ):
            ok, detail = authentik_storage.migrate(self.project, {}, lambda _line: None, self.paths)
        return ok, detail, up

    def test_a_verified_copy_is_recorded_and_started_on_the_new_folder(self):
        same = (0, "abc -\n42\n---\nabc -\n42\n")
        ok, _detail, up = self._migrate([(0, ""), same] * 3)
        self.assertTrue(ok)
        self.assertEqual(authentik_storage.status(self.paths), "ready")
        self.assertNotIn("extra_files", up.call_args.kwargs)

    def test_a_mismatched_copy_restarts_authentik_on_its_original_volumes(self):
        different = (0, "abc -\n42\n---\nxyz -\n41\n")
        ok, detail, up = self._migrate([(0, ""), different])
        self.assertFalse(ok)
        self.assertIn("did not match", detail)
        self.assertEqual(up.call_args.kwargs["extra_files"], [self.project / authentik_storage.LEGACY_OVERRIDE])
        with patch("ctl.lifecycle.authentik_storage._volume_exists", return_value=True):
            self.assertEqual(authentik_storage.status(self.paths), "needs_migration")

    def test_failing_to_start_on_the_new_folder_rolls_back(self):
        same = (0, "abc -\n42\n---\nabc -\n42\n")
        ok, _detail, up = self._migrate([(0, ""), same] * 3, up_results=[(1, "unhealthy"), (0, "")])
        self.assertFalse(ok)
        self.assertEqual(up.call_count, 2)
        self.assertFalse((authentik_storage.data_dir(self.paths) / authentik_storage.MARKER).exists())

    def test_the_fallback_override_uses_the_original_volume_names(self):
        import yaml

        root = Path(__file__).resolve().parents[1]
        override = yaml.safe_load((root / "core" / "authentik" / authentik_storage.LEGACY_OVERRIDE).read_text())
        # Compose names volumes <project>_<name>; the project is the "authentik" folder.
        self.assertEqual({f"authentik_{name}" for name in override["volumes"]}, set(authentik_storage.VOLUMES))


if __name__ == "__main__":
    unittest.main()
