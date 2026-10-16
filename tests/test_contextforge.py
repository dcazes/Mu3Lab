"""ContextForge: connectors registered once, tools chosen in its screens, per-person keys for LobeChat."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import yaml

from ctl import contextforge, contextforge_ops
from ctl.authentik_blueprints import OidcApp, render_oidc_blueprint
from ctl.contextforge import ContextForge, ContextForgeError
from ctl.manifest.catalog import load as load_catalog
from ctl.mcp_catalog import load as load_connectors
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.store import records

ROOT = Path(__file__).resolve().parents[1]


class FakeContextForge:
    """ContextForge's admin API as far as Mu3Lab uses it."""

    def __init__(self):
        self.gateways: list[dict] = []
        self.tools: list[dict] = []
        self.servers: list[dict] = [{"id": "kept", "name": "Grocy (Grocy)", "associatedTools": ["chosen-by-operator"]}]
        self.users: set[str] = set()
        self.admins: set[str] = set()
        self.user_lookup_status = 0
        self.keys: list[dict] = []
        self.keys_made = 0
        self.providers: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        body = json.loads(request.content) if request.content else None
        if path == "/v1/auth/email/login":
            if body != {"email": contextforge.ADMIN_EMAIL, "password": "admin-pass"}:
                return httpx.Response(401, json={"detail": "admin-pass is wrong"})
            return httpx.Response(200, json={"access_token": "session"})
        if request.headers.get("authorization") != "Bearer session":
            return httpx.Response(401, json={})
        if path == "/v1/gateways" and method == "GET":
            return httpx.Response(200, json=self.gateways)
        if path == "/v1/gateways" and method == "POST":
            gateway = {"id": f"g{len(self.gateways) + 1}", "enabled": True, **body}
            self.gateways.append(gateway)
            self.tools += [
                {"id": f"{gateway['id']}-{name}", "originalName": name, "gatewayId": gateway["id"]}
                for name in ("search", "delete_everything")
            ]
            return httpx.Response(200, json=gateway)
        if path.startswith("/v1/gateways/") and method == "PUT":
            gateway = next(item for item in self.gateways if item["id"] == path.split("/")[3])
            gateway.update(body)
            return httpx.Response(200, json=gateway)
        if path.endswith("/state"):
            gateway = next(item for item in self.gateways if item["id"] == path.split("/")[3])
            gateway["enabled"] = request.url.params["activate"] == "true"
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/tools":
            wanted = request.url.params["gateway_id"]
            return httpx.Response(200, json=[tool for tool in self.tools if tool["gatewayId"] == wanted])
        if path == "/v1/servers" and method == "GET":
            return httpx.Response(200, json=self.servers)
        if path == "/v1/servers" and method == "POST":
            server = {"id": f"s{len(self.servers)}", **body["server"], "visibility": body["visibility"]}
            self.servers.append(server)
            return httpx.Response(201, json=server)
        if path.startswith("/v1/auth/email/admin/users/") and method == "PATCH":
            self.admins.discard(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={})
        if path.startswith("/v1/auth/email/admin/users/"):
            email = path.rsplit("/", 1)[1]
            if self.user_lookup_status:
                return httpx.Response(self.user_lookup_status, json={})
            if email not in self.users:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"email": email, "is_admin": email in self.admins})
        if path == "/v1/auth/email/admin/users":
            self.users.add(body["email"])
            return httpx.Response(201, json={})
        if path == "/v1/tokens/admin/all":
            email = request.url.params.get("user_email")
            return httpx.Response(200, json={"tokens": [key for key in self.keys if key["user_email"] == email]})
        if path == "/v1/tokens":
            key = {"id": f"k{self.keys_made}", **body}
            self.keys_made += 1
            self.keys.append(key)
            return httpx.Response(201, json={"token": key, "access_token": f"secret-{key['id']}"})
        if path.startswith("/v1/tokens/admin/") and method == "DELETE":
            self.keys = [key for key in self.keys if key["id"] != path.rsplit("/", 1)[1]]
            return httpx.Response(204)
        if path == "/v1/auth/sso/admin/providers" and method == "GET":
            return httpx.Response(200, json=[{"id": key} for key in self.providers])
        if path.startswith("/v1/auth/sso/admin/providers"):
            self.providers[body.get("id") or path.rsplit("/", 1)[1]] = body
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})


def connector(server_id: str):
    return next(server for server in load_connectors(load_registry()) if server.id == server_id)


class ContextForgeCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name))
        self.fake = FakeContextForge()

        def client(_paths=None):
            return ContextForge(
                "http://cf", contextforge.ADMIN_EMAIL, "admin-pass", transport=httpx.MockTransport(self.fake.handler)
            )

        for target, kwargs in (
            ("ctl.contextforge_ops.client", {"side_effect": client}),
            ("ctl.contextforge_ops.read_runtime_env", {"return_value": {"MCP_AUTH_TOKEN": "connector-token"}}),
            ("ctl.service_state.tailnet_dns_name", {"return_value": "home.example.ts.net"}),
            ("ctl.store.records.RuntimePaths", {"return_value": self.paths}),
            ("ctl.contextforge_ops.RuntimePaths", {"return_value": self.paths}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)


class ClientTests(ContextForgeCase):
    def test_errors_never_carry_the_response_body_or_password(self):
        with (
            ContextForge(
                "http://cf", contextforge.ADMIN_EMAIL, "wrong", transport=httpx.MockTransport(self.fake.handler)
            ) as api,
            self.assertRaises(ContextForgeError) as caught,
        ):
            api.gateways()
        self.assertNotIn("admin-pass", str(caught.exception))
        self.assertIn("HTTP 401", str(caught.exception))

    def test_a_key_acts_as_the_person_and_reaches_one_virtual_server(self):
        with contextforge_ops.client() as api:
            key_id, token = api.create_key("kid@example.test", "mu3lab-lobechat-grocy", "s1")
        self.assertEqual((key_id, token), ("k0", "secret-k0"))
        key = self.fake.keys[0]
        self.assertEqual((key["user_email"], key["scope"]), ("kid@example.test", {"server_id": "s1"}))


class ConnectTests(ContextForgeCase):
    def test_a_reviewed_connector_starts_with_its_reviewed_tools_and_an_unreviewed_one_with_all(self):
        grocy = connector("grocy-community")
        self.assertIn("inventory_stock_get_volatile", contextforge_ops.starting_tools(grocy))
        unreviewed = SimpleNamespace(id="mealie-community", review="")
        self.assertIsNone(contextforge_ops.starting_tools(unreviewed))
        self.assertEqual(contextforge.tool_ids([{"id": "a", "originalName": "x"}], None), ["a"])
        self.assertEqual(contextforge.tool_ids([{"id": "a", "originalName": "x"}], {"y"}), [])

    def test_connect_registers_once_and_never_changes_an_existing_virtual_server(self):
        server = SimpleNamespace(id="mealie-community", name="Mealie", endpoint="http://mealie-mcp:8000/mcp", review="")
        ok, _detail = contextforge_ops.connect(server, "Mealie", lambda _line: None)
        self.assertTrue(ok)
        gateway = self.fake.gateways[0]
        self.assertEqual(
            (gateway["url"], gateway["auth_type"], gateway["auth_token"]),
            (server.endpoint, "bearer", "connector-token"),
        )
        created = self.fake.servers[-1]
        self.assertEqual(created["associated_tools"], ["g1-search", "g1-delete_everything"])
        self.assertEqual(created["visibility"], "public")
        self.assertEqual(contextforge_ops.virtual_server(server.id), created["id"])
        # Open WebUI's sign-ins are trusted, but only those issued to Open WebUI.
        provider = self.fake.providers[contextforge.CHAT_PROVIDER]
        self.assertTrue(provider["trusted_for_api_auth"])
        self.assertEqual(provider["api_audience"], "mu3lab-open-webui")
        self.assertEqual(provider["issuer"], "https://home.example.ts.net/application/o/mu3lab-open-webui/")

        # An operator trims the tools in ContextForge; a reconnect leaves their choice alone.
        created["associated_tools"] = ["g1-search"]
        contextforge_ops.disconnect(server, lambda _line: None)
        self.assertFalse(gateway["enabled"])
        contextforge_ops.connect(server, "Mealie", lambda _line: None)
        self.assertTrue(gateway["enabled"])
        self.assertEqual(len(self.fake.gateways), 1)
        self.assertEqual(self.fake.servers[-1]["associated_tools"], ["g1-search"])
        self.assertEqual([item["id"] for item in self.fake.servers].count(created["id"]), 1)

    def test_an_existing_virtual_server_is_reused_by_name(self):
        with contextforge_ops.client() as api:
            kept = api.ensure_server("Grocy (Grocy)", "", ["new-tool"])
        self.assertEqual(kept["associatedTools"], ["chosen-by-operator"])
        self.assertNotIn(("POST", "/v1/servers"), self.fake.calls)


class PersonKeyTests(ContextForgeCase):
    def test_a_person_gets_one_key_per_connector_kept_until_withdrawn(self):
        first = contextforge_ops.person_key("uid-1", "kid@example.test", "grocy-community", "s1", self.paths)
        again = contextforge_ops.person_key("uid-1", "kid@example.test", "grocy-community", "s1", self.paths)
        self.assertEqual(first, again)
        self.assertEqual(len(self.fake.keys), 1)
        self.assertIn("kid@example.test", self.fake.users)
        # A different virtual server (the app switched connectors) replaces the old key.
        contextforge_ops.person_key("uid-1", "kid@example.test", "grocy-community", "s2", self.paths)
        self.assertEqual([key["scope"]["server_id"] for key in self.fake.keys], ["s2"])
        records.put(contextforge_ops.SCOPE, "grocy-community", {"server_id": "s2"}, self.paths)
        contextforge_ops.revoke_person("uid-1", "kid@example.test", self.paths)
        self.assertEqual(self.fake.keys, [])

    def test_a_key_revoked_in_contextforge_is_replaced(self):
        first = contextforge_ops.person_key("uid-1", "kid@example.test", "grocy-community", "s1", self.paths)
        # An operator revokes it in ContextForge's screens; it no longer appears among active keys.
        self.fake.keys.clear()
        second = contextforge_ops.person_key("uid-1", "kid@example.test", "grocy-community", "s1", self.paths)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.fake.keys), 1)

    def test_a_server_error_is_not_mistaken_for_a_missing_account(self):
        self.fake.user_lookup_status = 503
        with contextforge_ops.client() as api, self.assertRaises(ContextForgeError):
            api.ensure_user("kid@example.test", "kid", "random")
        self.assertNotIn(("POST", "/v1/auth/email/admin/users"), self.fake.calls)

    def test_keys_are_found_by_their_saved_email_and_never_silently_skipped(self):
        records.put(contextforge_ops.SCOPE, "grocy-community", {"server_id": "s1"}, self.paths)
        contextforge_ops.person_key("uid-1", "kid@example.test", "grocy-community", "s1", self.paths)
        # Authentik no longer gives an email: the one saved with the key still finds it.
        contextforge_ops.revoke_person("uid-1", "", self.paths)
        self.assertEqual(self.fake.keys, [])
        # A key saved before emails were kept, with no email from Authentik, stays pending.
        from ctl.store.secrets import SecretStore

        SecretStore(self.paths).put(contextforge_ops.KEY_SCOPE, "uid-2:grocy-community", {"token": "t"})
        with self.assertRaises(ContextForgeError):
            contextforge_ops.revoke_person("uid-2", "", self.paths)

    def test_admin_rights_are_removed_at_once(self):
        self.fake.users |= {"op@example.test"}
        self.fake.admins |= {"op@example.test"}
        contextforge_ops.remove_admin("op@example.test", self.paths)
        self.assertEqual(self.fake.admins, set())
        # Someone without a ContextForge account has nothing to remove.
        contextforge_ops.remove_admin("nobody@example.test", self.paths)
        self.assertNotIn(("PATCH", "/v1/auth/email/admin/users/nobody@example.test"), self.fake.calls)


