"""An app keeps its CPU or GPU build until the owner switches it, and a failed switch puts it back."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import compute
from ctl.lifecycle import app_releases, compute_switch
from ctl.registry import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]


class ComputeSwitchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.paths = RuntimePaths(Path(temp.name))
        self.immich = load().get("immich")
        (self.paths.projects / "immich").mkdir(parents=True)
        # Installed on the CPU build, before the computer got a graphics card.
        app_releases.write("immich", app_releases.approved(self.immich, ROOT, "cpu"), self.paths)
        self.stack = [
            patch("ctl.lifecycle.compute_switch.resolved_mode", return_value="nvidia"),
            patch("ctl.compute.resolved_mode", return_value="nvidia"),
            patch("ctl.lifecycle.app_releases.download"),
        ]
        for item in self.stack:
            item.start()
            self.addCleanup(item.stop)

    def switch(self, started=(True, "")):
        with patch("ctl.lifecycle.compute_switch.app_control.start", return_value=started) as start:
            detail = compute_switch.switch(
                self.immich, ROOT, lambda _line: None, lambda *_args: None, running=True, paths=self.paths
            )
        return detail, start

    def test_a_new_compute_mode_is_offered_not_applied(self):
        project = self.paths.projects / "immich"
        self.assertEqual(compute.app_mode(project, True), "cpu")
        self.assertEqual(compute.compose_overrides("immich", project), [])
        self.assertEqual(compute_switch.info(self.immich, ROOT, self.paths), {"running": "cpu", "wanted": "nvidia"})

    def test_switching_records_the_gpu_build_and_restarts(self):
        detail, start = self.switch()
        start.assert_called_once()
        self.assertIn("NVIDIA GPU version", detail)
        self.assertEqual(app_releases.installed(self.immich, ROOT, self.paths).variant, "nvidia")
        self.assertIn("-cuda@", app_releases.installed(self.immich, ROOT, self.paths).images["immich-machine-learning"])

    def test_a_build_that_does_not_start_is_put_back(self):
        with self.assertRaises(compute_switch.SwitchFailed) as failed:
            self.switch(started=(False, "no GPU visible"))
        self.assertEqual(failed.exception.code, "compute_switch_rolled_back")
        self.assertEqual(app_releases.installed(self.immich, ROOT, self.paths).variant, "cpu")

    def test_apps_without_a_gpu_build_have_nothing_to_switch(self):
        self.assertIsNone(compute_switch.info(load().get("mealie"), ROOT, self.paths))


if __name__ == "__main__":
    unittest.main()
