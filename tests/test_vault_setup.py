"""Seeding the owner's Vaultwarden with Mu3Lab logins and provider sign-ups."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient

from ctl import vaultwarden_api as vw
from ctl.api import create_app
from ctl.authentik_blueprints import GatedApp, render_gate_blueprint
from ctl.identity import mode_for
from ctl.provider_catalog import PROVIDERS, setup_progress
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.vault_setup import FOLDER, PROVIDER_FOLDER, desired_items, seed

EMAIL = "owner@example.test"
PASSWORD = "correct horse battery staple"
KDF = {"kdf": vw.KDF_PBKDF2, "kdfIterations": 5000}


class FakeVaultwarden:
    """An in-memory server speaking the subset of the protocol Mu3Lab uses."""

    def __init__(self, *, totp: str = "") -> None:
        master_key = vw.derive_master_key(PASSWORD, EMAIL, KDF)
        self.password_hash = vw.master_password_hash(master_key, PASSWORD)
        self.user_key = vw.SymmetricKey.from_bytes(os.urandom(64))
        self.protected_key = vw.encrypt(self.user_key.enc + self.user_key.mac, vw.stretch(master_key))
        self.totp = totp
        self.folders: list[dict[str, Any]] = []
        self.ciphers: list[dict[str, Any]] = []
        self.requests: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(f"{request.method} {path}")
        if path == "/identity/accounts/prelogin":
            return httpx.Response(200, json=KDF)
        if path == "/identity/connect/token":
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            if form.get("username") != EMAIL or form.get("password") != self.password_hash:
                return httpx.Response(400, json={"error": "invalid_grant"})
            if self.totp and form.get("twoFactorToken") != self.totp:
                return httpx.Response(400, json={"TwoFactorProviders": [0]})
            return httpx.Response(200, json={"access_token": "token", "Key": self.protected_key})
        if request.headers.get("authorization") != "Bearer token":
            return httpx.Response(401)
        body = json.loads(request.content) if request.content else {}
        if path == "/api/sync":
            return httpx.Response(200, json={"folders": self.folders, "ciphers": self.ciphers})
        if path == "/api/folders":
            folder = {"id": str(uuid.uuid4()), "name": body["name"]}
            self.folders.append(folder)
            return httpx.Response(200, json=folder)
        if path == "/api/ciphers" and request.method == "POST":
            cipher = {**body, "id": str(uuid.uuid4()), "object": "cipher"}
            self.ciphers.append(cipher)
            return httpx.Response(200, json=cipher)
        if path.startswith("/api/ciphers/") and request.method == "PUT":
            cipher_id = path.rsplit("/", 1)[-1]
            index = next(i for i, item in enumerate(self.ciphers) if item["id"] == cipher_id)
            self.ciphers[index] = {**body, "id": cipher_id}
            return httpx.Response(200, json=self.ciphers[index])
        return httpx.Response(404)

    def add_login(self, name: str, uri: str, password: str = "mine", **extra: Any) -> None:
        key = self.user_key
        self.ciphers.append(
            {
                "id": str(uuid.uuid4()),
                "type": vw.LOGIN_ITEM,
                "name": vw.encrypt(name, key),
                "login": {"password": vw.encrypt(password, key), "uris": [{"uri": vw.encrypt(uri, key)}]},
                **extra,
            }
        )

    def decrypted(self) -> dict[str, dict[str, Any]]:
        key = self.user_key
        folders = {folder["id"]: vw.decrypt(folder["name"], key) for folder in self.folders}
        result = {}
        for cipher in self.ciphers:
            if cipher.get("organizationId"):
                continue
            login = cipher.get("login") or {}
            result[vw.decrypt(cipher["name"], key)] = {
                "folder": folders.get(cipher.get("folderId") or ""),
                "username": vw.decrypt(login.get("username"), key),
                "password": vw.decrypt(login.get("password"), key),
                "uris": [(vw.decrypt(uri["uri"], key), uri.get("match")) for uri in login.get("uris") or []],
                "history": len(cipher.get("passwordHistory") or []),
            }
        return result


def session_for(server: FakeVaultwarden) -> vw.VaultSession:
    return vw.VaultSession("http://vault.test", client=httpx.Client(transport=server.transport()))


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


class LoginTests(unittest.TestCase):
    def test_wrong_password_is_a_clear_error(self):
        with session_for(FakeVaultwarden()) as session, self.assertRaises(vw.VaultError) as caught:
            session.login(EMAIL, "wrong")
        self.assertEqual(caught.exception.code, "invalid_credentials")

    def test_two_step_login_asks_for_a_code_then_accepts_it(self):
        server = FakeVaultwarden(totp="123456")
        with session_for(server) as session:
            with self.assertRaises(vw.VaultError) as caught:
                session.login(EMAIL, PASSWORD)
            self.assertEqual(caught.exception.code, "two_factor_required")
            session.login(EMAIL, PASSWORD, totp="123456")
            self.assertEqual(session.sync(), ([], []))

    def test_error_messages_never_contain_the_password(self):
        with session_for(FakeVaultwarden()) as session, self.assertRaises(vw.VaultError) as caught:
            session.login(EMAIL, "hunter2-secret")
        self.assertNotIn("hunter2", str(caught.exception))


def item(mu3lab_id: str, name: str, uri: str, password: str = "", *, rotate: bool = False, folder: str = FOLDER):
    from ctl.vault_setup import DesiredItem

    return DesiredItem(
        mu3lab_id=mu3lab_id,
        name=name,
        folder=folder,
        username="user",
        password=password,
        uris=((uri, vw.MATCH_HOST if mu3lab_id.startswith("service:") else None),),
        rotate=rotate,
    )


class SeedTests(unittest.TestCase):
    def run_seed(self, server: FakeVaultwarden, items):
        with session_for(server) as session:
            session.login(EMAIL, PASSWORD)
            return seed(session, items)

    def test_first_run_creates_folders_and_items(self):
        server = FakeVaultwarden()
        items = [
            item("service:freellmapi", "FreeLLMAPI", "https://h.ts.net:8455", "gen-1", rotate=True),
            item("provider:groq", "Groq", "https://console.groq.com/", "p-1", folder=PROVIDER_FOLDER),
        ]
        result = self.run_seed(server, items)
        self.assertEqual(result.created, ["FreeLLMAPI", "Groq"])
        vault = server.decrypted()
        self.assertEqual(vault["FreeLLMAPI"]["folder"], FOLDER)
        self.assertEqual(vault["FreeLLMAPI"]["uris"], [("https://h.ts.net:8455", vw.MATCH_HOST)])
        self.assertEqual(vault["Groq"]["folder"], PROVIDER_FOLDER)
        self.assertEqual(len(server.folders), 2)

    def test_rerun_is_idempotent(self):
        server = FakeVaultwarden()
        items = [item("service:freellmapi", "FreeLLMAPI", "https://h.ts.net:8455", "gen-1", rotate=True)]
        self.run_seed(server, items)
        result = self.run_seed(server, items)
        self.assertEqual((result.created, result.updated), ([], []))
        self.assertEqual(len(server.ciphers), 1)
        self.assertEqual(len(server.folders), 1)

    def test_managed_app_password_rotates_and_keeps_history(self):
        server = FakeVaultwarden()
        self.run_seed(server, [item("service:x", "X", "https://h.ts.net:8448", "old", rotate=True)])
        result = self.run_seed(server, [item("service:x", "X", "https://h.ts.net:8448", "new", rotate=True)])
        self.assertEqual(result.updated, ["X"])
        self.assertEqual(server.decrypted()["X"]["password"], "new")
        self.assertEqual(server.decrypted()["X"]["history"], 1)

    def test_provider_signup_password_is_never_overwritten(self):
        server = FakeVaultwarden()
        self.run_seed(server, [item("provider:groq", "Groq", "https://console.groq.com/", "used-to-sign-up")])
        self.run_seed(server, [item("provider:groq", "Groq", "https://console.groq.com/", "fresh")])
        self.assertEqual(server.decrypted()["Groq"]["password"], "used-to-sign-up")

    def test_owner_saved_logins_are_not_duplicated(self):
        server = FakeVaultwarden()
        server.add_login("My Groq", "https://groq.com/login")
        server.add_login("My Authentik", "https://h.ts.net/")
        result = self.run_seed(
            server,
            [
                item("provider:groq", "Groq", "https://console.groq.com/", "p"),
                item("service:authentik", "Authentik", "https://h.ts.net"),
                item("service:litellm", "Other port", "https://h.ts.net:8454", "p"),
            ],
        )
        self.assertEqual(result.skipped, ["Groq", "Authentik"])
        self.assertEqual(result.created, ["Other port"])
        self.assertEqual(server.decrypted()["My Groq"]["password"], "mine")

    def test_organization_items_are_ignored(self):
        server = FakeVaultwarden()
        server.ciphers.append({"id": "org", "type": 1, "organizationId": "o", "name": "2.x|y|z"})
        result = self.run_seed(server, [item("provider:groq", "Groq", "https://console.groq.com/", "p")])
        self.assertEqual(result.created, ["Groq"])


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


ROUTE_HEADERS = {
    "x-mu3lab-proxy-token": "real-token",
    "x-authentik-username": "owner",
    "x-authentik-uid": "subject",
    "x-authentik-email": EMAIL,
    "x-authentik-groups": "mu3lab-operators",
    "host": "testserver",
    "origin": "https://testserver",
    "x-mu3lab-csrf": "bound",
}


class VaultRouteTests(unittest.TestCase):
    def setUp(self):
        for target, value in (
            ("ctl.api.security.ingress_token", "real-token"),
            ("ctl.api.security.csrf_token", "bound"),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = FakeVaultwarden()
        client = httpx.Client(transport=self.server.transport())
        for target, value in (
            ("ctl.api.routes.vault.VaultSession", lambda url: vw.VaultSession(url, client=client)),
            ("ctl.api.routes.vault._host", lambda: "h.ts.net"),
            ("ctl.api.routes.vault.JobStore.runtime", lambda: None),
            ("ctl.api.routes.vault.ControlState.runtime", lambda: None),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(create_app())

    def test_setup_seeds_the_vault_and_is_not_cached(self):
        response = self.client.post(
            "/api/v1/vault/setup", headers=ROUTE_HEADERS, json={"email": EMAIL, "master_password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Groq", response.json()["created"])
        self.assertNotIn(PASSWORD, response.text)

    def test_wrong_master_password_is_401(self):
        response = self.client.post(
            "/api/v1/vault/setup", headers=ROUTE_HEADERS, json={"email": EMAIL, "master_password": "nope"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "invalid_credentials")


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
        caddy = (Path(__file__).resolve().parents[1] / "apps" / "ingress" / "Caddyfile.authenticated").read_text()
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
