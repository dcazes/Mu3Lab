"""R07: deactivation denies at once and revokes every Mu3Lab credential until done."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import access
from ctl.api import create_app
from ctl.runtime import RuntimePaths
from tests.test_app_security import OPERATOR, PROXY_TOKEN


class ApiDenialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        paths = RuntimePaths(Path(temporary.name))
        patcher = patch("ctl.access.RuntimePaths", return_value=paths)
        patcher.start()
        self.addCleanup(patcher.stop)
        for target, value in (
            ("ctl.api.security.ingress_token", PROXY_TOKEN),
            ("ctl.api.security.csrf_token", "bound-token"),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(), base_url="https://host.ts.net")

    def test_held_operator_session_cannot_administer(self):
        self.assertEqual(self.client.get("/api/v1/system/config", headers=OPERATOR).status_code, 200)
        access.record(OPERATOR["x-authentik-uid"], "owner", "deactivated", "admin")
        response = self.client.get("/api/v1/system/config", headers=OPERATOR)
        self.assertEqual(response.status_code, 403)
        me = self.client.get("/api/v1/identity", headers=OPERATOR)
        if me.status_code == 200:
            self.assertFalse(me.json()["writes_enabled"])

    def test_unreadable_access_store_fails_closed(self):
        access.record("someone", "someone", "demoted", "admin")
        with patch("ctl.access.db.connect", side_effect=__import__("sqlite3").OperationalError("locked")):
            self.assertEqual(access.hold("anyone")["kind"], "deactivated")


if __name__ == "__main__":
    unittest.main()
