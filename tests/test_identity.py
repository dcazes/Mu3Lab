from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


from ctl.control_state import ControlState
from ctl.identity import launch_path, mode_for, projection
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

    def test_the_launcher_starts_at_the_origin_when_the_start_page_is_deeper(self):
        service = load().get("contextforge")
        item = {"route_ready": True, "health_state": "healthy", "url": "https://host.example:8475/admin/"}
        identity = projection(service, item, None)
        self.assertEqual(identity["launch_url"], "https://host.example:8475/__mu3lab/login")

    def test_a_turned_off_app_is_off_not_degraded(self):
        service = load().get("audiobookshelf")
        state = SimpleNamespace(service_identity=lambda _id: {"state": "ready", "detail": ""})
        stopped = {"state": "stopped", "route_ready": False, "health_state": "unknown"}
        identity = projection(service, stopped, state)
        self.assertEqual(identity["state"], "off")
        # Running but unreachable is still a real problem.
        running = {**stopped, "state": "running"}
        self.assertEqual(projection(service, running, state)["state"], "degraded")

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

    def test_gated_household_apps_admit_only_the_household_and_administrators(self):
        # Review C-L1: an Authentik account outside both groups is refused, like at the OIDC apps.
        import re

        from ctl.authentik_blueprints import GatedApp, render_gate_blueprint

        content = render_gate_blueprint(
            "mu3lab.example.ts.net",
            8446,
            (GatedApp("grocy", "Grocy", 8447, "household"), GatedApp("litellm", "LiteLLM", 8454, "operators")),
        )
        groups: dict[str, list[str]] = {}
        for target, group in re.findall(r"target: !KeyOf (\S+)\n.*\n.*\n.*\[name, ([^\]]+)\]", content):
            groups.setdefault(target, []).append(group)
        self.assertEqual(
            groups["mu3lab-grocy-application"], ["authentik Admins", "mu3lab-operators", "mu3lab-household"]
        )
        self.assertEqual(groups["mu3lab-litellm-application"], ["authentik Admins", "mu3lab-operators"])

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