class SignInTests(unittest.TestCase):
    def app(self, audience: str) -> OidcApp:
        return OidcApp(
            "contextforge",
            "ContextForge",
            8475,
            "mu3lab-contextforge",
            "s" * 40,
            ("/auth/sso/callback/mu3lab",),
            audience=audience,
        )

    def test_operator_only_oidc_apps_admit_administrators_only(self):
        content = render_oidc_blueprint("home.example.ts.net", self.app("operators"))
        bound = yaml.safe_load(content.replace("!Find", "").replace("!KeyOf", ""))["entries"]
        groups = [
            str(entry["attrs"]["group"])
            for entry in bound
            if entry["model"].endswith("policybinding") and entry.get("attrs")
        ]
        self.assertTrue(groups and all("mu3lab-household" not in group for group in groups))
        self.assertIn("offline_access", content)
        everyone = render_oidc_blueprint("home.example.ts.net", self.app("household"))
        self.assertIn("mu3lab-household", everyone)

    def test_the_contextforge_manifest_is_operator_only_and_trusts_open_webui(self):
        app = next(item for item in load_catalog().apps if item.id == "contextforge")
        self.assertEqual(app.manifest.route.audience, "operators")
        compose = yaml.safe_load((ROOT / "apps/contextforge/docker-compose.yml").read_text())
        env = compose["services"]["contextforge"]["environment"]
        self.assertEqual(env["SSO_API_TOKEN_AUTH_ENABLED"], "true")
        self.assertEqual(env["MCP_CLIENT_AUTH_ENABLED"], "true")
        self.assertIn("@sha256:", compose["services"]["contextforge"]["image"])
        # Connectors are reachable from ContextForge only.
        self.assertIn("mu3lab_mcp_upstream", compose["services"]["contextforge"]["networks"])
        for path in ROOT.glob("apps/*/connectors/*/docker-compose.yml"):
            for service in yaml.safe_load(path.read_text())["services"].values():
                self.assertNotIn("mu3lab_mcp", service.get("networks") or [], path)
        owui = yaml.safe_load((ROOT / "apps/open-webui/docker-compose.yml").read_text())
        self.assertIn("offline_access", owui["services"]["open-webui"]["environment"]["OAUTH_SCOPES"])


if __name__ == "__main__":
    unittest.main()
