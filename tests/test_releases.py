"""Upstream release discovery accepts only curated GitHub repositories."""

from __future__ import annotations

import io
import json
import unittest
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
        payload = {"tag_name": "v3.27.0", "html_url": "https://github.com/mealie-recipes/mealie/releases/tag/v3.27.0", "published_at": "2026-09-17T13:40:44Z", "name": "3.27", "body": "notes"}
        with patch("urllib.request.urlopen", return_value=_Response(json.dumps(payload).encode())):
            result = releases.latest("mealie-recipes/mealie", "v3.26.0")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "v3.27.0")

    def test_invalid_release_link_is_rejected(self):
        payload = {"tag_name": "v1", "html_url": "https://evil.example/v1"}
        with patch("urllib.request.urlopen", return_value=_Response(json.dumps(payload).encode())):
            with self.assertRaisesRegex(RuntimeError, "invalid release"):
                releases.latest("owner/repo", "v0")


if __name__ == "__main__":
    unittest.main()
