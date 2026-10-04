"""Prove an app's Authentik sign-in works before an install is called done.

An app that answers its health check can still be unusable: its client may be
missing from Authentik, its secret may not match, its login cookie may be
refused by browsers, or a launch page may trip its own CSRF check. Each of
those only showed up when a person clicked Open. This module walks the same
path a browser takes, over the app's real private HTTPS address, up to the
point where Authentik would ask who you are, and checks every hand-off:

* Authentik publishes the app's client (``wait_for_provider``).
* The app's sign-in entry sends the browser to Authentik with the right client
  and a registered return address, and sets only cookies a browser will keep.
* Authentik accepts the app's client secret.
* App-specific first-run state is gone (Actual uses OpenID, Mealie has no
  "first login" screen).

Nothing here signs anyone in or touches a person's session.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from ctl.identity import launch_path
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

Log = Callable[[str], None]
AUTHORIZE_PATH = "/application/o/authorize/"
TIMEOUT = httpx.Timeout(20.0)


class SignInError(RuntimeError):
    """A plain-language reason sign-in would fail for a person."""


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, follow_redirects=False)


def discovery_url(host: str, service_id: str) -> str:
    return f"https://{host}/application/o/mu3lab-{service_id}/.well-known/openid-configuration"


def wait_for_provider(host: str, service_id: str, timeout: float = 180, sleep=time.sleep) -> dict:
    """Wait until Authentik has applied the app's Blueprint and serves its client.

    Authentik's worker applies Blueprints asynchronously. An app started before
    then gets a 404 from discovery and some (Actual Budget) never retry.
    """
    url = discovery_url(host, service_id)
    deadline = time.monotonic() + timeout
    last = ""
    with _client() as client:
        while True:
            try:
                response = client.get(url)
                if response.status_code == 200:
                    document = response.json()
                    if str(document.get("issuer", "")).rstrip("/").endswith(f"/application/o/mu3lab-{service_id}"):
                        return document
                    last = "Authentik answered for a different application"
                else:
                    last = f"Authentik answered HTTP {response.status_code}"
            except (httpx.HTTPError, ValueError) as exc:
                last = f"Authentik could not be reached ({type(exc).__name__})"
            if time.monotonic() >= deadline:
                raise SignInError(f"Authentik never published this app's sign-in: {last}.")
            sleep(3)


def _check_cookies(response: httpx.Response, host: str) -> None:
    """A cookie scoped wider than this host is dropped by browsers (e.g. ``.ts.net``)."""
    for header in response.headers.get_list("set-cookie"):
        try:
            jar = SimpleCookie()
            jar.load(header)
        except CookieError:
            continue
        for morsel in jar.values():
            domain = morsel["domain"].lstrip(".").lower()
            if domain and domain != host.lower():
                raise SignInError(
                    f"The app sets its login cookie for “{domain}” instead of this server, so browsers discard it "
                    "and the sign-in fails on the way back from Authentik."
                )


def _follow(client: httpx.Client, response: httpx.Response, host: str, authentik: str) -> str:
    """Follow the app's own redirects until one leaves for Authentik; return that URL."""
    for _hop in range(8):
        _check_cookies(response, host)
        if response.status_code not in (301, 302, 303, 307, 308):
            raise SignInError(
                f"Opening the app should hand over to Authentik, but the app answered HTTP {response.status_code}."
            )
        location = urljoin(str(response.url), response.headers.get("location", ""))
        if location.startswith(authentik + "/"):
            return location
        response = client.get(location)
    raise SignInError("The app kept redirecting to itself instead of handing over to Authentik.")


def _authorize_target(url: str) -> dict[str, str]:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if not parts.path.startswith(AUTHORIZE_PATH):
        # Signed-out browsers are bounced to the login flow with ?next=authorize.
        nxt = (query.get("next") or [""])[0]
        if not nxt.startswith(AUTHORIZE_PATH):
            raise SignInError("The app handed over to an unexpected Authentik page.")
        query = parse_qs(urlsplit(nxt).query)
    return {key: values[0] for key, values in query.items() if values}


# Apps whose login page starts sign-in with a JSON POST that returns
# Authentik's address: (path, body, where the address is in the reply, name).
_JSON_LAUNCH: dict[str, tuple[str, dict[str, object], tuple[str, ...], str]] = {
    "actual-budget": (
        "/account/login",
        {"loginMethod": "openid", "returnUrl": "{origin}"},
        ("data", "returnUrl"),
        "Actual Budget",
    ),
    "immich": ("/api/oauth/authorize", {"redirectUri": "{origin}/auth/login"}, ("url",), "Immich"),
    "lobehub": (
        "/api/auth/sign-in/oauth2",
        {"providerId": "authentik", "callbackURL": "{origin}/", "disableRedirect": True},
        ("url",),
        "LobeChat",
    ),
}


def _json_launch(service_id: str, client: httpx.Client, origin: str) -> httpx.Response:
    path, template, keys, name = _JSON_LAUNCH[service_id]
    body = {key: value.format(origin=origin) if isinstance(value, str) else value for key, value in template.items()}
    response = client.post(origin + path, json=body)
    _check_cookies(response, urlsplit(origin).hostname or "")
    target: object = response.json() if response.content else {}
    for key in keys:
        target = target.get(key, "") if isinstance(target, dict) else ""
    if response.status_code not in (200, 201) or not isinstance(target, str) or not target:
        raise SignInError(f"{name} did not start an Authentik sign-in.")
    return httpx.Response(302, headers={"location": target}, request=response.request)


