"""The pinned-image check asks a busy registry again but never hides a missing digest."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from tools import check_images

IMAGE = "ghcr.io/example/app:1.0@sha256:" + "a" * 64
INDEX = json.dumps({"manifests": [{"platform": {"os": "linux", "architecture": "amd64"}}]})


def answer(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


class RegistryRetryTests(unittest.TestCase):
    def test_a_rate_limited_registry_is_asked_again(self):
        busy = answer(1, stderr="unexpected status from GET request: 429 Too Many Requests\ntoomanyrequests")
        with (
            patch.object(check_images.subprocess, "run", side_effect=[busy, answer(0, INDEX)]) as run,
            patch.object(check_images.time, "sleep") as sleep,
        ):
            self.assertEqual(check_images.platforms(IMAGE), {"linux/amd64"})
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(check_images.RETRY_WAITS[0])

    def test_a_digest_the_registry_does_not_have_fails_at_once(self):
        missing = answer(1, stderr=f"ERROR: {IMAGE}: not found")
        with (
            patch.object(check_images.subprocess, "run", return_value=missing) as run,
            patch.object(check_images.time, "sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "not found"),
        ):
            check_images.platforms(IMAGE)
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()

    def test_a_registry_busy_every_time_fails_after_the_last_wait(self):
        busy = answer(1, stderr="503 Service Unavailable")
        with (
            patch.object(check_images.subprocess, "run", return_value=busy) as run,
            patch.object(check_images.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "503"),
        ):
            check_images.platforms(IMAGE)
        self.assertEqual(run.call_count, len(check_images.RETRY_WAITS) + 1)


if __name__ == "__main__":
    unittest.main()
