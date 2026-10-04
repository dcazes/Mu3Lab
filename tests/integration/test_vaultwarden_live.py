"""Fresh isolated Vaultwarden: official CLI writes and per-person sharing.

Enable explicitly with MU3LAB_VAULT_INTEGRATION=1. The runner must start
mu3lab-test-vaultwarden at port 19902; it never uses a live Mu3Lab project.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.integrations.vaultwarden import VaultSession, register
from ctl.integrations.vaultwarden.org import STATUS_ACCEPTED, OrgSession
from ctl.runtime import RuntimePaths
from ctl.secrets import runtime_env_text

URL = "http://127.0.0.1:19902"
OWNER = "owner@mu3lab.test"
MEMBER = "member@mu3lab.test"
PASSWORD = "isolated test vault password 2026"


@unittest.skipUnless(os.environ.get("MU3LAB_VAULT_INTEGRATION") == "1", "requires isolated Vaultwarden")
class VaultwardenLiveTests(unittest.TestCase):
    def test_register_create_share_and_read_login(self):
        register(URL, OWNER, PASSWORD, "Owner")
        register(URL, MEMBER, PASSWORD, "Member")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            env = paths.projects / "vaultwarden" / ".env"
            env.parent.mkdir(parents=True)
            env.write_text(runtime_env_text({"MU3LAB_ADMIN_PASSWORD": os.environ["MU3LAB_TEST_VAULT_ADMIN_PASSWORD"]}))
            with OrgSession(URL) as owner:
                owner.login(OWNER, PASSWORD)
                org_id, key = owner.ensure_organization(OWNER)
                with patch("ctl.integrations.vaultwarden.org.RuntimePaths", return_value=paths):
                    owner.invite(org_id, MEMBER)
                member = next(m for m in owner.members(org_id) if m.email == MEMBER)
                self.assertEqual(member.status, STATUS_ACCEPTED)
                owner.confirm(org_id, member, key)
                collection = owner.create_collection(org_id, key, "Member private", [member.id])
                item_id = owner.create_login(
                    org_id,
                    key,
                    collection,
                    name="Test login",
                    username="member",
                    password="generated test password",
                    uris=[("https://app.example.test:8450", 1)],
                    notes="isolated integration",
                    fields={"mu3lab_id": "test:member"},
                )
                self.assertTrue(item_id)
                owner_items = owner.logins(org_id, key)
                self.assertEqual(owner_items[0].collection_ids, [collection])
            with VaultSession(URL) as member_session:
                member_session.login(MEMBER, PASSWORD)
                member_session.run("sync", raw=True)
                items = member_session.run("list", "items", "--organizationid", org_id)
                item = next(i for i in items if i["id"] == item_id)
                self.assertEqual(item["login"]["password"], "generated test password")
                self.assertEqual(item["collectionIds"], [collection])
            # Each session's plaintext cache disappears on close.
            self.assertFalse(Path(owner.temporary.name).exists())
            self.assertFalse(Path(member_session.temporary.name).exists())
