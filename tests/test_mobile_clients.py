"""Phone clients are reviewed metadata: safe links, known kinds, one primary per app."""

from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path

import yaml
from pydantic import ValidationError

from ctl.manifest.models import Mobile
from ctl.registry import load

ROOT = Path(__file__).resolve().parents[1]


class MobileClientTests(unittest.TestCase):
    def test_every_end_user_app_offers_phone_guidance(self):
        mobile = {service.id: service.mobile for service in load().services if service.mobile}
        self.assertEqual(
            set(mobile),
            {
                "vaultwarden",
                "surfsense",
                "lobehub",
                "actual-budget",
                "immich",
                "mealie",
                "adventurelog",
                "paperless-ngx",
                "nextcloud",
                "baby-buddy",
            },
        )
        self.assertEqual(mobile["lobehub"]["primary"], "lobechat-pwa")
        self.assertEqual(mobile["paperless-ngx"]["primary"], "papernext")

    def test_unsafe_or_ambiguous_metadata_is_rejected(self):
        original = yaml.safe_load((ROOT / "apps" / "vaultwarden" / "app.yaml").read_text(encoding="utf-8"))["mobile"]
        mutations = {
            "kind": lambda mobile: mobile["clients"][0].__setitem__("kind", "wrapper"),
            "platforms": lambda mobile: mobile["clients"][0].__setitem__("platforms", ["windows-phone"]),
            "HTTPS": lambda mobile: mobile["clients"][0]["install"].__setitem__("ios", "http://unsafe.example/app"),
            "unique": lambda mobile: mobile["clients"].append(deepcopy(mobile["clients"][0])),
            "primary": lambda mobile: mobile.__setitem__("primary", "missing-client"),
        }
        Mobile.model_validate(original)
        for expected, mutate in mutations.items():
            with self.subTest(expected=expected):
                raw = deepcopy(original)
                mutate(raw)
                with self.assertRaisesRegex(ValidationError, expected):
                    Mobile.model_validate(raw)

    def test_the_browser_view_carries_no_secrets(self):
        def keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield str(key).lower()
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)

        exposed = {key for service in load().services for key in keys(service.public().get("mobile", {}))}
        self.assertTrue({"password", "secret", "api_key", "token", "compose_dir"}.isdisjoint(exposed))


class BabyBuddyApiRouteTests(unittest.TestCase):
    def test_token_requests_skip_the_sign_in_gate_and_cannot_claim_an_identity(self):
        from ctl.routes import _block

        block = _block(load().get("baby-buddy"))
        bypass = block.split("handle @api_token {", 1)[1].split("reverse_proxy /outpost", 1)[0]
        self.assertIn('header Authorization "Token *"', block)
        self.assertIn("path /api/*", block)
        self.assertIn("header_up -Remote-User", bypass)
        self.assertNotIn("header_up Remote-User", bypass)
        # Everything else still goes through Authentik first.
        self.assertLess(block.index("handle @api_token"), block.index("forward_auth"))


if __name__ == "__main__":
    unittest.main()
