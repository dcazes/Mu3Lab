"""REST contract, bounded readiness, useful failures and password hashing."""

from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

from ctl.integrations.authentik import password_hash
from ctl.secrets import clear_authentik_bootstrap, ensure_authentik_env, read_runtime_env


class AuthentikClientTests(unittest.TestCase):
    def test_password_hash_matches_django_pbkdf2(self):
        value = password_hash("test password", salt="known-salt", iterations=10)
        expected = base64.b64encode(hashlib.pbkdf2_hmac("sha256", b"test password", b"known-salt", 10)).decode()
        self.assertEqual(value, f"pbkdf2_sha256$10$known-salt${expected}")

    def test_the_recovery_flow_is_set_on_authentiks_own_default_brand(self):
        import yaml

        from ctl.authentik_blueprints import render_gate_blueprint

        document = yaml.safe_load(
            render_gate_blueprint("mu3lab.example.ts.net", 8443).replace("!KeyOf", "").replace("!Find", "")
        )
        brand = next(entry for entry in document["entries"] if entry["model"] == "authentik_brands.brand")
        self.assertEqual(brand["identifiers"], {"domain": "authentik-default", "default": True})

    def test_first_start_env_is_preserved_and_cleanup_keeps_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, added = ensure_authentik_env(
                root,
                token_factory=lambda: "stable-secret",
                email="owner@example.test",
                password_hash=password_hash("owner password", iterations=10),
            )
            original = read_runtime_env(path)
            self.assertIn("AUTHENTIK_BOOTSTRAP_TOKEN", added)
            self.assertNotIn("owner password", path.read_text())
            self.assertNotIn("AUTHENTIK_TAG", original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            ensure_authentik_env(
                root, token_factory=lambda: "different-secret", email="new@example.test", password_hash="different-hash"
            )
            self.assertEqual(read_runtime_env(path), original)
            clear_authentik_bootstrap(root)
            final = read_runtime_env(path)
            self.assertEqual(final["AUTHENTIK_BOOTSTRAP_TOKEN"], original["AUTHENTIK_BOOTSTRAP_TOKEN"])
            self.assertNotIn("AUTHENTIK_BOOTSTRAP_PASSWORD_HASH", final)
            self.assertNotIn("AUTHENTIK_BOOTSTRAP_EMAIL", final)


if __name__ == "__main__":
    unittest.main()