def _entry(service_id: str, client: httpx.Client, origin: str, launch_path: str) -> httpx.Response:
    """Start sign-in the way the app's own login page does."""
    if service_id in _JSON_LAUNCH:
        return _json_launch(service_id, client, origin)
    if service_id == "paperless-ngx":
        page = client.get(origin + launch_path)
        _check_cookies(page, urlsplit(origin).hostname or "")
        if page.headers.get("referrer-policy", "").lower() == "no-referrer":
            # Browsers then send "Origin: null" with the form, which CSRF rejects.
            raise SignInError("Paperless's sign-in page would be rejected by its own security check.")
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text)
        action = re.search(r'<form id="login" method="post" action="([^"]+)"', page.text)
        if page.status_code != 200 or not token or not action:
            raise SignInError("Paperless did not serve its Authentik sign-in page.")
        return client.post(
            origin + action.group(1),
            data={"csrfmiddlewaretoken": token.group(1)},
            headers={"Origin": origin, "Referer": origin + launch_path},
        )
    return client.get(origin + launch_path)


def _check_secret(client: httpx.Client, token_endpoint: str, client_id: str, secret: str, redirect: str) -> None:
    """A made-up code must fail as a bad code, not as a bad client."""
    response = client.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": "mu3lab-sign-in-check",
            "redirect_uri": redirect,
            "client_id": client_id,
            "client_secret": secret,
        },
    )
    try:
        error = str(response.json().get("error", ""))
    except ValueError:
        error = ""
    if error == "invalid_client" or response.status_code == 401:
        raise SignInError("Authentik rejected the app's client secret, so every sign-in would fail.")
    if error != "invalid_grant":
        raise SignInError(f"Authentik's token endpoint answered unexpectedly (HTTP {response.status_code}).")


def _check_first_run(service_id: str, client: httpx.Client, origin: str) -> None:
    if service_id == "actual-budget":
        data = client.get(origin + "/account/needs-bootstrap").json().get("data", {})
        if not data.get("bootstrapped") or data.get("loginMethod") != "openid":
            raise SignInError("Actual Budget is waiting for a first-time password instead of using Authentik.")
    elif service_id == "mealie":
        if client.get(origin + "/api/app/about/startup-info").json().get("isFirstLogin"):
            raise SignInError("Mealie still shows its first-time login screen.")


def verify_oidc(
    service_id: str,
    *,
    host: str,
    port: int,
    launch_path: str,
    client_id: str,
    client_secret: str,
    redirect_paths: tuple[str, ...],
    log: Log,
) -> str:
    """Raise ``SignInError`` unless a browser could sign in to this app through Authentik."""
    origin = f"https://{host}:{port}"
    authentik = f"https://{host}"
    document = wait_for_provider(host, service_id)
    log("Authentik publishes this app's sign-in.")
    with _client() as client:
        try:
            _check_first_run(service_id, client, origin)
            target = _authorize_target(
                _follow(client, _entry(service_id, client, origin, launch_path), host, authentik)
            )
            if target.get("client_id") != client_id:
                raise SignInError("The app asks Authentik for a different application than the one Mu3Lab registered.")
            allowed = {origin + path for path in redirect_paths}
            if target.get("redirect_uri") not in allowed:
                raise SignInError("The app's return address is not one Authentik will send people back to.")
            log("The app hands over to Authentik with the registered client and return address.")
            _check_secret(client, str(document["token_endpoint"]), client_id, client_secret, target["redirect_uri"])
            log("Authentik accepts the app's client secret.")
        except httpx.HTTPError as exc:
            raise SignInError(
                f"The app's sign-in could not be checked over its private address ({type(exc).__name__})."
            ) from None
        except ValueError:
            raise SignInError("The app answered its sign-in check with something unexpected.") from None
    return "Sign-in through Authentik verified."


def verify_gate(*, host: str, port: int, log: Log, timeout: float = 360, sleep=time.sleep) -> str:
    """For Authentik-gated apps: a signed-out visit is sent to Authentik and comes back here.

    Authentik's outpost picks up new apps on its own schedule and answers 404
    for an address it does not know yet, so this waits for it.
    """
    origin = f"https://{host}:{port}"
    deadline = time.monotonic() + timeout
    last = ""
    with _client() as client:
        while True:
            try:
                response = client.get(origin + "/")
                location = response.headers.get("location", "")
                if response.status_code in (302, 303, 307) and location.startswith(f"https://{host}/"):
                    target = _authorize_target(location)
                    if not str(target.get("redirect_uri", "")).startswith(origin + "/outpost.goauthentik.io/"):
                        raise SignInError("Authentik would not send people back to this app after signing in.")
                    log("Authentik guards this app and returns people to it after signing in.")
                    return "Authentik sign-in verified."
                last = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            if time.monotonic() >= deadline:
                raise SignInError(f"Authentik is not guarding this app's address yet ({last}).")
            sleep(5)


def verify_sign_in(service_id: str, host: str, log: Log) -> str:
    """Check whichever kind of Authentik sign-in this installed app uses."""
    service = load().get(service_id)
    port = service.private_https_port
    if not host or not port:
        raise SignInError("This server's private address is not known yet.")
    if service.manifest.sign_in.method in {"gate", "trusted_header"}:
        # The outpost can keep serving an app Authentik has already deleted;
        # ask Authentik itself first.
        wait_for_provider(host, service_id)
        return verify_gate(host=host, port=port, log=log)
    contract = service.manifest.sign_in.oidc
    if contract is None:
        return "This app does not use Authentik sign-in."
    values = read_runtime_env(RuntimePaths().projects / service_id / ".env")
    return verify_oidc(
        service_id,
        host=host,
        port=port,
        launch_path=launch_path(service.manifest),
        client_id=values.get(contract.env.client_id, ""),
        client_secret=values.get(contract.env.client_secret, ""),
        redirect_paths=contract.redirect_paths,
        log=log,
    )
