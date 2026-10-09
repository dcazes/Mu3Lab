"""App containers reach this server's private address through the tailnet relay."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ctl import install, tailnet_relay
from ctl.app_settings import fixed_names
from ctl.engine.project import TAILNET_HOST, Facts, build_env
from ctl.manifest.catalog import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
HOST = "mu3lab.example.ts.net"
MAPPING = "${MU3LAB_TAILNET_HOST:-tailnet.invalid}:host-gateway"


class RelayConfigTests(unittest.TestCase):
    def test_the_relay_passes_port_443_through_from_dockers_host_address_only(self):
        text = tailnet_relay.render(HOST, "10.210.0.1")
        self.assertIn("listen 10.210.0.1:443;", text)
        self.assertIn(f"proxy_pass {HOST}:443;", text)
        self.assertIn("deny all;", text)
        self.assertNotIn("ssl", text)  # pass-through: the relay never holds a certificate

    def test_unsafe_values_are_refused(self):
        with self.assertRaises(ValueError):
            tailnet_relay.render("evil; include /etc/passwd", "10.210.0.1")
        with self.assertRaises(ValueError):
            tailnet_relay.render(HOST, "0.0.0.0; listen 443")

    def test_dockers_host_address_is_read_from_the_default_bridge(self):
        with patch.object(
            tailnet_relay.actions,
            "docker_cmd",
            return_value=(0, '[{"Subnet":"10.210.0.0/24","Gateway":"10.210.0.1"}]\n'),
        ):
            self.assertEqual(tailnet_relay.host_address(lambda _line: None), "10.210.0.1")
        with patch.object(tailnet_relay.actions, "docker_cmd", return_value=(1, "")):
            self.assertEqual(tailnet_relay.host_address(lambda _line: None), "")

    def test_ready_only_with_this_servers_configuration_and_a_listener(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            with (
                patch.object(tailnet_relay, "host_address", return_value="10.210.0.1"),
                patch.object(tailnet_relay, "_listening", return_value=True),
            ):
                self.assertEqual(tailnet_relay.status(HOST, print, paths)["state"], "missing")
                target = tailnet_relay.config_path(paths)
                target.parent.mkdir(parents=True)
                target.write_text(tailnet_relay.render("other.example.ts.net", "10.210.0.1"))
                self.assertEqual(tailnet_relay.status(HOST, print, paths)["state"], "missing")
                target.write_text(tailnet_relay.render(HOST, "10.210.0.1"))
                self.assertEqual(tailnet_relay.status(HOST, print, paths)["state"], "ready")
                self.assertEqual(tailnet_relay.status("", print, paths)["state"], "missing")

    def test_apply_writes_the_configuration_and_starts_the_relay(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            with (
                patch.object(tailnet_relay, "host_address", return_value="10.210.0.1"),
                patch.object(tailnet_relay, "_listening", return_value=True),
                patch.object(tailnet_relay.actions, "compose_up", return_value=(0, "")) as up,
            ):
                self.assertEqual(tailnet_relay.apply(HOST, print, paths), {"ok": True})
            self.assertEqual(tailnet_relay.config_path(paths).read_text(), tailnet_relay.render(HOST, "10.210.0.1"))
            self.assertEqual(up.call_args.args[0], tailnet_relay.SOURCE)
            self.assertEqual(up.call_args.kwargs["env"], {"MU3LAB_RELAY_CONFIG": str(tailnet_relay.config_path(paths))})

    def test_the_installer_starts_it_once_authentik_has_its_private_address(self):
        order = [step["id"] for step in install.STEPS]
        self.assertLess(order.index("authentik_serve"), order.index("tailnet_relay"))
        self.assertEqual(install.fix_for_state("tailnet_relay", "missing"), "start_relay")
        self.assertEqual(install.PHASE_OF["tailnet_relay"], "Set up sign-in")


class AppMappingTests(unittest.TestCase):
    def test_every_app_that_signs_in_through_authentik_maps_the_private_name_to_the_relay(self):
        for app in load().apps:
            oidc = app.manifest.sign_in.oidc
            if app.manifest.sign_in.method != "oidc" or oidc is None:
                continue
            with self.subTest(app=app.id):
                compose = yaml.safe_load((app.folder / "docker-compose.yml").read_text(encoding="utf-8"))
                names = {value for value in oidc.env.model_dump().values() if value}
                mapped = []
                for service_id, service in compose["services"].items():
                    has_mapping = MAPPING in (service.get("extra_hosts") or [])
                    if has_mapping:
                        mapped.append(service_id)
                    if any("${" + name in yaml.safe_dump(service) for name in names):
                        self.assertTrue(has_mapping, f"{service_id} uses the sign-in settings but cannot reach them")
                self.assertTrue(mapped, "no service can reach Authentik")

    def test_every_app_is_told_this_servers_private_name_and_it_is_never_stored(self):
        app = load().get("open-webui")
        with tempfile.TemporaryDirectory() as tmp:
            facts = Facts(HOST, RuntimePaths(Path(tmp)), load())
            self.assertEqual(build_env(app, facts, {}, [])[TAILNET_HOST], HOST)
        self.assertIn(TAILNET_HOST, fixed_names(app.manifest))


if __name__ == "__main__":
    unittest.main()
