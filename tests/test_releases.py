"""Upstream release discovery accepts only curated GitHub repositories."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from ctl import releases


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        releases._CACHE.clear()

    def test_rejects_arbitrary_urls(self):
        with self.assertRaisesRegex(ValueError, "invalid curated"):
            releases.latest("https://evil.example/repo")

    def test_reports_latest_stable_release(self):
        payload = {
            "tag_name": "v3.27.0",
            "html_url": "https://github.com/mealie-recipes/mealie/releases/tag/v3.27.0",
            "published_at": "2026-09-17T13:40:44Z",
            "name": "3.27",
            "body": "notes",
        }
        with patch("urllib.request.urlopen", return_value=_Response(json.dumps(payload).encode())):
            result = releases.latest("mealie-recipes/mealie", "v3.26.0")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "v3.27.0")

    def test_repository_spelling_may_differ_in_case(self):
        payload = {
            "tag_name": "v0.16.46",
            "html_url": "https://github.com/tashfeenahmed/freellmapi/releases/tag/v0.16.46",
        }
        with patch("urllib.request.urlopen", return_value=_Response(json.dumps(payload).encode())):
            result = releases.latest("TashfeenAhmed/FreeLLMAPI", "v0.16.19")
        self.assertEqual(result["latest_version"], "v0.16.46")

    def test_projects_marking_every_release_prerelease_use_the_newest_final_tag(self):
        listing = [
            {
                "tag_name": "v0.9.0-rc1",
                "html_url": "https://github.com/o/r/releases/tag/v0.9.0-rc1",
                "prerelease": True,
            },
            {"tag_name": "v0.8.9", "html_url": "https://github.com/o/r/releases/tag/v0.8.9", "prerelease": True},
        ]
        missing = urllib.error.HTTPError("https://api.github.com/repos/o/r/releases/latest", 404, "Not Found", {}, None)
        with patch("urllib.request.urlopen", side_effect=[missing, _Response(json.dumps(listing).encode())]):
            result = releases.latest("o/r", "v0.8.8")
        self.assertEqual(result["latest_version"], "v0.8.9")

    def test_invalid_release_link_is_rejected(self):
        payload = {"tag_name": "v1", "html_url": "https://evil.example/v1"}
        with (
            patch("urllib.request.urlopen", return_value=_Response(json.dumps(payload).encode())),
            self.assertRaisesRegex(RuntimeError, "invalid release"),
        ):
            releases.latest("owner/repo", "v0")


if __name__ == "__main__":
    unittest.main()
