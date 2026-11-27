"""Newer releases from each app's makers: how they are named, screened, offered and checked after updating."""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ctl.lifecycle import app_releases, update_checks, upstream
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.store import records

APPROVED = "ghcr.io/mealie-recipes/mealie:v3.30.0@sha256:approved"


def _quiet(_line: str) -> None:
    pass


class VersionTests(unittest.TestCase):
    def test_release_numbers_lose_their_prefixes(self):
        self.assertEqual(upstream.bare("n8n@2.49.7"), "2.49.7")
        self.assertEqual(upstream.bare("release-5.3.1"), "5.3.1")
        self.assertEqual(upstream.bare("v3.4.2"), "3.4.2")

    def test_releases_are_named_like_mu3labs(self):
        self.assertEqual(upstream.label("n8n@2.48.5", "2.49.7"), "n8n@2.49.7")
        self.assertEqual(upstream.label("v5.3.0", "5.3.1"), "v5.3.1")
        self.assertEqual(upstream.label("1.21.0", "1.23.0"), "1.23.0")

    def test_a_new_major_version_counts_the_minor_one_before_1_0(self):
        self.assertNotEqual(upstream.major("v0.14.0"), upstream.major("v0.13.1"))
        self.assertEqual(upstream.major("v0.13.5"), upstream.major("v0.13.0"))
        self.assertEqual(upstream.major("v3.4.2"), upstream.major("v3.3.6"))
        self.assertNotEqual(upstream.major("v4.0.0"), upstream.major("v3.9.0"))


class ScreeningTests(unittest.TestCase):
    def reasons(self, body: str) -> list[str]:
        return upstream._risky_notes([("v1.1.0", body)])

    def test_notes_that_ask_something_of_the_upgrade_hold_it(self):
        for body in (
            "## Breaking changes\n- the API moved",
            "> [!WARNING]\n> Back up first",
            "Note that some users may need to adjust their PAPERLESS_ALLOWED_HOSTS setting",
            "- Things to know when upgrading:",
            "### Action required",
        ):
            with self.subTest(body=body):
                self.assertTrue(self.reasons(body))

    def test_ordinary_notes_do_not(self):
        for body in (
            "- Math next to a non-breaking hyphen now renders",
            "- Fixed Claude Code's billing header breaking prompt caching",
            "- BUGFIX: Fix performance statistics time units",
            # Every Servarr release says this.
            "**If you are using docker you will need to update the container image. "
            "Do not attempt to update Prowlarr within an existing docker container.**",
        ):
            with self.subTest(body=body):
                self.assertEqual(self.reasons(body), [])

    def test_only_the_projects_main_deployment_files_count(self):
        def counts(path: str) -> bool:
            return bool(upstream.DEPLOYMENT_FILE.search(path)) and not upstream.NOT_DEPLOYMENT.search(path)

        for path in ("docker-compose.yml", "docker/docker-compose.yml", ".env.example", "compose.yaml"):
            self.assertTrue(counts(path), path)
        for path in ("docker-compose.sso.yml", "docker/docker-compose.dev.yml", "www/.env.example", ".github/x.yml"):
            self.assertFalse(counts(path), path)


class TagTests(unittest.TestCase):
    def test_the_release_number_in_a_tag_is_replaced(self):
        self.assertEqual(
            upstream.new_tag(None, "ghcr.io/x/y:v3.3.6-cuda@sha256:a", "", "3.3.6", "3.4.2"), "v3.4.2-cuda"
        )
        self.assertEqual(upstream.new_tag(None, "postgres:16-alpine@sha256:a", "", "3.3.6", "3.4.2"), "")
        # 3.3.6 inside 13.3.6 is another release.
        self.assertEqual(upstream.new_tag(None, "ghcr.io/x/y:13.3.6@sha256:a", "", "3.3.6", "3.4.2"), "")

    def test_a_build_counter_takes_the_highest_build(self):
        tags = ["3.1.0.5799-ls169", "3.1.0.5799-ls170", "3.1.0.5799-ls98", "3.0.2.5780-ls171", "nightly-3.1.0.5799-ls2"]
        with patch.object(upstream, "_registry_tags", return_value=tags), patch.object(upstream, "Registry"):
            tag = upstream.new_tag(
                None,
                "lscr.io/linuxserver/prowlarr:3.0.2.5780-ls169@sha256:a",
                "{version}-ls*",
                "3.0.2.5780",
                "3.1.0.5799",
            )
        self.assertEqual(tag, "3.1.0.5799-ls170")

    def test_a_missing_build_waits_for_the_next_check(self):
        with (
            patch.object(upstream, "_registry_tags", return_value=["5.3.0-2"]),
            patch.object(upstream, "Registry"),
            self.assertRaises(upstream.CheckFailed),
        ):
            upstream.new_tag(None, "q/qb:5.3.0-2@sha256:a", "{version}-*", "5.3.0", "5.3.1")


class CandidateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.root = base / "checkout"
        self.paths = RuntimePaths(base / "runtime")
        self.paths.state.mkdir(parents=True)
        definition = self.root / "apps" / "mealie"
        definition.mkdir(parents=True)
        (definition / "docker-compose.yml").write_text(
            yaml.safe_dump({"services": {"mealie": {"image": APPROVED}}}), encoding="utf-8"
        )
        self.service = dataclasses.replace(
            load().get("mealie"), update={"repository": "mealie-recipes/mealie", "approved_version": "v3.30.0"}
        )

    def found(self, **record):
        base = {"state": "ready", "version": "v3.31.0", "images": {"cpu": {"mealie": "m:v3.31.0@sha256:n"}}}
        records.put(upstream.SCOPE, "mealie", {"failed": {}, **base, **record}, self.paths)

    def target(self):
        return app_releases.target(self.service, self.root, "cpu", self.paths)

    def test_a_screened_newer_release_is_the_target(self):
        self.found()
        self.assertEqual(self.target().version, "v3.31.0")
        self.assertEqual(app_releases.source(self.service, self.root, self.target()), "upstream")

    def test_held_failed_or_overtaken_releases_are_not(self):
        for record in ({"state": "held"}, {"failed": {"v3.31.0": "broke sign-in"}}, {"version": "v3.29.0"}):
            with self.subTest(record=record):
                self.found(**record)
                self.assertEqual(self.target().version, "v3.30.0")
                self.assertEqual(app_releases.source(self.service, self.root, self.target()), "mu3lab")

    def test_another_build_needs_its_own_images(self):
        self.found()
        self.assertEqual(app_releases.target(self.service, self.root, "nvidia", self.paths).version, "v3.30.0")

    def test_foundation_apps_never_move_on_their_own(self):
        self.found()
        foundation = dataclasses.replace(self.service, stage="foundation")
        self.assertIsNone(upstream.candidate(foundation, self.root, "cpu", self.paths))


class AfterUpdateCheckTests(unittest.TestCase):
    def regressions(self, probes, passed, *, settle=0.0):
        with patch.object(update_checks, "probes", return_value=probes):
            return update_checks.regressions(None, None, passed, _quiet, settle=settle, sleep=lambda _s: None)

    def test_only_what_worked_before_is_held_against_the_new_release(self):
        probes = {"sign-in": lambda _p: True, "widget Recipes": lambda _p: False, "chat": lambda _p: None}
        with patch.object(update_checks, "probes", return_value=probes):
            self.assertEqual(update_checks.baseline(None, None, _quiet), ["sign-in"])
        self.assertEqual(self.regressions(probes, ["sign-in"]), [])

    def test_a_check_that_keeps_failing_is_a_regression(self):
        self.assertEqual(self.regressions({"sign-in": lambda _p: False}, ["sign-in"]), ["sign-in"])

    def test_a_check_gets_time_to_pass_again(self):
        answers = iter([False, False, True])
        self.assertEqual(self.regressions({"chat": lambda _p: next(answers)}, ["chat"], settle=60), [])

    def test_a_read_only_tool_without_arguments_is_called(self):
        tools = [
            {"id": "create_recipe", "risk": "write", "parameters": {}},
            {"id": "get_recipe", "risk": "read", "parameters": {"required": ["slug"]}},
            {"id": "search_recipes", "risk": "read", "parameters": {}},
            {"id": "list_recipes", "risk": "read", "parameters": {"required": []}},
        ]
        self.assertEqual(update_checks._read_tool(tools), "list_recipes")
        self.assertEqual(update_checks._read_tool(tools[:2]), "")


if __name__ == "__main__":
    unittest.main()
