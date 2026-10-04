"""Faster provider onboarding: Bitwarden pre-installed in the browser, key
detection for one paste field, and OpenRouter's key-by-sign-in (OAuth PKCE)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ctl import browser_extension, install
from ctl.api import create_app
from ctl.provider_catalog import catalog, detect, key_problem

SERVER = "https://mu3lab.example.ts.net:8443"
OPERATOR_WITH_CSRF = {
    "x-mu3lab-proxy-token": "real-token",
    "x-authentik-username": "owner",
    "x-authentik-uid": "subject",
    "x-authentik-groups": "mu3lab-operators",
    "host": "testserver",
    "origin": "https://testserver",
    "x-mu3lab-csrf": "bound",
}


def _install_browser(root: Path, marker: str) -> None:
    path = root / marker.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


class BrowserPolicyTests(unittest.TestCase):
    def test_policy_only_adds_bitwarden_and_its_server(self):
        policy = browser_extension.policy(SERVER)
        self.assertEqual(set(policy), {"ExtensionSettings", "3rdparty"})
        self.assertEqual(set(policy["ExtensionSettings"]), {browser_extension.BITWARDEN_ID})
        settings = policy["ExtensionSettings"][browser_extension.BITWARDEN_ID]
        # The owner can still turn it off or unpin it.
        self.assertEqual(settings["installation_mode"], "normal_installed")
        self.assertEqual(settings["toolbar_pin"], "default_pinned")
        self.assertEqual(
            policy["3rdparty"]["extensions"][browser_extension.BITWARDEN_ID], {"environment": {"base": SERVER}}
        )

    def test_plan_writes_only_for_installed_browsers_and_detects_up_to_date_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(browser_extension.plan(SERVER, root), {"ready": [], "to_write": [], "conflict": []})
            _install_browser(root, "/opt/google/chrome/chrome")
            found = browser_extension.plan(SERVER, root)
            self.assertEqual([b.name for b in found["to_write"]], ["Google Chrome"])
            chrome = found["to_write"][0]
            path = root / chrome.policy_path.lstrip("/")
            path.parent.mkdir(parents=True)
            path.write_text(browser_extension.render(SERVER))
            self.assertEqual(browser_extension.plan(SERVER, root)["ready"], [chrome])
            # A new tailnet address makes the file outdated.
            self.assertEqual(browser_extension.plan("https://new.example.ts.net:8443", root)["to_write"], [chrome])
            self.assertEqual(
                browser_extension.status(root),
                {"browsers": ["Google Chrome"], "server_url": SERVER, "signed_in": False},
            )

    def test_an_extension_sign_in_is_read_from_the_vaults_device_list(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "db.sqlite3"
            self.assertFalse(browser_extension.signed_in(database))
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE devices (uuid TEXT, atype INTEGER)")
            # Mu3Lab's own sign-in while saving app logins is not a browser.
            connection.execute("INSERT INTO devices VALUES ('mu3lab', 25)")
            connection.commit()
            self.assertFalse(browser_extension.signed_in(database))
            connection.execute("INSERT INTO devices VALUES ('chrome', 2)")
            connection.commit()
            connection.close()
            self.assertTrue(browser_extension.signed_in(database))

    def test_existing_extension_rules_from_someone_else_are_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _install_browser(root, "/opt/google/chrome/chrome")
            other = root / "etc/opt/chrome/policies/managed/company.json"
            other.parent.mkdir(parents=True)
            other.write_text(json.dumps({"ExtensionSettings": {"*": {"installation_mode": "blocked"}}}))
            found = browser_extension.plan(SERVER, root)
            self.assertEqual([b.name for b in found["conflict"]], ["Google Chrome"])
            self.assertEqual(found["to_write"], [])

    def test_install_step_is_skipped_without_a_browser_and_never_blocks(self):
        empty = {"ready": [], "to_write": [], "conflict": []}
        with (
            patch("ctl.install._tailscale_dns_name_for_install", return_value="mu3lab.example.ts.net"),
            patch("ctl.browser_extension.plan", return_value=empty),
        ):
            check = install._browser_extension_check({})
        self.assertEqual(check["state"], "not_needed")
        self.assertEqual(install.fix_for_state("browser_extension", "not_needed"), "skip")
        with patch("ctl.install._tailscale_dns_name_for_install", return_value=""):
            self.assertEqual(install._browser_extension_check({})["state"], "no_address")
        self.assertEqual(install.fix_for_state("browser_extension", "no_address"), "skip")

    def test_install_step_writes_policy_through_the_privilege_boundary(self):
        chrome = browser_extension.BROWSERS[0]
        with (
            patch("ctl.install._tailscale_dns_name_for_install", return_value="mu3lab.example.ts.net"),
            patch("ctl.browser_extension.plan", return_value={"ready": [], "to_write": [chrome], "conflict": []}),
            patch("ctl.install.actions.privilege.run_privileged", return_value={"ok": True}) as mkdir,
            patch("ctl.install.actions.write_root_file", return_value={"ok": True}) as write,
        ):
            result = install.fix_browser_extension({}, {"log_fn": lambda _step: lambda _line: None})
        self.assertTrue(result["ok"])
        self.assertEqual(mkdir.call_args.args[0], ["mkdir", "-p", chrome.policy_dir])
        path, content = write.call_args.args[:2]
        self.assertEqual(path, chrome.policy_path)
        self.assertEqual(json.loads(content), browser_extension.policy(SERVER))

    def test_uninstall_removes_every_policy_file_mu3lab_writes(self):
        text = (Path(__file__).resolve().parents[1] / "uninstall.sh").read_text(encoding="utf-8")
        self.assertIn(browser_extension.POLICY_FILE, text)
        for browser in browser_extension.BROWSERS:
            self.assertIn(browser.policy_dir.removesuffix("/policies/managed"), text)


class KeyDetectionTests(unittest.TestCase):
    def test_detects_provider_from_prefix_or_shape(self):
        cases = {
            "csk-abc": "cerebras",
            "AIzaSyExample": "google",
            "AQ.Ab8RN6Example": "google",
            "gsk_abc": "groq",
            "hf_abc": "huggingface",
            "nvapi-abc": "nvidia",
            "sk-or-v1-abc": "openrouter",
            "A1b2" * 8: "mistral",
            "mstrl" + "a" * 40: "mistral",
            "0123456789abcdef" * 2 + ".ABCDEFGHijklmnop": "zhipu",
        }
        for key, provider_id in cases.items():
            with self.subTest(key=key):
                self.assertEqual(getattr(detect(f"  {key} "), "id", None), provider_id)
        self.assertIsNone(detect("not-a-known-key"))

    def test_only_empty_or_oversized_pastes_are_refused(self):
        for text in ("   ", "x" * 5000):
            with self.subTest(text=text[:20]):
                self.assertTrue(key_problem(text))
        # Anything else is treated as a key; the provider's live check decides.
        for text in ("  gsk_" + "a" * 52 + "  ", "CEREBRAS_API_KEY=csk-abc", "gsk_abc\nsecond line"):
            with self.subTest(text=text[:20]):
                self.assertEqual(key_problem(text), "")

    def test_payment_required_and_recommended_flags_are_exposed_to_the_dashboard(self):
        by_id = {item["id"]: item for item in catalog()}
        # Cerebras grants no free inference until a card is on file, so it is
        # flagged and never recommended; OpenRouter's free models need no card.
        self.assertEqual({pid for pid, item in by_id.items() if item["payment_required"]}, {"cerebras"})
        self.assertEqual({pid for pid, item in by_id.items() if item["recommended"]}, {"google", "groq", "nvidia"})
        self.assertNotIn("oauth", by_id["openrouter"])


class ProviderRouteTests(unittest.TestCase):
    HEADERS = OPERATOR_WITH_CSRF

    def setUp(self):
        for target, value in (
            ("ctl.api.security.ingress_token", "real-token"),
            ("ctl.api.security.csrf_token", "bound"),
            ("ctl.api.routes.providers.ControlState.runtime", None),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        store = patch("ctl.api.routes.providers.runtime.job_store").start()
        self.addCleanup(patch.stopall)
        store.return_value.by_idempotency_key.return_value = None
        store.return_value.create.return_value = {"id": "job-1"}
        self.saved = patch("ctl.store.providers.save", side_effect=lambda pid, label, _key: {"id": pid, "label": label})
        self.save = self.saved.start()
        self.client = TestClient(create_app())

    def test_a_key_without_a_provider_is_detected(self):
        response = self.client.post("/api/v1/providers", headers=self.HEADERS, json={"api_key": "gsk_abc"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.save.call_args.args, ("groq", "Groq", "gsk_abc"))

    def test_an_unrecognised_key_asks_for_the_provider(self):
        response = self.client.post("/api/v1/providers", headers=self.HEADERS, json={"api_key": "mystery"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose the provider", response.json()["error"])
        self.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
