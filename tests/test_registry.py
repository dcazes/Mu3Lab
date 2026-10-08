"""Registry invariants: only curated, internally safe Compose definitions load."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl.backups import Retention
from ctl.manifest.catalog import compose_images
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.service_state import _compose_state, public_url
from ctl.service_state import status as service_status
from tests.support import runtime_paths

ROOT = Path(__file__).resolve().parents[1]


class _StrictLoader(yaml.SafeLoader):
    """A repeated key silently replaces the earlier one; refuse it instead."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _value in node.value]
        repeated = {key for key in keys if keys.count(key) > 1}
        if repeated:
            raise yaml.constructor.ConstructorError(None, None, f"repeated keys {repeated}", node.start_mark)
        return super().construct_mapping(node, deep=deep)


class RegistryTests(unittest.TestCase):
    def test_manifests_never_repeat_a_key(self):
        for path in sorted((ROOT / "apps").glob("**/*.yaml")):
            with self.subTest(path=path.relative_to(ROOT)):
                yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)

    def test_baby_buddy_uses_loopback_only_trusted_header_auth(self):
        service = load().get("baby-buddy")
        compose = yaml.safe_load((ROOT / service.compose_dir / "docker-compose.yml").read_text(encoding="utf-8"))
        app = compose["services"]["baby-buddy"]
        self.assertEqual(app["ports"], ["127.0.0.1:8002:8000"])
        self.assertEqual(app["environment"]["REVERSE_PROXY_AUTH"], "True")
        self.assertEqual(app["environment"]["PROXY_HEADER"], "HTTP_REMOTE_USER")
        self.assertTrue(any("mu3lab_auth.py" in volume for volume in app["volumes"]))
        self.assertTrue(any("90-mu3lab-auth" in volume for volume in app["volumes"]))
        self.assertNotIn("docker.sock", str(app))

    def test_baby_buddy_materialization_preserves_secret_and_public_url(self):
        from ctl.engine.runtime import render_service as materialize
        from ctl.secrets import read_runtime_env

        service = load().get("baby-buddy")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            with (
                runtime_paths(paths),
                patch("ctl.engine.runtime.tailnet_dns_name", return_value="mu3lab.example.ts.net"),
            ):
                project = materialize(service, ROOT)
                first = read_runtime_env(project / ".env")["BABY_BUDDY_SECRET_KEY"]
                materialize(service, ROOT)
                values = read_runtime_env(project / ".env")
                second = values["BABY_BUDDY_SECRET_KEY"]
            self.assertTrue(first)
            self.assertEqual(first, second)
            self.assertEqual(values["BABY_BUDDY_PUBLIC_URL"], "https://mu3lab.example.ts.net:8458")
            self.assertEqual((project / ".env").stat().st_mode & 0o777, 0o600)

    def test_successful_one_shot_migration_does_not_make_app_look_stopped(self):
        def fake_run(*_args, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    '{"State":"running","Status":"Up 10 minutes (healthy)"}\n'
                    '{"State":"exited","Status":"Exited (0) 10 minutes ago"}\n'
                ),
            )

        self.assertEqual(_compose_state(Path("/srv/mu3lab/projects/surfsense/docker-compose.yml"), fake_run), "running")

    def test_checked_in_registry_marks_surfsense_installable_and_local_account(self):
        registry = load()
        self.assertEqual(registry.get("surfsense").stage, "optional")
        self.assertEqual(registry.get("surfsense").auth, "proxy")
        self.assertIn("no single sign-on", registry.get("surfsense").identity_note)
        compose = registry.get("surfsense").compose_path(Path(__file__).resolve().parents[1]) / "docker-compose.yml"
        compose_text = compose.read_text(encoding="utf-8")
        self.assertNotIn("docker.sock", compose_text)
        image_lines = [line.strip() for line in compose_text.splitlines() if line.strip().startswith("image:")]
        self.assertTrue(image_lines)
        self.assertTrue(all("@sha256:" in line for line in image_lines))
        self.assertFalse(registry.catalog.get("vaultwarden").connectors)

    def test_tailnet_url_never_falls_back_to_localhost(self):
        service = load().get("litellm")
        self.assertEqual(public_url(service, ""), "")
        self.assertEqual(public_url(service, "mu3lab.example.ts.net"), "")
        ingress = load().get("ingress")
        self.assertEqual(public_url(ingress, "mu3lab.example.ts.net"), "")

    def test_browser_ui_contract_distinguishes_services_from_internal_apis(self):
        registry = load()
        self.assertFalse(registry.get("ingress").ui["available"])
        self.assertFalse(registry.get("ollama").ui["available"])
        self.assertTrue(registry.get("authentik").ui["available"])
        self.assertTrue(registry.get("litellm").ui["available"])
        self.assertTrue(registry.get("freellmapi").ui["available"])
        self.assertTrue(registry.get("nextcloud").ui["available"])

    def test_firecrawl_has_a_complete_private_login_free_runtime(self):
        service = load().get("firecrawl")
        self.assertEqual(service.stage, "core")
        self.assertEqual(service.auth, "excluded")
        self.assertEqual(service.private_https_port, 8456)
        self.assertEqual(service.proxy_port, 19473)
        compose = yaml.safe_load((ROOT / service.compose_dir / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertEqual(set(compose["services"]), {"api", "playwright-service", "redis", "rabbitmq", "nuq-postgres"})
        self.assertTrue(all("@sha256:" in contract["image"] for contract in compose["services"].values()))
        self.assertEqual(compose["services"]["playwright-service"]["command"], "node dist/api.js")
        self.assertIn("mu3lab_frontend", compose["services"]["playwright-service"]["networks"])
        self.assertNotIn(
            "authentik", (ROOT / service.compose_dir / "docker-compose.yml").read_text(encoding="utf-8").lower()
        )

    def test_excluded_authentication_service_is_not_classified_as_local_login(self):
        from ctl.identity import mode_for

        self.assertEqual(mode_for(load().get("firecrawl")), "none")

    def test_firecrawl_materialization_generates_all_runtime_secrets(self):
        from ctl.engine.runtime import render_service as materialize
        from ctl.secrets import read_runtime_env

        service = load().get("firecrawl")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            with (
                runtime_paths(paths),
                patch("ctl.engine.runtime.tailnet_dns_name", return_value="mu3lab.example.ts.net"),
            ):
                project = materialize(service, ROOT)
            values = read_runtime_env(project / ".env")
            for key in ("POSTGRES_PASSWORD", "RABBITMQ_DEFAULT_PASS", "BULL_AUTH_KEY"):
                self.assertTrue(values[key])
            self.assertEqual((project / ".env").stat().st_mode & 0o777, 0o600)

    def test_lobechat_is_required_persistent_oidc_chat(self):
        from ctl.engine.runtime import render_service as materialize
        from ctl.identity import mode_for
        from ctl.secrets import read_runtime_env

        service = load().get("lobehub")
        self.assertTrue(service.required)
        self.assertEqual(service.stage, "core")
        self.assertEqual(service.dependencies, ("litellm", "authentik"))
        self.assertEqual(mode_for(service), "native_oidc")
        compose = yaml.safe_load((ROOT / service.compose_dir / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertEqual(
            set(compose["services"]), {"app", "edge", "postgres", "redis", "rustfs", "rustfs-init", "storage-init"}
        )
        self.assertTrue(all("@sha256:" in item["image"] for item in compose["services"].values()))
        self.assertEqual(compose["services"]["app"]["environment"]["OPENAI_PROXY_URL"], "http://litellm:4000/v1")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            litellm = paths.projects / "litellm"
            litellm.mkdir(parents=True)
            (litellm / ".env").write_text("LITELLM_MASTER_KEY=test-master-key\n", encoding="utf-8")
            with (
                runtime_paths(paths),
                patch("ctl.engine.runtime.tailnet_dns_name", return_value="mu3lab.example.ts.net"),
            ):
                project = materialize(service, ROOT)
            values = read_runtime_env(project / ".env")
            launcher = (project / "Caddyfile").read_text()
            self.assertIn("handle /__mu3lab/login", launcher)
            self.assertIn("/api/auth/sign-in/oauth2", launcher)
            self.assertIn("/api/auth/get-session", launcher)
            self.assertIn("Content-Security-Policy", launcher)
            self.assertNotIn("{{sign_in_launch}}", launcher)
            self.assertEqual(values["AUTH_SSO_PROVIDERS"], "authentik")
            self.assertEqual(values["AUTH_DISABLE_EMAIL_PASSWORD"], "1")
            self.assertEqual(values["LITELLM_MASTER_KEY"], "test-master-key")
            self.assertEqual(values["APP_URL"], "https://mu3lab.example.ts.net:8457")
            self.assertIn("/api/auth/callback/authentik", service.manifest.sign_in.oidc.redirect_paths)
            self.assertFalse((paths.projects / "authentik" / "blueprints").exists())

    def test_foundation_images_are_pinned_and_planned_services_are_not_routable(self):
        registry = load()
        self.assertTrue(compose_images(ROOT / "apps" / "vaultwarden" / "docker-compose.yml"))
        self.assertEqual(registry.get("lobehub").route, "pending")

    def test_runtime_paths_are_outside_checkout(self):
        from ctl.runtime import PRODUCTION_RUNTIME_ROOT

        paths = RuntimePaths(PRODUCTION_RUNTIME_ROOT)
        self.assertEqual(paths.root, Path("/srv/mu3lab"))
        self.assertEqual(paths.backups, Path("/srv/mu3lab/backups"))
        checkout = Path(__file__).resolve().parent.parent
        self.assertNotIn(checkout, paths.root.parents)

    def test_backup_policy_matches_the_product_default(self):
        self.assertEqual(Retention().restic_args(), ["--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "12"])

    def test_core_compose_files_are_valid_and_pin_identity_images(self):
        root = Path(__file__).resolve().parents[1]
        for rel in ("apps/authentik/docker-compose.yml", "apps/vaultwarden/docker-compose.yml"):
            with self.subTest(rel=rel):
                compose = yaml.safe_load((root / rel).read_text(encoding="utf-8"))
                self.assertIn("services", compose)
        authentik = (root / "apps/authentik/.env.example").read_text(encoding="utf-8")
        self.assertNotIn("AUTHENTIK_TAG=latest", authentik)

    def test_core_suite_has_a_compose_manifest_and_pinned_registry_images(self):
        registry = load()
        root = Path(__file__).resolve().parents[1]
        core = [service for service in registry.services if service.manifest.tier == "core"]
        self.assertEqual({service.id for service in core}, {"ollama", "freellmapi", "litellm", "lobehub", "firecrawl"})
        for service in core:
            images = compose_images(service.compose_path(root) / "docker-compose.yml")
            self.assertTrue(images, service.id)
            self.assertTrue(all("@sha256:" in image for image in images.values()))

    def test_ingress_matches_tailnet_host_headers_on_loopback(self):
        caddyfile = (Path(__file__).resolve().parents[1] / "apps/ingress/Caddyfile").read_text(encoding="utf-8")
        self.assertIn(":19460 {", caddyfile)
        self.assertIn("bind 127.0.0.1", caddyfile)
        self.assertNotIn("http://127.0.0.1:19460 {", caddyfile)
        self.assertNotIn("header_up X-Mu3Lab-Proxy-Token", caddyfile)

    def test_ingress_health_uses_dedicated_caddy_endpoint(self):
        self.assertTrue(load().get("ingress").health["url"].endswith("/__mu3lab_caddy_health"))

    def test_dashboard_catalog_describes_apps_separately_from_sign_in(self):
        from ctl.api.routes.system import catalog

        registry = load()
        with patch("ctl.api.routes.system.load_registry", return_value=registry):
            result = catalog({"writes_enabled": True}).model_dump()
        self.assertTrue(result["ok"])
        self.assertEqual(set(result["services"]), {service.id for service in registry.services})
        for service in registry.services:
            with self.subTest(service=service.id):
                self.assertTrue(service.summary.strip())
                self.assertEqual(result["services"][service.id]["summary"], service.summary)
                self.assertNotEqual(service.summary, service.identity_note)
                self.assertNotEqual(service.summary, service.setup_action)
                # The Apps list shows the tagline; the detail page shows the summary.
                self.assertTrue(0 < len(service.tagline) <= 45, service.tagline)
                self.assertEqual(result["services"][service.id]["tagline"], service.tagline)

    def test_dashboard_catalog_legacy_manifest_uses_setup_copy(self):
        from dataclasses import replace

        from ctl.api.routes.system import catalog

        service = replace(load().get("mealie"), summary="")
        with patch("ctl.api.routes.system.load_registry", return_value=SimpleNamespace(services=[service])):
            result = catalog({"writes_enabled": True}).model_dump()
        self.assertEqual(result["services"][service.id]["summary"], service.setup_action)

    def test_core_suite_profile_lists_the_core_tier(self):
        from ctl.api.routes.system import catalog

        profile = catalog({}).model_dump()["profiles"][0]
        self.assertEqual(set(profile["services"]), {"ollama", "freellmapi", "litellm", "lobehub", "firecrawl"})

    def test_nextcloud_is_curated_productivity_with_unique_private_ports(self):
        registry = load()
        service = registry.get("nextcloud")
        self.assertEqual(service.stage, "optional")
        self.assertEqual(service.category, "productivity")
        self.assertEqual(service.private_https_port, 8453)
        self.assertEqual(service.proxy_port, 19470)
        self.assertEqual(service.account["mode"], "environment_bootstrap")
        ports = [item.private_https_port for item in registry.services if item.private_https_port]
        proxies = [item.proxy_port for item in registry.services if item.proxy_port]
        self.assertEqual(len(ports), len(set(ports)))
        self.assertEqual(len(proxies), len(set(proxies)))

    def test_generated_tokens_never_start_with_an_option_dash(self):
        from ctl.engine.project import token as _token

        with patch("ctl.engine.project.secrets.token_urlsafe", side_effect=["-jAbc", "_ok", "kOk"]):
            self.assertEqual(_token(36), "_ok")

    def test_nextcloud_materialization_generates_private_runtime_secrets_and_oidc(self):
        from ctl.engine.runtime import render_service as materialize
        from ctl.secrets import read_runtime_env

        root = Path(__file__).resolve().parents[1]
        service = load().get("nextcloud")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            with (
                runtime_paths(paths),
                patch("ctl.engine.runtime.tailnet_dns_name", return_value="mu3lab.example.ts.net"),
            ):
                project = materialize(service, root)
            values = read_runtime_env(project / ".env")
            self.assertEqual(values["NEXTCLOUD_OIDC_CLIENT_ID"], "mu3lab-nextcloud")
            self.assertTrue(values["NEXTCLOUD_DB_PASSWORD"])
            self.assertTrue(values["NEXTCLOUD_REDIS_PASSWORD"])
            self.assertEqual((project / ".env").stat().st_mode & 0o777, 0o600)
            self.assertIn("/apps/user_oidc/code", service.manifest.sign_in.oidc.redirect_paths)
            self.assertFalse((paths.projects / "authentik" / "blueprints").exists())

    def test_multi_container_apps_keep_generic_backing_hostnames_private(self):
        root = Path(__file__).resolve().parents[1]
        generic_aliases: set[str] = set()
        private_networks = {
            "adventurelog": "adventurelog_internal",
            "paperless-ngx": "paperless_internal",
            "nextcloud": "nextcloud_internal",
            "surfsense": "surfsense_internal",
            "immich": "immich_internal",
        }
        for service_id, private_network in private_networks.items():
            compose = yaml.safe_load(
                (root / load().get(service_id).compose_dir / "docker-compose.yml").read_text(encoding="utf-8")
            )
            self.assertIn(private_network, compose["networks"], service_id)
            for _name, contract in compose["services"].items():
                networks = contract.get("networks", [])
                backend = networks.get("mu3lab_backend", {}) if isinstance(networks, dict) else {}
                generic_aliases.update(backend.get("aliases", []) if isinstance(backend, dict) else [])
        self.assertFalse({"db", "redis", "broker", "app"}.intersection(generic_aliases))

    def test_healthy_service_without_private_route_needs_setup(self):
        service = load().get("lobehub")
        root = Path(__file__).resolve().parents[1]
        with (
            patch("ctl.service_state._compose_state", return_value="running"),
            patch("ctl.service_state._healthy", return_value=(True, "HTTP 200")),
            patch("ctl.service_state._tailnet_route_present", return_value=False),
        ):
            state = service_status(service, "", root)
        self.assertEqual(state["lifecycle_state"], "needs_setup")
        self.assertEqual(state["health_state"], "healthy")
        self.assertEqual(state["route_state"], "pending")
        self.assertEqual(state["user_action"], "Private HTTPS route pending")

    def test_core_lobechat_status_uses_its_materialized_compose_project(self):
        service = load().get("lobehub")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            project = paths.projects / "lobehub"
            project.mkdir(parents=True)
            (project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            with (
                patch("ctl.service_state.RuntimePaths", return_value=paths),
                patch("ctl.service_state._compose_state", return_value="running") as compose,
                patch("ctl.service_state._healthy", return_value=(True, "HTTP 200")),
                patch("ctl.service_state.route_probe", return_value=(True, 0.0)),
            ):
                state = service_status(service, "mu3lab.example.ts.net", ROOT, {8457})
        self.assertEqual(compose.call_args.args[0], project / "docker-compose.yml")
        self.assertEqual(state["state"], "ready")
        self.assertEqual(state["url"], "https://mu3lab.example.ts.net:8457")

    def test_stack_started_from_the_checkout_is_not_reported_missing(self):
        # Older installers started Vaultwarden from apps/ even though a rendered project also exists.
        service = load().get("vaultwarden")
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            project = paths.projects / "vaultwarden"
            project.mkdir(parents=True)
            (project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            source = str((ROOT / service.compose_dir).resolve())
            with (
                patch("ctl.service_state.RuntimePaths", return_value=paths),
                patch("ctl.service_state._healthy", return_value=(True, "HTTP 200")),
                patch("ctl.service_state.route_probe", return_value=(True, 0.0)),
            ):
                state = service_status(service, "mu3lab.example.ts.net", ROOT, {8443}, {source: "running"})
        self.assertEqual(state["lifecycle_state"], "ready")

    def test_healthy_litellm_dashboard_requires_its_private_route(self):
        service = load().get("litellm")
        root = Path(__file__).resolve().parents[1]
        with (
            patch("ctl.service_state._compose_state", return_value="running"),
            patch("ctl.service_state._healthy", return_value=(True, "HTTP 200")),
            patch("ctl.service_state._tailnet_route_present", return_value=False),
        ):
            state = service_status(service, "", root)
        self.assertEqual(state["lifecycle_state"], "needs_setup")
        self.assertEqual(state["route_state"], "pending")
        self.assertFalse(state["route_ready"])
