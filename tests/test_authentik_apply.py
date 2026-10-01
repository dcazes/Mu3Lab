"""Mu3Lab applies its Authentik Blueprints itself, removals first."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import authentik_apply
from ctl.runtime import RuntimePaths
from tests.support import runtime_paths


class ApplyBlueprintsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = RuntimePaths(Path(tmp.name))
        (self.paths.projects / "authentik" / "blueprints").mkdir(parents=True)

    def run_apply(self, output: str, rc: int = 0):
        with (
            runtime_paths(self.paths),
            patch.object(authentik_apply, "CONTAINER", "authentik-server-1"),
            patch("ctl.authentik_apply.actions.docker_cmd_with_stdin", return_value=(rc, output)) as docker,
        ):
            return authentik_apply.apply_blueprints(lambda _line: None), docker

    def test_removals_run_first_and_records_of_deleted_files_are_forgotten(self):
        (ok, _detail), docker = self.run_apply("noise\nMU3LAB_BLUEPRINTS=ok\n")
        self.assertTrue(ok)
        script = docker.call_args.args[1]
        self.assertIn('path.stem.endswith("-removed")', script)
        self.assertIn("instance.delete()", script)

    def test_a_rejected_blueprint_is_reported_by_name(self):
        (ok, detail), _docker = self.run_apply("MU3LAB_BLUEPRINTS=mu3lab-mealie.yaml\n")
        self.assertFalse(ok)
        self.assertIn("mu3lab-mealie.yaml", detail)

    def test_an_unreachable_authentik_fails_plainly(self):
        (ok, detail), _docker = self.run_apply("Error: No such container", rc=1)
        self.assertFalse(ok)
        self.assertIn("did not apply", detail)

    def test_nothing_runs_without_blueprints_or_without_a_container(self):
        with runtime_paths(RuntimePaths(self.paths.root / "elsewhere")):
            self.assertTrue(authentik_apply.apply_blueprints(lambda _line: None)[0])
        with runtime_paths(self.paths), patch("ctl.authentik_apply.actions.docker_cmd_with_stdin") as docker:
            authentik_apply.apply_blueprints(lambda _line: None)
        docker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
