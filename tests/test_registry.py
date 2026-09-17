"""Registry invariants: only curated, internally safe Compose definitions load."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl.registry import RegistryError, load
from ctl.backups import Retention
from ctl.runtime import RuntimePaths
from ctl.service_state import public_url


class RegistryTests(unittest.TestCase):
    def test_checked_in_registry_loads_and_surfsense_is_explicitly_blocked(self):
        registry = load()
        self.assertEqual(registry.get("surfsense").availability, "blocked")
        self.assertIn("supported", registry.get("surfsense").blocked_reason)
        self.assertFalse(registry.get("vaultwarden").mcp.get("exposed", False))

    def test_rejects_compose_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "services.yaml"
            path.write_text("""schema_version: 1\nservices:\n  - id: bad\n    name: Bad\n    category: test\n    lifecycle: optional\n    compose_dir: ../outside\n    https_port: 1\n    health: {kind: tcp, port: 1}\n    auth: proxy\n    profiles: [cpu]\n""", encoding="utf-8")
            with self.assertRaisesRegex(RegistryError, "unsafe compose_dir"):
                load(path)

    def test_tailnet_url_never_falls_back_to_localhost(self):
        service = load().get("litellm")
        self.assertEqual(public_url(service, ""), "")
        self.assertEqual(public_url(service, "mu3lab.example.ts.net"),
                         "https://mu3lab.example.ts.net:4000")

    def test_runtime_paths_are_outside_checkout(self):
        paths = RuntimePaths()
        self.assertEqual(paths.root, Path("/srv/mu3lab"))
        self.assertEqual(paths.backups, Path("/srv/mu3lab/backups"))

    def test_backup_policy_matches_the_product_default(self):
        self.assertEqual(Retention().restic_args(),
                         ["--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "12"])

    def test_core_compose_files_are_valid_and_pin_identity_images(self):
        root = Path(__file__).resolve().parents[1]
        for rel in ("core/authentik/docker-compose.yml", "core/vaultwarden/docker-compose.yml"):
            with self.subTest(rel=rel):
                compose = yaml.safe_load((root / rel).read_text(encoding="utf-8"))
                self.assertIn("services", compose)
        authentik = (root / "core/authentik/.env.example").read_text(encoding="utf-8")
        self.assertNotIn("AUTHENTIK_TAG=latest", authentik)

    def test_ingress_matches_tailnet_host_headers_on_loopback(self):
        caddyfile = (Path(__file__).resolve().parents[1] / "core/ingress/Caddyfile").read_text(
            encoding="utf-8"
        )
        self.assertIn(":19460 {", caddyfile)
        self.assertIn("bind 127.0.0.1", caddyfile)
        self.assertNotIn("http://127.0.0.1:19460 {", caddyfile)
