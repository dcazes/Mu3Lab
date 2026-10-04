from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from ctl.control_state import ControlState
from ctl.identity import launch_path, mode_for, projection, remove_sign_in, sync_sign_in
from ctl.integrations.authentik import Authentik
from ctl.manifest.catalog import App, Catalog
from ctl.registry import load
from ctl.runtime import RuntimePaths


class IdentityContractTests(unittest.TestCase):
    def test_authentication_modes_are_not_conflated(self):
        registry = load()
        self.assertEqual(mode_for(registry.get("nextcloud")), "native_oidc")
        self.assertEqual(mode_for(registry.get("lobehub")), "native_oidc")
        self.assertEqual(mode_for(registry.get("baby-buddy")), "trusted_header")
        self.assertEqual(mode_for(registry.get("litellm")), "proxy_gate")
        self.assertEqual(mode_for(registry.get("surfsense")), "proxy_gate")
        self.assertEqual(mode_for(registry.get("vaultwarden")), "local")
        self.assertEqual(mode_for(registry.get("ollama")), "none")
        self.assertEqual(mode_for(registry.get("firecrawl")), "none")

    def test_login_free_api_uses_its_identity_note(self):
        service = load().get("firecrawl")
        identity = projection(service, {"route_ready": True, "health_state": "healthy"}, None)
        self.assertEqual(identity["mode"], "none")
        self.assertEqual(identity["detail"], service.identity_note)

    def test_native_oidc_never_becomes_ready_from_manifest_alone(self):
        service = load().get("nextcloud")
        item = {"route_ready": True, "health_state": "healthy", "ui": {"url": "https://host.example:8453"}}
        identity = projection(service, item, None)
        self.assertEqual(identity["state"], "unconfigured")

    def test_nextcloud_launcher_enters_oidc_flow_directly(self):
        service = load().get("nextcloud")
        identity = projection(
            service,
            {
                "route_ready": True,
                "health_state": "healthy",
                "url": "https://host.example:8453",
            },
            None,
        )
        self.assertEqual(identity["launch_url"], "https://host.example:8453/index.php/apps/user_oidc/login/1")

    def test_identity_provider_projection_remains_ready_without_an_app_id_branch(self):
        source = load().get("authentik")
        for healthy, expected in ((True, "ready"), (False, "degraded")):
            with self.subTest(healthy=healthy):
                item = {"route_ready": healthy, "health_state": "healthy" if healthy else "unhealthy"}
                self.assertEqual(projection(source, item, None)["state"], expected)

    def test_identity_state_is_additive_and_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            state.set_service_identity(
                "nextcloud",
                "native_oidc",
                "migration_required",
                owner_uid="owner-subject",
                job_id="job-1",
                detail="callback required",
            )
            reopened = ControlState(Path(tmp) / "control.sqlite3").service_identity("nextcloud")
            self.assertEqual(reopened["owner_uid"], "owner-subject")
            self.assertEqual(reopened["state"], "migration_required")

    def test_launch_path_uses_saved_env_and_manifest_default(self):
        manifest = load().get("nextcloud").manifest
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            self.assertTrue(launch_path(manifest, paths).endswith("/1"))
            project = paths.projects / manifest.id
            project.mkdir(parents=True)
            (project / ".env").write_text("NEXTCLOUD_OIDC_PROVIDER_ID=7\n")
            self.assertTrue(launch_path(manifest, paths).endswith("/7"))

    def test_sync_uses_manifests_saved_secrets_and_only_installed_optional_apps(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            registry = load()
            # Rename a real manifest to prove there is no app-ID lookup table.
            source = registry.catalog.get("nextcloud")
            custom = App(
                source.manifest.model_copy(update={"id": "custom-cloud", "name": "Custom Cloud"}), source.folder
            )
            catalog = Catalog((*(app for app in registry.catalog.apps if app.id != source.id), custom))
            project = paths.projects / custom.id
            project.mkdir(parents=True)
            env = "NEXTCLOUD_OIDC_CLIENT_ID=custom-client\nNEXTCLOUD_OIDC_CLIENT_SECRET=preserved-secret\n"
            (project / ".env").write_text(env)
            bodies = []
            client = self._client(bodies)
            self.assertEqual(sync_sign_in(catalog, "mu3lab.example.ts.net", client, paths), [])
            self.assertNotIn("preserved-secret", bodies[-1])
            (project / "docker-compose.yml").write_text("services: {}\n")
            self.assertEqual(sync_sign_in(catalog, "mu3lab.example.ts.net", client, paths), [custom.id])
            self.assertIn("preserved-secret", bodies[-1])
            self.assertIn("slug: mu3lab-custom-cloud", bodies[-1])
            self.assertIn("/apps/user_oidc/code", bodies[-1])
            self.assertIn("email_verified", bodies[-1])
            self.assertEqual((project / ".env").read_text(), env)
            self.assertFalse((paths.projects / "authentik" / "blueprints").exists())

    def test_gates_track_install_remove_and_reinstall_in_synchronous_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load().catalog
            bodies = []
            client = self._client(bodies)
            sync_sign_in(catalog, "mu3lab.example.ts.net", client, paths)
            self.assertNotIn("Mu3Lab SurfSense provider", bodies[-1])
            self.assertIn("Mu3Lab LiteLLM provider", bodies[-1])
            self.assertIn("target: !KeyOf mu3lab-litellm-application", bodies[-1])
            for app_id in ("surfsense", "baby-buddy"):
                project = paths.projects / app_id
                project.mkdir(parents=True)
                (project / "docker-compose.yml").write_text("services: {}\n")
            sync_sign_in(catalog, "mu3lab.example.ts.net", client, paths)
            self.assertIn("Mu3Lab SurfSense provider", bodies[-1])
            self.assertIn("Mu3Lab Baby Buddy provider", bodies[-1])
            remove_sign_in(catalog.get("surfsense"), client, catalog=catalog, host="mu3lab.example.ts.net", paths=paths)
            self.assertNotIn("Mu3Lab SurfSense provider", bodies[-2])
            self.assertIn("Mu3Lab Baby Buddy provider", bodies[-2])
            self.assertIn("slug: mu3lab-surfsense", bodies[-1])
            self.assertIn("state: absent", bodies[-1])
            sync_sign_in(catalog, "mu3lab.example.ts.net", client, paths)
            self.assertIn("Mu3Lab SurfSense provider", bodies[-1])

    def test_owner_guard_is_read_from_the_manifest_env_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            catalog = load().catalog
            project = paths.projects / "actual-budget"
            project.mkdir(parents=True)
            (project / "docker-compose.yml").write_text("services: {}\n")
            (project / ".env").write_text(
                "ACTUAL_OPENID_CLIENT_ID=x\nACTUAL_OPENID_CLIENT_SECRET=y\nMU3LAB_INITIAL_OWNER_USERNAME=owner\n"
            )
            bodies = []
            sync_sign_in(catalog, "mu3lab.example.ts.net", self._client(bodies), paths)
            self.assertIn("request.user.username == 'owner'", bodies[-1])

    def _client(self, bodies):
        def send(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/v3/managed/blueprints/import/")
            self.assertEqual(request.headers["authorization"], "Bearer test-token")
            bodies.append(request.content.decode())
            return httpx.Response(200, json={"success": True})

        return Authentik("test-token", transport=httpx.MockTransport(send))

    def test_every_authentik_provider_skips_the_consent_page(self):
        # A consent page left open loses its place when another app starts a
        # sign-in in the same browser, and Authentik drops the person on its
        # own library instead of the app.
        from ctl.authentik_blueprints import GatedApp, OidcApp, render_gate_blueprint, render_oidc_blueprint

        gated = render_gate_blueprint(
            "mu3lab.example.ts.net", 8446, (GatedApp("surfsense", "SurfSense", 8447, "household"),)
        )
        oidc = render_oidc_blueprint(
            "mu3lab.example.ts.net", OidcApp("mealie", "Mealie", 8450, "mu3lab-mealie", "secret", ("/login",))
        )
        for content in (gated, oidc):
            self.assertNotIn("explicit-consent", content)
            self.assertIn("default-provider-authorization-implicit-consent", content)

    def test_admitted_people_carry_the_group_names_apps_gate_on(self):
        from ctl.authentik_blueprints import OidcApp, render_oidc_blueprint

        content = render_oidc_blueprint(
            "mu3lab.example.ts.net", OidcApp("mealie", "Mealie", 8450, "mu3lab-mealie", "secret", ("/login",))
        )
        expression = content.split("expression: |\n", 1)[1].split("  - id:", 1)[0]
        body = "\n".join(line[8:] for line in expression.splitlines())

        def claims(*groups: str) -> dict:
            class Groups:
                def all(self):
                    return [type("G", (), {"name": name}) for name in groups]

            user = type("U", (), {"ak_groups": Groups(), "email": "a@b.test", "username": "a", "name": "A"})
            request = type("R", (), {"user": user})
            scope: dict = {"request": request}
            exec("def mapping():\n" + "\n".join("    " + line for line in body.splitlines()), scope)
            return scope["mapping"]()

        owner = claims("authentik Admins")
        self.assertIn("mu3lab-users", owner["groups"])
        self.assertIn("mu3lab-operators", owner["groups"])
        self.assertEqual(owner["mu3lab_role"], "admin")
        member = claims("mu3lab-household")
        self.assertIn("mu3lab-users", member["groups"])
        self.assertNotIn("mu3lab-operators", member["groups"])
        self.assertEqual(claims()["groups"], [])


if __name__ == "__main__":
    unittest.main()
