"""CI runs exactly the checks a developer runs, with pinned actions (review R30)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _steps(workflow: str, job: str) -> list[dict]:
    return yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"][job]["steps"]


class CiParityTests(unittest.TestCase):
    def test_ci_only_runs_the_make_targets(self):
        commands = [step["run"].strip() for step in _steps("ci.yml", "validate") if "run" in step]
        self.assertEqual(
            commands, ["uv sync --frozen", "make verify UV=uv", "make verify-security UV=uv", "make sbom UV=uv"]
        )

    def test_every_check_that_ci_used_to_run_is_in_a_make_target(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for check in (
            "bash -n",
            "tools.validate_compose",
            "ctl.manifest.catalog import load",
            "tools.api_schema --check",
            "mypy",
            "ruff format --check",
            "tools.dashboard check",
            "tools.dashboard build",
            "pip-audit",
            "tools.audit",
            "tools.check_images",
            "cyclonedx",
        ):
            self.assertIn(check, makefile)
        self.assertRegex(makefile, r"(?m)^verify: lint typecheck test contract$")

    def test_every_external_action_is_pinned_to_a_commit(self):
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                match = re.search(r"uses:\s*([^\s#]+)", line)
                if not match or match.group(1).startswith("./"):
                    continue
                with self.subTest(workflow=workflow.name, action=match.group(1)):
                    self.assertRegex(match.group(1), r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
                    self.assertRegex(line, r"# v\d", "name the release the commit is, for review")

    def test_dependabot_proposes_action_updates(self):
        config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
        self.assertIn("github-actions", [update["package-ecosystem"] for update in config["updates"]])

    def test_release_files_and_images_get_signed_build_records(self):
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("actions/attest-build-provenance@"), 2)
        self.assertIn("id-token: write", text)


if __name__ == "__main__":
    unittest.main()
