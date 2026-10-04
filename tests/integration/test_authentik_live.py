"""Mu3Lab's Authentik client against the real, pinned Authentik image.

Runs only with MU3LAB_INTEGRATION=1 (CI's integration job, or by hand). It
starts a throwaway Authentik from tests/integration/authentik-compose.yml on
its own port and project name, and removes it with its data afterwards.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import httpx

from ctl import people
from ctl.authentik_blueprints import (
    DASHBOARD_BLUEPRINT,
    GatedApp,
    OidcApp,
    oidc_blueprint_name,
    removal_blueprint_name,
    render_gate_blueprint,
    render_oidc_blueprint,
    render_removal_blueprint,
)
from ctl.integrations.authentik import Authentik, password_hash
from ctl.secrets import runtime_env_text

COMPOSE = Path(__file__).with_name("authentik-compose.yml")
PORT = 19901
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "integration-only-token-" + "b" * 40
OWNER_PASSWORD = "Owner-pass-6420!"
HOST = "mu3lab.example.ts.net"


def _compose(*args: str, env_file: Path) -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "--env-file", str(env_file), *args],
        check=True,
        capture_output=True,
        timeout=600,
    )


def _sign_in(username: str, password: str) -> str:
    """Walk Authentik's default sign-in flow; return the last stage's component."""
    executor = "/api/v3/flows/executor/default-authentication-flow/?query="
    with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as client:
        client.get(executor)
        client.post(executor, json={"component": "ak-stage-identification", "uid_field": username})
        return str(
            client.post(executor, json={"component": "ak-stage-password", "password": password}).json()["component"]
        )


@unittest.skipUnless(os.environ.get("MU3LAB_INTEGRATION") == "1", "set MU3LAB_INTEGRATION=1 to run against Docker")
class AuthentikLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.env_file = Path(cls.tmp.name) / "authentik.env"
        values = {
            "TEST_AUTHENTIK_EMAIL": "owner@example.test",
            "TEST_AUTHENTIK_PASSWORD_HASH": password_hash(OWNER_PASSWORD),
            "TEST_AUTHENTIK_TOKEN": TOKEN,
            "TEST_AUTHENTIK_PORT": str(PORT),
        }
        cls.env_file.write_text(runtime_env_text(values), encoding="utf-8")
        cls.addClassCleanup(_compose, "down", "-v", env_file=cls.env_file)
        _compose("down", "-v", env_file=cls.env_file)
        _compose("up", "-d", env_file=cls.env_file)
        deadline = time.monotonic() + 300
        cls.authentik = Authentik(TOKEN, base_url=BASE)
        while not cls.authentik.ready():
            if time.monotonic() > deadline:
                raise RuntimeError("Authentik did not start")
            time.sleep(5)

        cls.authentik.wait_for_defaults()

    def test_1_bootstrap_hash_gives_the_owner_their_own_password(self) -> None:
        self.assertEqual(self.authentik.user("akadmin")["email"], "owner@example.test")
        self.assertEqual(_sign_in("akadmin", OWNER_PASSWORD), "xak-flow-redirect")
        self.assertNotEqual(_sign_in("akadmin", "wrong password"), "xak-flow-redirect")

    def test_2_gate_owner_guard_and_removal(self) -> None:
        gated = (
            GatedApp("litellm", "LiteLLM", 8454, "operators"),
            GatedApp("baby-buddy", "Baby Buddy", 8458, "household"),
        )
        self.authentik.apply_blueprint(DASHBOARD_BLUEPRINT, render_gate_blueprint(HOST, 8446, gated))
        app = OidcApp("actual-budget", "Actual Budget", 8448, "mu3lab-actual-budget", "s3cret", ("/openid/callback",))
        guarded = OidcApp(**{**app.__dict__, "initial_owner": "akadmin"})
        self.authentik.apply_blueprint(oidc_blueprint_name(app.id), render_oidc_blueprint(HOST, guarded))
        self.assertEqual(self._binding_kinds(app.id), [(10, "policy")])
        self.authentik.apply_blueprint(oidc_blueprint_name(app.id), render_oidc_blueprint(HOST, app))
        self.assertEqual(self._binding_kinds(app.id), [(0, "group"), (1, "group"), (2, "group")])
        discovery = httpx.get(f"{BASE}/application/o/mu3lab-actual-budget/.well-known/openid-configuration")
        self.assertEqual(discovery.status_code, 200)
        self.authentik.apply_blueprint(
            removal_blueprint_name(app.id), render_removal_blueprint(app.id, app.name, oidc=True)
        )
        slugs = {item["slug"] for item in self.authentik.request("GET", "/core/applications/")["results"]}
        self.assertEqual(slugs, {"mu3lab", "mu3lab-litellm", "mu3lab-baby-buddy"})

    def test_3_household_member_invite_link(self) -> None:
        self.authentik.apply_blueprint(DASHBOARD_BLUEPRINT, render_gate_blueprint(HOST, 8446))
        with patch("ctl.people.Authentik.runtime", return_value=self.authentik):
            added = people.add_person("Member One", "member1@example.test", "member", f"https://{HOST}")
            self.assertEqual(added["person"]["role"], "member")
            self.assertTrue(added["invite"]["url"].startswith(f"https://{HOST}/"))
            self.assertEqual({person["username"] for person in people.list_people()}, {"akadmin", "member1"})
            with self.assertRaisesRegex(people.PeopleError, "at least one administrator"):
                people.change("akadmin", "deactivate", f"https://{HOST}")
            self.assertEqual(people.change("member1", "role", f"https://{HOST}", "admin")["person"]["role"], "admin")
            self.assertEqual(people.change("member1", "role", f"https://{HOST}", "member")["person"]["role"], "member")
            self.assertFalse(people.change("member1", "deactivate", f"https://{HOST}")["person"]["active"])
            self.assertTrue(people.change("member1", "reactivate", f"https://{HOST}")["person"]["active"])
        link = urllib.parse.urlsplit(added["invite"]["url"])
        executor = f"/api/v3/flows/executor/mu3lab-welcome/?query={urllib.parse.quote(link.query)}"
        with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as client:
            client.get(f"{link.path}?{link.query}")
            self.assertEqual(client.get(executor).json()["component"], "ak-stage-prompt")
            answer = {
                "component": "ak-stage-prompt",
                "password": "Member-pass-9183!",
                "password_repeat": "Member-pass-9183!",
            }
            self.assertEqual(client.post(executor, json=answer).json()["component"], "xak-flow-redirect")
        self.assertEqual(_sign_in("member1", "Member-pass-9183!"), "xak-flow-redirect")

    def _binding_kinds(self, app_id: str) -> list[tuple[int, str]]:
        apps = self.authentik.request("GET", "/core/applications/", params={"slug": f"mu3lab-{app_id}"})["results"]
        bindings = self.authentik.request("GET", "/policies/bindings/", params={"target": apps[0]["pk"]})["results"]
        return sorted((item["order"], "policy" if item.get("policy") else "group") for item in bindings)


if __name__ == "__main__":
    unittest.main()
