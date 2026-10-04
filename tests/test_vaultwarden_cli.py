"""Secrets stay off argv/logs and official CLI caches are deleted after jobs."""

from __future__ import annotations

import base64
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.integrations.vaultwarden import VaultError, VaultSession


class CliTests(unittest.TestCase):
    def test_password_and_item_are_passed_privately_and_cache_is_erased(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, dict(kwargs["env"]), kwargs.get("input")))
            reply = "session-secret" if "login" in argv else '{"id":"new"}'
            return subprocess.CompletedProcess(argv, 0, reply, "")

        with (
            patch("ctl.integrations.vaultwarden.cli.subprocess.run", side_effect=run),
            VaultSession("https://vault.test") as client,
        ):
            folder = Path(client.temporary.name)
            client.login("person@test", "master-secret")
            client.create_login(
                name="Saved", username="person", password="item-secret", folder_id=None, uris=[], notes="", fields={}
            )
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertNotIn("BW_PASSWORD", client.env)
        self.assertFalse(folder.exists())
        self.assertNotIn("master-secret", str([c[0] for c in calls]))
        self.assertNotIn("item-secret", str([c[0] for c in calls]))
        self.assertEqual(calls[1][1]["BW_PASSWORD"], "master-secret")
        self.assertEqual(json.loads(base64.b64decode(calls[-1][2]))["login"]["password"], "item-secret")

    def test_failure_never_exposes_cli_output(self):
        with (
            patch(
                "ctl.integrations.vaultwarden.cli.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, "password-secret", "session-secret"),
            ),
            VaultSession("https://vault.test") as client,
            self.assertRaises(VaultError) as caught,
        ):
            client.run("create", "item", body={})
        self.assertNotIn("secret", str(caught.exception))
