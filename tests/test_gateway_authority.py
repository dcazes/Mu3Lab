"""Durable policy, operator credentials and human approval enforcement."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from ctl import mcp_gateway
from gateway_authority import Authority
from tests.test_mcp_gateway import _app, gateway


class ExistingBoundaryRegressions(unittest.TestCase):
    def test_failed_publication_cannot_preserve_previous_authority(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(mcp_gateway, "project", return_value=Path(tmp)):
            mcp_gateway.write_policy({"version": 1, "apps": {"demo": _app()}})
            reader = gateway.PolicyStore(str(Path(tmp) / "policy" / "policy.json"))
            reader.get()
            with (
                patch.object(mcp_gateway, "build_policy", side_effect=ValueError("publication failed")),
                self.assertRaises(ValueError),
            ):
                mcp_gateway.write_policy()
            with self.assertRaises((OSError, ValueError)):
                reader.get()

    def test_app_wide_bearer_is_not_a_person(self):
        app = _app()
        app["token_sha256"] = hashlib.sha256(b"old-app-token").hexdigest()
        handler = MagicMock(headers={"Authorization": "Bearer old-app-token"})
        self.assertFalse(gateway.Handler._authorized(handler, app))

    def test_approval_capable_write_is_prepared_instead_of_dispatched(self):
        app = _app(rename={"enabled": True, "core": True}) | {
            "approval_required": True,
            "data_scope": "operator_only",
            "_principal": {"subject": "operator", "version": 1},
            "_revision": 1,
        }
        authority = MagicMock()
        authority.prepare.return_value = {"id": "pending", "state": "pending"}
        connector = MagicMock()
        connector.list_tools.return_value = {"rename": {"inputSchema": {"type": "object"}}}
        with (
            patch.object(gateway.CONNECTORS, "get", return_value=connector),
            patch.object(gateway, "AUTHORITY", authority, create=True),
        ):
            result, _, _ = gateway.handle_call("demo", app, "rename", {"name": "changed"})
        self.assertEqual(result["structuredContent"]["operation_id"], "pending")
        connector.request.assert_not_called()


class AuthorityContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Authority(Path(self.tmp.name) / "authority")
        self.revision = self.store.invalidate()
        self.store.publish(self.revision, "committed")
        self.store.grant_subject("operator", operator=True)
        self.token = self.store.credential("operator", "demo")
        self.principal = self.store.authenticate(self.token, "demo")
        self.app = _app(rename={"enabled": True}) | {"approval_required": True}
        self.schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }

    def prepare(self):
        return self.store.prepare(
            self.principal, "demo", self.app, "rename", {"name": "private-name"}, self.schema, self.revision
        )

    def test_members_and_bare_app_tokens_have_no_credentials(self):
        self.store.grant_subject("member", operator=False)
        with self.assertRaises(ValueError):
            self.store.credential("member", "demo")
        with self.assertRaises(ValueError):
            self.store.authenticate("legacy-app-token", "demo")
        with self.assertRaises(ValueError):
            self.store.authenticate(self.token, "other")

    def test_credential_rotation_revokes_cached_identity_and_pending_approval(self):
        request = self.prepare()
        newer = self.store.credential("operator", "demo", rotate=True)
        self.assertNotEqual(newer, self.token)
        with self.assertRaises(ValueError):
            self.store.authenticate(self.token, "demo")
        self.assertEqual(self.store.view(request["id"], "operator")["state"], "revoked")
        with self.assertRaises(ValueError):
            self.store.claim(request["id"], self.principal, self.app, self.schema, self.revision)

    def test_subject_revocation_and_expired_evidence_deny_reads(self):
        self.store.revoke_subject("operator")
        with self.assertRaises(ValueError):
            self.store.authenticate(self.token, "demo")
        self.store.grant_subject("operator", operator=True)
        new_token = self.store.credential("operator", "demo")
        with patch("gateway_authority.time.time", return_value=10**12), self.assertRaises(ValueError):
            self.store.authenticate(new_token, "demo")
        with self.assertRaises(ValueError):
            self.store.authenticate(self.token, "demo")

    def test_arguments_are_encrypted_and_wrong_person_cannot_decide_or_view(self):
        request = self.prepare()
        self.assertNotIn(b"private-name", self.store.database.read_bytes())
        with self.assertRaises(ValueError):
            self.store.view(request["id"], "other", arguments=True)
        self.store.grant_subject("other", operator=True)
        with self.assertRaises(ValueError):
            self.store.decide(request["id"], "other", approve=True)
        self.assertEqual(
            self.store.view(request["id"], "operator", arguments=True)["arguments"], {"name": "private-name"}
        )

    def test_pending_rejected_expired_and_revoked_requests_never_dispatch(self):
        request = self.prepare()
        with self.assertRaises(ValueError):
            self.store.claim(request["id"], self.principal, self.app, self.schema, self.revision)
        self.store.decide(request["id"], "operator", approve=False)
        self.assertIsNone(self.store.claim(request["id"], self.principal, self.app, self.schema, self.revision))
        request = self.prepare()
        self.store.decide(request["id"], "operator", approve=True)
        with (
            patch("gateway_authority.time.time", return_value=request["expires_at"] + 1),
            self.assertRaises(ValueError),
        ):
            self.store.claim(request["id"], self.principal, self.app, self.schema, self.revision)
        self.store.invalidate()
        with self.assertRaises(ValueError):
            self.store.claim(request["id"], self.principal, self.app, self.schema, self.revision)

    def test_changed_schema_and_connector_revision_cannot_use_approval(self):
        request = self.prepare()
        self.store.decide(request["id"], "operator", approve=True)
        for app, schema in (
            (self.app | {"connector_revision": "different"}, self.schema),
            (self.app, {"type": "object"}),
        ):
            with self.subTest(app=app["connector_revision"]), self.assertRaises(ValueError):
                self.store.claim(request["id"], self.principal, app, schema, self.revision)
        self.assertEqual(self.store.view(request["id"], "operator")["state"], "approved")

    def test_concurrent_claims_dispatch_once_and_terminal_result_cannot_replay(self):
        request = self.prepare()
        self.store.decide(request["id"], "operator", approve=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(
                pool.map(
                    lambda _: self.store.claim(request["id"], self.principal, self.app, self.schema, self.revision),
                    range(4),
                )
            )
        self.assertEqual(sum(claim is not None for claim in claims), 1)
        self.store.finish(request["id"], "succeeded")
        self.assertIsNone(self.store.claim(request["id"], self.principal, self.app, self.schema, self.revision))
        self.assertNotIn("arguments", self.store.view(request["id"], "operator", arguments=True))

    def test_gateway_restart_retains_pending_and_never_replays_dispatching(self):
        pending = self.prepare()
        reopened = Authority(self.store.directory)
        self.assertEqual(reopened.view(pending["id"], "operator")["state"], "pending")
        reopened.decide(pending["id"], "operator", approve=True)
        reopened.claim(pending["id"], self.principal, self.app, self.schema, self.revision)
        reopened.recover_dispatches()
        self.assertEqual(reopened.view(pending["id"], "operator")["state"], "outcome_unknown")
        self.assertIsNone(reopened.claim(pending["id"], self.principal, self.app, self.schema, self.revision))

    def test_revocation_epoch_and_hash_survive_restart_and_policy_rollback(self):
        old = self.revision
        self.store.invalidate()
        reopened = Authority(self.store.directory)
        with self.assertRaises(ValueError):
            reopened.check_policy(old, "committed")
        new = reopened.policy_state()["revision"]
        reopened.publish(new, "new-hash")
        with self.assertRaises(ValueError):
            reopened.check_policy(new, "different-hash")
        reopened.check_policy(new, "new-hash", acknowledge=True)
        self.assertEqual(reopened.policy_state()["acknowledged"], new)

    def test_missing_key_requires_original_checkpoint(self):
        self.prepare()
        self.store.key.unlink()
        with self.assertRaises(ValueError):
            Authority(self.store.directory)

    def test_arguments_validation_and_changed_app_cannot_prepare(self):
        for payload in ({"name": 4}, {"name": "value", "extra": "unsafe"}):
            with self.assertRaises(ValueError):
                self.store.prepare(self.principal, "demo", self.app, "rename", payload, self.schema, self.revision)
        with self.assertRaises(ValueError):
            self.store.prepare(
                self.principal, "other", self.app, "rename", {"name": "value"}, self.schema, self.revision
            )

    def test_corrupt_authority_denies_cached_policy_with_controlled_error(self):
        self.store.database.write_bytes(b"not a sqlite database")
        with self.assertRaisesRegex(ValueError, "authority is unavailable"):
            self.store.check_policy(self.revision, "committed")

    def test_prepare_deduplication_binds_schema_and_connector(self):
        first = self.prepare()
        same = self.prepare()
        self.assertEqual(first["id"], same["id"])
        for app, schema in (
            (self.app | {"connector_revision": "new"}, self.schema),
            (self.app, {"type": "object"}),
        ):
            changed = self.store.prepare(
                self.principal, "demo", app, "rename", {"name": "private-name"}, schema, self.revision
            )
            self.assertNotEqual(first["id"], changed["id"])
