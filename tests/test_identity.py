from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.control_state import ControlState
from ctl.identity import mode_for, projection, reconcile_blueprints
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

    def test_missing_blueprint_is_restored_without_rotating_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime")
            project = paths.projects / "nextcloud"
            project.mkdir(parents=True)
            (project / ".env").write_text(
                "NEXTCLOUD_OIDC_CLIENT_ID=mu3lab-nextcloud\nNEXTCLOUD_OIDC_CLIENT_SECRET=preserved-secret\n",
                encoding="utf-8",
            )
            # Uninstalled with data kept: credentials remain but it is not registered.
            self.assertEqual(reconcile_blueprints(load(), "mu3lab.example.ts.net", paths), [])
            (project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            written = reconcile_blueprints(load(), "mu3lab.example.ts.net", paths)
            self.assertIn("nextcloud", written)
            blueprint = paths.projects / "authentik" / "blueprints" / "mu3lab-nextcloud.yaml"
            content = blueprint.read_text(encoding="utf-8")
            self.assertIn("preserved-secret", content)
            self.assertIn("email_verified", content)
            self.assertIn("preferred_username", content)
            self.assertIn('meta_launch_url: "blank://blank"', content)
            self.assertEqual((project / ".env").read_text(encoding="utf-8").count("preserved-secret"), 1)

    def test_installed_trusted_header_app_is_registered_with_the_outpost(self):
        # Caddy sends Baby Buddy through the outpost; without a provider for
        # its host the outpost answers every request with a 404.
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime")
            blueprint = paths.projects / "authentik" / "blueprints" / "mu3lab-dashboard.yaml"
            reconcile_blueprints(load(), "mu3lab.example.ts.net", paths)
            self.assertNotIn("Baby Buddy", blueprint.read_text(encoding="utf-8"))
            (paths.projects / "baby-buddy").mkdir(parents=True)
            (paths.projects / "baby-buddy" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            reconcile_blueprints(load(), "mu3lab.example.ts.net", paths)
            content = blueprint.read_text(encoding="utf-8")
            self.assertIn('external_host: "https://mu3lab.example.ts.net:8458"', content)
            outpost = content.split("authentik_outposts.outpost", 1)[1]
            self.assertIn("[name, Mu3Lab Baby Buddy provider]", outpost)

    def test_surfsense_provider_tracks_install_uninstall_and_reinstall(self):
        from ctl.authentik_blueprints import write_removal_blueprint
        from ctl.lifecycle.uninstall import _remove_sign_in

        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp) / "runtime")
            registry = load()
            project = paths.projects / "surfsense"
            blueprint = paths.projects / "authentik" / "blueprints" / "mu3lab-dashboard.yaml"
            project.mkdir(parents=True)
            (project / ".env").write_text("REGISTRATION_ENABLED=TRUE\n", encoding="utf-8")
            reconcile_blueprints(registry, "mu3lab.example.ts.net", paths)
            self.assertNotIn("Mu3Lab SurfSense provider", blueprint.read_text(encoding="utf-8"))

            compose = project / "docker-compose.yml"
            compose.write_text("services: {}\n", encoding="utf-8")
            removal = write_removal_blueprint(paths.root, "surfsense", "SurfSense", oidc=False)
            reconcile_blueprints(registry, "mu3lab.example.ts.net", paths)
            content = blueprint.read_text(encoding="utf-8")
            self.assertIn('external_host: "https://mu3lab.example.ts.net:8447"', content)
            self.assertIn("[name, Mu3Lab SurfSense provider]", content.split("authentik_outposts.outpost", 1)[1])
            self.assertEqual(content.count("name: Mu3Lab LiteLLM provider"), 2)
            self.assertFalse(removal.exists())

            compose.unlink()
            with patch("ctl.service_state.tailnet_dns_name", return_value="mu3lab.example.ts.net"):
                _remove_sign_in(registry.get("surfsense"), registry, paths)
            self.assertNotIn("Mu3Lab SurfSense provider", blueprint.read_text(encoding="utf-8"))
            self.assertTrue(removal.exists())

            compose.write_text("services: {}\n", encoding="utf-8")
            reconcile_blueprints(registry, "mu3lab.example.ts.net", paths)
            self.assertIn("Mu3Lab SurfSense provider", blueprint.read_text(encoding="utf-8"))
            self.assertFalse(removal.exists())

    def test_every_authentik_provider_skips_the_consent_page(self):
        # A consent page left open loses its place when another app starts a
        # sign-in in the same browser, and Authentik drops the person on its
        # own library instead of the app.
        from ctl.authentik_blueprints import render_dashboard_blueprint, render_oidc_application_blueprint

        gated = render_dashboard_blueprint("mu3lab.example.ts.net", gated_apps=(("surfsense", "SurfSense", 8447),))
        oidc = render_oidc_application_blueprint(
            "mu3lab.example.ts.net",
            service_id="mealie",
            name="Mealie",
            private_port=8450,
            client_id="mu3lab-mealie",
            client_secret="secret",
            redirect_paths=("/login",),
        )
        for content in (gated, oidc):
            self.assertNotIn("explicit-consent", content)
            self.assertIn("default-provider-authorization-implicit-consent", content)

    def test_admitted_people_carry_the_group_names_apps_gate_on(self):
        from ctl.authentik_blueprints import render_oidc_application_blueprint

        content = render_oidc_application_blueprint(
            "mu3lab.example.ts.net",
            service_id="mealie",
            name="Mealie",
            private_port=8450,
            client_id="mu3lab-mealie",
            client_secret="secret",
            redirect_paths=("/login",),
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
