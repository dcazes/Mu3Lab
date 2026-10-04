"""Generated logins reach each person's own vault collection, once, without their password."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import onboarding_state, vault_sync
from ctl.integrations.vaultwarden.org import STATUS_ACCEPTED, STATUS_CONFIRMED, Member, OrgLogin
from ctl.runtime import RuntimePaths

KEY = None


class FakeOrg:
    """The organization calls the sync makes, recorded in memory."""

    def __init__(self, members: dict[str, int]):
        self.members_by_email = {
            email: Member(f"m-{email}", f"u-{email}", email, status) for email, status in members.items()
        }
        self.items: list[OrgLogin] = []
        self.collections_by_id: dict[str, str] = {}
        self.confirmed: list[str] = []
        self.invited: list[str] = []
        self.writes = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def ensure_organization(self, _email):
        return "org", KEY

    def members(self, _org):
        return list(self.members_by_email.values())

    def invite(self, _org, email):
        self.invited.append(email)
        self.members_by_email[email] = Member(f"m-{email}", "", email, 0)

    def confirm(self, _org, member, _key):
        self.confirmed.append(member.email)
        member.status = STATUS_CONFIRMED

    def collections(self, _org, _key):
        return dict(self.collections_by_id)

    def create_collection(self, _org, _key, name, member_ids):
        collection = f"c{len(self.collections_by_id)}"
        self.collections_by_id[collection] = name
        return collection

    def logins(self, _org, _key):
        return list(self.items)

    def create_login(self, _org, _key, collection, **item):
        self.writes += 1
        self.items.append(
            OrgLogin(
                id=str(len(self.items)),
                name=item["name"],
                username=item["username"],
                uris=[uri for uri, _ in item["uris"]],
                fields=item["fields"],
                collection_ids=[collection],
                raw={"login": {"password": item["password"]}},
            )
        )
        return self.items[-1].id

    def rename_collection(self, _org, _key, collection, name, member_ids):
        self.collections_by_id[collection] = name

    def delete_login(self, login_id):
        self.items = [item for item in self.items if item.id != login_id]

    def update_login(self, existing, _org, _key, **item):
        self.writes += 1
        existing.raw["login"]["password"] = item["password"]


OWNER = {"uid": "o" * 64, "email": "owner@example.test", "name": "Owner", "role": "admin", "active": True}
PARTNER = {"uid": "p" * 64, "email": "partner@example.test", "name": "Partner", "role": "member", "active": True}


class VaultSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = RuntimePaths(Path(self.tmp.name))
        self.paths.runtime.mkdir(parents=True)
        (self.paths.projects / "litellm").mkdir(parents=True)
        (self.paths.projects / "litellm" / ".env").write_text("LITELLM_MASTER_KEY=sk-master\n")
        identity = {"owner_uid": OWNER["uid"], "email": OWNER["email"], "username": "owner", "display_name": "Owner"}
        onboarding_state.remember_owner("surfsense", identity, self.paths)
        onboarding_state.prepare_login("surfsense", self.paths)
        onboarding_state.complete_login("surfsense", "owner", "https://host.ts.net:8447", self.paths)

    def sync(self, org: FakeOrg, people):
        with patch("ctl.vault_sync._session", return_value=org):
            return vault_sync.sync(people, "host.ts.net", lambda _line: None, self.paths)

    def test_logins_land_in_the_persons_own_collection_with_the_app_address(self):
        org = FakeOrg({"owner@example.test": STATUS_CONFIRMED})
        result = self.sync(org, [OWNER])
        self.assertEqual(result["people"][0]["state"], "up_to_date")
        names = {item.name: item for item in org.items}
        self.assertEqual(names["SurfSense"].uris, ["https://host.ts.net:8447"])
        self.assertEqual(names["LiteLLM admin"].username, "admin")
        self.assertEqual(org.collections_by_id, {"c0": "Mu3Lab app logins"})
        # Saved once, it is no longer waiting to be saved.
        self.assertEqual(onboarding_state.pending_logins(OWNER["uid"], self.paths), [])

    def test_running_again_writes_nothing_new(self):
        org = FakeOrg({"owner@example.test": STATUS_CONFIRMED})
        self.sync(org, [OWNER])
        writes = org.writes
        self.sync(org, [OWNER])
        self.assertEqual(org.writes, writes)

    def test_a_changed_admin_password_is_updated_in_place(self):
        org = FakeOrg({"owner@example.test": STATUS_CONFIRMED})
        self.sync(org, [OWNER])
        (self.paths.projects / "litellm" / ".env").write_text("LITELLM_MASTER_KEY=sk-rotated\n")
        self.sync(org, [OWNER])
        self.assertEqual(len([item for item in org.items if item.name == "LiteLLM admin"]), 1)

    def test_someone_without_a_vault_account_is_invited_and_waits(self):
        org = FakeOrg({"owner@example.test": STATUS_CONFIRMED})
        result = self.sync(org, [OWNER, PARTNER])
        partner = next(person for person in result["people"] if person["name"] == "Partner")
        self.assertEqual(partner["state"], "waiting_for_account")
        self.assertEqual(org.invited, ["partner@example.test"])

    def test_an_accepted_member_is_confirmed_and_gets_their_logins(self):
        org = FakeOrg({"owner@example.test": STATUS_ACCEPTED})
        self.sync(org, [OWNER])
        self.assertEqual(org.confirmed, ["owner@example.test"])
        self.assertTrue(org.items)

    def test_authentik_only_apps_never_reach_the_vault_and_old_logins_are_removed(self):
        identity = {"owner_uid": OWNER["uid"], "email": OWNER["email"], "username": "owner", "display_name": "Owner"}
        onboarding_state.remember_owner("paperless-ngx", identity, self.paths)
        onboarding_state.prepare_login("paperless-ngx", self.paths)
        onboarding_state.complete_login("paperless-ngx", "owner", "https://host.ts.net:8452", self.paths)
        self.assertNotIn("service:paperless-ngx", [i.mu3lab_id for i in vault_sync.items_for(OWNER, "", self.paths)])
        org = FakeOrg({"owner@example.test": STATUS_CONFIRMED})
        org.create_login(
            "org",
            KEY,
            "c0",
            name="Paperless-ngx",
            username="owner",
            password="old",
            uris=[("https://host.ts.net:8452", None)],
            fields={"mu3lab_id": f"{OWNER['uid']}:service:paperless-ngx"},
        )
        self.sync(org, [OWNER])
        self.assertNotIn("Paperless-ngx", [item.name for item in org.items])
        self.assertIn("SurfSense", [item.name for item in org.items])
        self.assertNotIn("password", onboarding_state.read("paperless-ngx", self.paths))

    def test_an_older_collection_name_is_renamed(self):
        org = FakeOrg({"owner@example.test": STATUS_CONFIRMED})
        org.collections_by_id["c0"] = "Mu3Lab: Owner"
        with patch("ctl.vault_sync._state", return_value={"collections": {OWNER["uid"]: "c0"}}):
            self.sync(org, [OWNER])
        self.assertEqual(org.collections_by_id, {"c0": "Mu3Lab app logins"})

    def test_members_never_receive_administrator_logins(self):
        items = vault_sync.items_for(PARTNER, "host.ts.net", self.paths)
        self.assertNotIn("service:litellm", [item.mu3lab_id for item in items])

    def test_a_login_the_old_flow_saved_personally_is_not_duplicated(self):
        from ctl.control_state import ControlState

        (self.paths.projects / "freellmapi").mkdir(parents=True)
        (self.paths.projects / "freellmapi" / ".env").write_text("FREELLMAPI_ADMIN_PASSWORD=pw\n")
        ids = lambda: [item.mu3lab_id for item in vault_sync.items_for(OWNER, "host.ts.net", self.paths)]  # noqa: E731
        self.assertIn("service:freellmapi", ids())
        ControlState(self.paths.runtime / "control-plane.sqlite3").mark_vault_seeded(OWNER["email"])
        self.assertNotIn("service:freellmapi", ids())


if __name__ == "__main__":
    unittest.main()
