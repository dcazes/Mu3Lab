"""Seeding the owner's Vaultwarden with Mu3Lab logins and provider sign-ups."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


from ctl.authentik_blueprints import GatedApp, render_gate_blueprint
from ctl.identity import mode_for
from ctl.integrations.vaultwarden import bootstrap as vw
from ctl.provider_catalog import PROVIDERS, setup_progress
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.vault_setup import desired_items

EMAIL = "owner@example.test"
KDF = {"kdf": vw.KDF_PBKDF2, "kdfIterations": 5000}


class CryptoTests(unittest.TestCase):
    def test_encstring_round_trips_and_detects_tampering(self):
        key = vw.SymmetricKey.from_bytes(os.urandom(64))
        value = vw.encrypt("secret ✓", key)
        self.assertTrue(value.startswith("2."))
        self.assertEqual(vw.decrypt(value, key), "secret ✓")
        iv, ciphertext, mac = value[2:].split("|")
        with self.assertRaises(vw.VaultError):
            vw.decrypt(f"2.{iv}|{ciphertext}|{'A' * len(mac)}", key)

    def test_master_key_salt_is_the_normalized_email(self):
        self.assertEqual(
            vw.derive_master_key("pw", " Owner@Example.TEST ", KDF), vw.derive_master_key("pw", EMAIL, KDF)
        )

    def test_argon2id_accounts_are_supported(self):
        kdf = {"kdf": vw.KDF_ARGON2ID, "kdfIterations": 1, "kdfMemory": 16, "kdfParallelism": 1}
        self.assertEqual(len(vw.derive_master_key("pw", EMAIL, kdf)), 32)

    def test_unsafe_kdf_settings_are_refused(self):
        for kdf in ({"kdf": 0, "kdfIterations": 1}, {"kdf": 1, "kdfIterations": 1, "kdfMemory": 1}, {"kdf": 9}):
            with self.subTest(kdf=kdf), self.assertRaises(vw.VaultError):
                vw.derive_master_key("pw", EMAIL, kdf)


class DesiredItemsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = RuntimePaths(Path(tmp.name))
        env = self.paths.projects / "freellmapi" / ".env"
        env.parent.mkdir(parents=True)
        env.write_text("FREELLMAPI_ADMIN_PASSWORD=generated-admin\n", encoding="utf-8")
        self.registry = load_registry()

    def build(self):
        return desired_items(
            registry=self.registry,
            host="h.ts.net",
            owner_uid="subject",
            username="owner",
            email=EMAIL,
            paths=self.paths,
        )

    def test_services_use_host_and_port_matching(self):
        by_id = {entry.mu3lab_id: entry for entry in self.build()}
        self.assertEqual(by_id["service:authentik"].uris, (("https://h.ts.net", vw.MATCH_HOST),))
        self.assertEqual(by_id["service:authentik"].password, "")
        freellmapi = by_id["service:freellmapi"]
        self.assertEqual(freellmapi.uris, (("https://h.ts.net:8455", vw.MATCH_HOST),))
        self.assertEqual(freellmapi.password, "generated-admin")
        self.assertTrue(freellmapi.rotate)

    def test_every_email_provider_gets_a_unique_signup_entry(self):
        entries = [entry for entry in self.build() if entry.mu3lab_id.startswith("provider:")]
        expected = {f"provider:{p.id}" for p in PROVIDERS if p.account == "email"}
        self.assertEqual({entry.mu3lab_id for entry in entries}, expected)
        self.assertNotIn("provider:google", expected)
        self.assertEqual(len({entry.password for entry in entries}), len(entries))
        self.assertTrue(all(entry.username == EMAIL and not entry.rotate for entry in entries))


class ProviderSetupProgressTests(unittest.TestCase):
    def test_one_provider_completes_setup_but_two_recommended_are_suggested(self):
        one = setup_progress([{"provider_id": "openrouter", "enabled": True, "state": "verified"}])
        self.assertTrue(one["complete"])
        self.assertFalse(one["recommendation_met"])
        two = setup_progress(
            [
                {"provider_id": "groq", "enabled": True, "state": "verified"},
                {"provider_id": "nvidia", "enabled": True, "state": "verified"},
                {"provider_id": "google", "enabled": False, "state": "verified"},
            ]
        )
        self.assertTrue(two["recommendation_met"])
        self.assertEqual(two["recommended_verified"], ["groq", "nvidia"])

    def test_nothing_verified_is_incomplete(self):
        progress = setup_progress([{"provider_id": "groq", "enabled": True, "state": "degraded"}])
        self.assertFalse(progress["complete"])

    def test_recommended_providers_link_to_signup_and_key_pages(self):
        recommended = [p for p in PROVIDERS if p.recommended]
        self.assertGreaterEqual(len(recommended), 2)
        for provider in PROVIDERS:
            with self.subTest(provider=provider.id):
                self.assertTrue(provider.signup_url.startswith("https://"))
                self.assertTrue(provider.keys_url.startswith("https://"))


class FreeLlmApiGateTests(unittest.TestCase):
    def test_freellmapi_is_behind_the_authentik_proxy_gate(self):
        self.assertEqual(mode_for(load_registry().get("freellmapi")), "proxy_gate")

    def test_blueprint_protects_the_freellmapi_origin(self):
        content = render_gate_blueprint(
            "mu3lab-4.taile2cc7a.ts.net", 8446, (GatedApp("freellmapi", "FreeLLMAPI", 8455, "operators"),)
        )
        self.assertIn('external_host: "https://mu3lab-4.taile2cc7a.ts.net:8455"', content)
        self.assertIn("- !Find [authentik_providers_proxy.proxyprovider, [name, Mu3Lab FreeLLMAPI provider]]", content)
        self.assertIn("target: !KeyOf mu3lab-freellmapi-application", content)

    def test_caddy_forward_auths_the_freellmapi_route(self):
        from ctl.routes import render

        caddy = render("", [load_registry().get("freellmapi")])
        block = caddy.split(":19472 {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("forward_auth 127.0.0.1:9001", block)
        self.assertIn("header_up Host {http.request.hostport}", block)
        self.assertLess(block.index("forward_auth"), block.index("reverse_proxy 127.0.0.1:3001"))


if __name__ == "__main__":
    unittest.main()


class VaultSeededMarkerTests(unittest.TestCase):
    def test_marker_starts_unset_and_records_the_first_save(self):
        from ctl.control_state import ControlState

        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "state.sqlite3")
            self.assertEqual(state.vault_seeded(), {"seeded": False, "seeded_at": "", "seeded_by": ""})
            state.mark_vault_seeded("owner")
            self.assertTrue(state.vault_seeded()["seeded"])
