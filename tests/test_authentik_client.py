"""REST contract, bounded readiness, useful failures and password hashing."""

from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ctl.integrations.authentik import Authentik, AuthentikError, password_hash
from ctl.secrets import clear_authentik_bootstrap, ensure_authentik_env, read_runtime_env


class AuthentikClientTests(unittest.TestCase):
    def test_password_hash_matches_django_pbkdf2(self):
        value = password_hash("test password", salt="known-salt", iterations=10)
        expected = base64.b64encode(hashlib.pbkdf2_hmac("sha256", b"test password", b"known-salt", 10)).decode()
        self.assertEqual(value, f"pbkdf2_sha256$10$known-salt${expected}")

    def test_defaults_wait_for_flows_and_signing_key_then_finish(self):
        calls = []

        def send(request):
            calls.append(request)
            path = request.url.path
            key = "slug" if "flows" in path else "domain" if "brands" in path else "name"
            results = [] if len(calls) == 1 else [{key: request.url.params[key]}]
            return httpx.Response(200, json={"results": results})

        with patch("ctl.integrations.authentik.time.sleep") as sleep:
            Authentik("token", transport=httpx.MockTransport(send)).wait_for_defaults()
        sleep.assert_called_once()
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[-2].url.params["name"], "authentik Self-signed Certificate")
        # Authentik's own default brand must exist before Mu3Lab sets its recovery flow.
        self.assertEqual(calls[-1].url.params["domain"], "authentik-default")

    def test_a_duplicate_default_brand_is_removed_and_authentiks_own_is_kept(self):
        deleted = []
        brands = [
            {"brand_uuid": "ours", "domain": "authentik-default", "default": False},
            {"brand_uuid": "authentik", "domain": "authentik-default", "default": True},
        ]

        def send(request):
            if request.method == "DELETE":
                deleted.append(request.url.path)
                return httpx.Response(204)
            return httpx.Response(200, json={"results": brands, "pagination": {"next": 0}})

        client = Authentik("token", transport=httpx.MockTransport(send))
        self.assertEqual(client.remove_duplicate_default_brands(), 1)
        self.assertEqual(deleted, ["/api/v3/core/brands/ours/"])
        # Without Authentik's own default brand, nothing is deleted.
        brands[:] = [{"brand_uuid": "ours", "domain": "authentik-default", "default": False}]
        deleted.clear()
        self.assertEqual(client.remove_duplicate_default_brands(), 0)
        self.assertEqual(deleted, [])

    def test_the_recovery_flow_is_set_on_authentiks_own_default_brand(self):
        import yaml

        from ctl.authentik_blueprints import render_gate_blueprint

        document = yaml.safe_load(
            render_gate_blueprint("mu3lab.example.ts.net", 8443).replace("!KeyOf", "").replace("!Find", "")
        )
        brand = next(entry for entry in document["entries"] if entry["model"] == "authentik_brands.brand")
        self.assertEqual(brand["identifiers"], {"domain": "authentik-default", "default": True})

    def test_missing_defaults_and_network_failure_time_out(self):
        for response in (httpx.Response(200, json={"results": []}), httpx.Response(503)):
            with self.subTest(response=response.status_code), patch("ctl.integrations.authentik.time.sleep") as sleep:
                client = Authentik("token", transport=httpx.MockTransport(lambda _, reply=response: reply))
                with self.assertRaisesRegex(AuthentikError, "not ready"):
                    client.wait_for_defaults(timeout=0)
                sleep.assert_not_called()

    def test_blueprint_import_waits_for_authentik_but_connects_fast(self):
        # An abandoned import keeps running and blocks the next one, so the
        # client waits it out instead of using the 30s API default.
        timeouts = {}

        def send(request):
            timeouts[request.url.path] = request.extensions["timeout"]
            return httpx.Response(200, json={"success": True, "results": []})

        client = Authentik("token", transport=httpx.MockTransport(send))
        client.apply_blueprint("test", "version: 1")
        client.request("GET", "/core/users/me/")
        self.assertEqual(timeouts["/api/v3/managed/blueprints/import/"]["read"], 300.0)
        self.assertEqual(timeouts["/api/v3/managed/blueprints/import/"]["connect"], 10.0)
        self.assertEqual(timeouts["/api/v3/core/users/me/"]["read"], 30.0)

    def test_blueprint_failure_keeps_all_warning_error_logs_and_redacts_credentials(self):
        def send(request):
            self.assertEqual(request.url.path, "/api/v3/managed/blueprints/import/")
            self.assertIn('filename="test.yaml"', request.content.decode())
            return httpx.Response(
                400,
                json={
                    "success": False,
                    "logs": [
                        {"log_level": "info", "event": "Task enqueued"},
                        *({"log_level": "warning", "event": f"warning-{index}"} for index in range(5)),
                        {"log_level": "error", "event": "invalid entry", "client_secret": "never-log-me"},
                    ],
                },
            )

        with self.assertRaises(AuthentikError) as failure:
            Authentik("token", transport=httpx.MockTransport(send)).apply_blueprint("test", "version: 1")
        text = str(failure.exception)
        self.assertIn("warning-4", text)
        self.assertIn("invalid entry", text)
        self.assertNotIn("Task enqueued", text)
        self.assertNotIn("never-log-me", text)

    def test_api_errors_are_plain_and_do_not_include_response_body(self):
        client = Authentik(
            "secret-token", transport=httpx.MockTransport(lambda _: httpx.Response(403, text="secret-body"))
        )
        with self.assertRaisesRegex(AuthentikError, "HTTP 403") as failure:
            client.users()
        self.assertNotIn("secret", str(failure.exception))

    def test_paginated_users_include_nested_groups_on_each_request(self):
        pages = []

        def send(request):
            page = int(request.url.params["page"])
            pages.append(page)
            self.assertEqual(request.url.params["include_groups"], "true")
            return httpx.Response(
                200, json={"results": [{"username": f"person{page}"}], "pagination": {"next": 2 if page == 1 else 0}}
            )

        users = Authentik("token", transport=httpx.MockTransport(send)).users()
        self.assertEqual([user["username"] for user in users], ["person1", "person2"])
        self.assertEqual(pages, [1, 2])

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
