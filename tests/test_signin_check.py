"""The install-time sign-in check catches each way an app's Authentik sign-in broke."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import httpx

from ctl.lifecycle import signin_check
from ctl.lifecycle.signin_check import SignInError, verify_gate, verify_oidc

HOST = "mu3lab.example.ts.net"
ORIGIN = f"https://{HOST}:8450"
AUTHENTIK = f"https://{HOST}"
DISCOVERY = f"{AUTHENTIK}/application/o/mu3lab-mealie/.well-known/openid-configuration"
TOKEN = f"{AUTHENTIK}/application/o/token/"


def authorize(client_id: str = "mu3lab-mealie", redirect: str = ORIGIN + "/login") -> str:
    query = urlencode({"client_id": client_id, "redirect_uri": redirect, "response_type": "code"})
    return f"{AUTHENTIK}/application/o/authorize/?{query}"


class FakeServer:
    """Authentik plus one app, answering like the real ones did."""

    def __init__(self):
        self.discovery = 200
        self.token_error = "invalid_grant"
        self.first_login = False
        self.entry_headers: dict[str, str] = {}
        self.entry_location = authorize()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == DISCOVERY:
            issuer = f"{AUTHENTIK}/application/o/mu3lab-mealie/"
            return httpx.Response(self.discovery, json={"issuer": issuer, "token_endpoint": TOKEN})
        if url == TOKEN:
            return httpx.Response(400, json={"error": self.token_error})
        if url == ORIGIN + "/api/app/about/startup-info":
            return httpx.Response(200, json={"isFirstLogin": self.first_login})
        if url == ORIGIN + "/api/auth/oauth":
            headers = {"location": self.entry_location, **self.entry_headers}
            return httpx.Response(302, headers=headers)
        return httpx.Response(404)


def check(server: FakeServer) -> str:
    def fresh_client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(server), follow_redirects=False)

    with patch("ctl.lifecycle.signin_check._client", side_effect=fresh_client):
        return verify_oidc(
            "mealie",
            host=HOST,
            port=8450,
            launch_path="/api/auth/oauth",
            client_id="mu3lab-mealie",
            client_secret="secret",
            redirect_paths=("/login",),
            log=lambda _line: None,
        )


class SignInCheckTests(unittest.TestCase):
    def test_a_working_app_passes(self):
        self.assertIn("verified", check(FakeServer()))

    def test_a_cookie_for_the_whole_tailnet_suffix_fails(self):
        # AdventureLog set "Domain=.ts.net"; browsers drop it and the return trip fails.
        server = FakeServer()
        server.entry_headers = {"set-cookie": "sessionid=abc; Domain=.ts.net; Path=/; Secure"}
        with self.assertRaisesRegex(SignInError, "ts.net"):
            check(server)

    def test_a_host_only_cookie_is_fine(self):
        server = FakeServer()
        server.entry_headers = {"set-cookie": f"sessionid=abc; Domain={HOST}; Path=/; Secure"}
        self.assertIn("verified", check(server))

    def test_a_wrong_client_secret_fails(self):
        server = FakeServer()
        server.token_error = "invalid_client"
        with self.assertRaisesRegex(SignInError, "client secret"):
            check(server)

    def test_an_unregistered_return_address_fails(self):
        server = FakeServer()
        server.entry_location = authorize(redirect=ORIGIN + "/somewhere-else")
        with self.assertRaisesRegex(SignInError, "return address"):
            check(server)

    def test_another_applications_client_fails(self):
        server = FakeServer()
        server.entry_location = authorize(client_id="mu3lab-immich")
        with self.assertRaisesRegex(SignInError, "different application"):
            check(server)

    def test_mealies_first_login_screen_fails(self):
        server = FakeServer()
        server.first_login = True
        with self.assertRaisesRegex(SignInError, "first-time"):
            check(server)

    def test_waits_for_authentik_to_publish_the_client(self):
        # Actual Budget started before Authentik applied its Blueprint and never retried.
        answers = iter([404, 404, 200])
        server = FakeServer()

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == DISCOVERY:
                server.discovery = next(answers)
            return server(request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        sleeps: list[float] = []
        with patch("ctl.lifecycle.signin_check._client", return_value=client):
            signin_check.wait_for_provider(HOST, "mealie", sleep=sleeps.append)
        self.assertEqual(len(sleeps), 2)

    def test_gives_up_when_authentik_never_publishes_the_client(self):
        server = FakeServer()
        server.discovery = 404
        client = httpx.Client(transport=httpx.MockTransport(server))
        with (
            patch("ctl.lifecycle.signin_check._client", return_value=client),
            self.assertRaisesRegex(SignInError, "never published"),
        ):
            signin_check.wait_for_provider(HOST, "mealie", timeout=0, sleep=lambda _s: None)

    def test_paperless_launch_page_must_not_hide_its_origin(self):
        # "no-referrer" makes browsers send "Origin: null", and Django answers 403.
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/__mu3lab/login":
                page = (
                    '<form id="login" method="post" action="/accounts/oidc/authentik/login/">'
                    '<input type="hidden" name="csrfmiddlewaretoken" value="t">'
                )
                return httpx.Response(200, text=page, headers={"referrer-policy": "no-referrer"})
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with self.assertRaisesRegex(SignInError, "security check"):
            signin_check._entry("paperless-ngx", client, ORIGIN, "/__mu3lab/login")


class GateCheckTests(unittest.TestCase):
    def test_a_guarded_app_sends_people_to_authentik_and_back(self):
        origin = f"https://{HOST}:8447"
        location = authorize(client_id="x", redirect=origin + "/outpost.goauthentik.io/callback?X=1")
        client = httpx.Client(
            transport=httpx.MockTransport(lambda _r: httpx.Response(302, headers={"location": location}))
        )
        with patch("ctl.lifecycle.signin_check._client", return_value=client):
            self.assertIn("verified", verify_gate(host=HOST, port=8447, log=lambda _l: None))

    def test_an_address_authentik_does_not_know_yet_fails_after_waiting(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(404)))
        with (
            patch("ctl.lifecycle.signin_check._client", return_value=client),
            self.assertRaisesRegex(SignInError, "not guarding"),
        ):
            verify_gate(host=HOST, port=8447, log=lambda _l: None, timeout=0, sleep=lambda _s: None)


if __name__ == "__main__":
    unittest.main()
