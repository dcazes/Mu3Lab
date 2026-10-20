"""Apps with more than one port or private address (``service.extra_ports``, ``extra_routes``).

LiveKit is the first: a voice page, a signalling server browsers connect to,
an admin dashboard, and WebRTC media that only the tailnet may reach.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from pydantic import ValidationError

from ctl.engine.compose import Compose
from ctl.engine.hooks import HookContext, StepFailed
from ctl.engine.project import Facts, lookup_for
from ctl.identity import gate_blueprint
from ctl.manifest import catalog
from ctl.manifest.models import AppManifest
from ctl.registry import load as load_registry
from ctl.routes import _block, render, upstream_auth_name
from ctl.rules import rules_for
from ctl.runtime import RuntimePaths
from ctl.service_state import tailnet_ip

ROOT = Path(__file__).resolve().parents[1]


def manifest(**changes) -> dict:
    base = {
        "id": "demo",
        "name": "Demo",
        "tier": "optional",
        "group": "apps",
        "category": "home",
        "tagline": "t",
        "summary": "s",
        "version": "v1",
        "upstream": "o/r",
        "service": {"local_port": 9100, "extra_ports": [{"port": 9101, "purpose": "admin"}]},
        "route": {"https_port": 8490, "caddy_port": 19497, "access": "open"},
        "extra_routes": [
            {
                "name": "admin",
                "label": "Admin",
                "local_port": 9101,
                "https_port": 8491,
                "caddy_port": 19498,
                "access": "gate",
            }
        ],
        "sign_in": {"method": "none"},
    }
    base.update(changes)
    return base


class ManifestTests(unittest.TestCase):
    def test_a_valid_extra_route_loads(self):
        self.assertEqual(AppManifest.model_validate(manifest()).extra_routes[0].name, "admin")

    def test_an_extra_route_needs_its_port_declared_on_loopback(self):
        data = manifest(service={"local_port": 9100})
        with self.assertRaisesRegex(ValidationError, "service.extra_ports"):
            AppManifest.model_validate(data)

    def test_two_routes_cannot_share_a_tailnet_port(self):
        data = manifest()
        data["extra_routes"][0]["https_port"] = 8490
        with self.assertRaisesRegex(ValidationError, "own https_port"):
            AppManifest.model_validate(data)

    def test_extra_routes_need_a_main_route(self):
        with self.assertRaisesRegex(ValidationError, "main route"):
            AppManifest.model_validate(manifest(route=None))

    def test_an_open_extra_route_cannot_hand_out_a_login(self):
        data = manifest()
        data["extra_routes"][0]["access"] = "open"
        data["extra_routes"][0]["upstream_basic_auth"] = {"user_env": "U", "password_env": "P"}
        with self.assertRaisesRegex(ValidationError, "Authentik guards"):
            AppManifest.model_validate(data)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copytree(ROOT / "apps", self.root / "apps", ignore=shutil.ignore_patterns("node_modules", ".env"))
        shutil.copytree(ROOT / "platform", self.root / "platform")

    def edit(self, old: str, new: str) -> None:
        path = self.root / "apps/livekit/docker-compose.yml"
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_the_livekit_ports_are_declared(self):
        catalog.load(self.root / "apps")

    def test_an_undeclared_extra_port_is_refused(self):
        self.edit('"127.0.0.1:7880:7880"', '"127.0.0.1:7883:7880"')
        with self.assertRaisesRegex(catalog.CatalogError, "publishes port 7883"):
            catalog.load(self.root / "apps")

    def test_a_tailnet_port_without_a_required_address_is_refused(self):
        # An empty ${MU3LAB_TAILNET_IP} would publish the media port on every network.
        self.edit('"${MU3LAB_TAILNET_IP:?MU3LAB_TAILNET_IP is required}:7881:7881"', '"${MU3LAB_TAILNET_IP}:7881:7881"')
        with self.assertRaisesRegex(catalog.CatalogError, "127.0.0.1 only"):
            catalog.load(self.root / "apps")

    def test_a_tailnet_port_published_on_loopback_does_not_match_its_declaration(self):
        self.edit('"${MU3LAB_TAILNET_IP:?MU3LAB_TAILNET_IP is required}:7882:7882/udp"', '"127.0.0.1:7882:7882/udp"')
        with self.assertRaisesRegex(catalog.CatalogError, "udp on loopback"):
            catalog.load(self.root / "apps")


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.service = load_registry().get("livekit")

    def test_each_address_gets_its_own_caddy_listener(self):
        targets = self.service.route_targets
        self.assertEqual(
            [(t.name, t.proxy_port, t.upstream_port) for t in targets],
            [
                ("", 19493, 8093),
                ("signal", 19494, 7880),
                ("admin", 19495, 8094),
            ],
        )
        text = render("", [self.service])
        for target in targets:
            self.assertIn(f":{target.proxy_port} {{", text)

    def test_the_signal_route_is_not_gated_and_the_admin_route_signs_in_for_you(self):
        signal, admin = self.service.route_targets[1:]
        self.assertNotIn("forward_auth", _block(self.service, signal))
        block = _block(self.service, admin)
        self.assertIn("forward_auth", block)
        self.assertIn("{env.MU3LAB_UPSTREAM_AUTH_LIVEKIT_ADMIN}", block)
        self.assertEqual(upstream_auth_name("livekit", "admin"), "MU3LAB_UPSTREAM_AUTH_LIVEKIT_ADMIN")
        self.assertEqual(upstream_auth_name("kopia-ui"), "MU3LAB_UPSTREAM_AUTH_KOPIA_UI")

    def test_the_main_route_block_is_unchanged_by_default(self):
        self.assertEqual(_block(self.service), _block(self.service, self.service.route_targets[0]))

    def test_the_dashboard_lists_only_linked_extra_addresses(self):
        public = self.service.public()["extra_routes"]
        self.assertEqual([item["name"] for item in public], ["signal", "admin"])
        self.assertFalse(self.service.manifest.extra_routes[0].link)

    def test_each_gated_extra_address_is_its_own_authentik_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = RuntimePaths(Path(temp))
            (paths.projects / "livekit").mkdir(parents=True)
            (paths.projects / "livekit" / "docker-compose.yml").write_text("services: {}\n")
            blueprint = gate_blueprint(catalog.cached(), "box.tail1234.ts.net", paths)
        self.assertIn("slug: mu3lab-livekit-admin", blueprint)
        self.assertIn("box.tail1234.ts.net:8478", blueprint)
        self.assertNotIn("mu3lab-livekit-signal", blueprint)


class FactTests(unittest.TestCase):
    def test_route_url_and_tailnet_ip_are_template_facts(self):
        cat = catalog.cached()
        with tempfile.TemporaryDirectory() as temp:
            facts = Facts("box.tail1234.ts.net", RuntimePaths(Path(temp)), cat)
            with mock.patch("ctl.service_state.tailnet_ip", return_value="100.64.1.2"):
                lookup = lookup_for(cat.get("livekit"), facts, {})
                self.assertEqual(lookup("tailnet_ip"), "100.64.1.2")
            self.assertEqual(lookup("route_url:signal"), "https://box.tail1234.ts.net:8477")
            self.assertIsNone(lookup("route_url:missing"))

    def test_tailnet_ip_accepts_only_a_tailscale_address(self):
        def run(output: str, code: int = 0):
            return lambda *_args, **_kwargs: subprocess.CompletedProcess([], code, output, "")

        self.assertEqual(tailnet_ip(run("100.100.101.115\n")), "100.100.101.115")
        self.assertEqual(tailnet_ip(run("192.168.1.4\n")), "")
        self.assertEqual(tailnet_ip(run("", 1)), "")


class FakeLiteLLM:
    calls: ClassVar[list] = []

    def __init__(self, base_url: str, master: str) -> None:
        FakeLiteLLM.calls.append(("open", base_url, master))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def keys(self, user_id: str):
        FakeLiteLLM.calls.append(("keys", user_id))
        return [{"key_alias": user_id}]

    def delete(self, aliases):
        FakeLiteLLM.calls.append(("delete", aliases))

    def generate(self, **kwargs):
        FakeLiteLLM.calls.append(("generate", kwargs))
        return kwargs["key"]


class GatewayKeyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.paths = RuntimePaths(Path(temp.name))
        self.catalog = catalog.cached()
        self.app = self.catalog.get("livekit")
        self.project = self.paths.projects / "livekit"
        self.project.mkdir(parents=True)
        (self.paths.projects / "litellm").mkdir(parents=True)
        (self.paths.projects / "litellm" / ".env").write_text("LITELLM_MASTER_KEY=master\n")
        self.rule = next(rule for rule in rules_for(self.app.manifest) if rule.name == "gateway_key")
        FakeLiteLLM.calls = []

    def ctx(self) -> HookContext:
        return HookContext(
            self.app,
            self.project,
            Compose(self.project),
            lambda _line: None,
            lambda *_: None,
            None,
            Facts("box.tail1234.ts.net", self.paths, self.catalog),
        )

    def test_registers_the_apps_own_key_for_its_models_only(self):
        (self.project / ".env").write_text("MU3LAB_LLM_API_KEY=sk-abc123\n")
        with mock.patch("ctl.rules.gateway_key.LiteLLM", FakeLiteLLM):
            self.rule.after_healthy(self.ctx())
        self.assertEqual(FakeLiteLLM.calls[0], ("open", "http://127.0.0.1:4000", "master"))
        self.assertIn(("delete", ["mu3lab-app-livekit"]), FakeLiteLLM.calls)
        generated = FakeLiteLLM.calls[-1][1]
        self.assertEqual(generated["key"], "sk-abc123")
        self.assertEqual(generated["models"], ["mu3lab-local", "mu3lab-stt", "mu3lab-tts"])
        self.assertEqual(generated["alias"], "mu3lab-app-livekit")

    def test_refuses_a_key_litellm_would_reject(self):
        (self.project / ".env").write_text("MU3LAB_LLM_API_KEY=abc123\n")
        with mock.patch("ctl.rules.gateway_key.LiteLLM", FakeLiteLLM), self.assertRaises(StepFailed):
            self.rule.after_healthy(self.ctx())
        self.assertEqual(FakeLiteLLM.calls, [])


if __name__ == "__main__":
    unittest.main()
