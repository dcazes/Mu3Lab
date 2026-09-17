from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import bootstrap_state, install
from ctl.runtime import RuntimePaths


class BootstrapIdentityTests(unittest.TestCase):
    def test_acknowledgements_store_only_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            bootstrap_state.confirm("vaultwarden_account", paths)
            value = bootstrap_state.read(paths)
            self.assertIn("vaultwarden_account", value)
            self.assertNotIn("password", str(value).lower())

    def test_manual_prompts_are_copyable_and_explicit(self):
        ctx = {"inputs": {}, "root": Path("/tmp"), "log_fn": lambda _: lambda _: None}
        with patch("ctl.install._runtime_marker", return_value=False):
            prompt = install.fix_vaultwarden_setup({}, ctx)["prompt"]
        self.assertEqual(prompt["kind"], "manual_setup")
        self.assertEqual(prompt["copy_url"], prompt["url"])
        self.assertIn("password", prompt["body"].lower())
        self.assertIn("check", prompt["check_label"].lower())

    def test_identity_steps_are_before_final_dashboard_route(self):
        ids = [step["id"] for step in install.STEPS]
        self.assertLess(ids.index("vaultwarden_setup"), ids.index("tailscale_join"))
        self.assertLess(ids.index("tailscale_join"), ids.index("authentik_setup"))
        self.assertLess(ids.index("authentik_setup"), ids.index("dashboard_protection"))
        self.assertLess(ids.index("serve"), ids.index("dashboard_protection"))
