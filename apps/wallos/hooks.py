"""Wallos signs people in with Authentik; Mu3Lab makes the owner its administrator first.

Wallos's administrator is its first account (user 1), and only its registration
page creates that account. On a new server Mu3Lab registers it through that page,
on the app's loopback port, under the owner's Authentik username and email with
a password nobody uses (password sign-in is off). The owner's first Authentik
sign-in then links to it by email, which Authentik vouches for. Everyone else
gets their own private account the first time they sign in; the private address
redirects the registration page away, so nobody registers by hand.

Wallos gives every account made at sign-in euros as its main currency.
``periodic`` switches each new account to the household's currency once,
while it still has no subscriptions (``scripts/household-currency.php``).
Checked against Wallos v5.11.2: registration.php, includes/oidc/.
"""

from __future__ import annotations

import secrets
import urllib.error
import urllib.parse
import urllib.request

from ctl.control_state import ControlState
from ctl.engine.hooks import AppHooks, HookContext

# The currencies Wallos v5.11.2 gives every account (registration.php); others fall back to US dollars.
CURRENCIES = frozenset(
    {
        *("EUR", "USD", "JPY", "BGN", "CZK", "DKK", "GBP", "HUF", "PLN", "RON", "SEK", "CHF", "ISK", "NOK"),
        *("RUB", "TRY", "AUD", "BRL", "CAD", "CNY", "HKD", "IDR", "ILS", "INR", "KRW", "MXN", "MYR", "NZD"),
        *("PHP", "SGD", "THB", "ZAR", "UAH", "TWD"),
    }
)
SEEN_ENV = "MU3LAB_WALLOS_CURRENCY_SEEN"
# The script runs as Wallos's own web user, so its database files keep their owner.
AS_WEB_USER = ("su", "-s", "/bin/sh", "www-data", "-c", 'exec php -- "$@"', "php")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # urllib's own signature; returning None stops redirects
        return None


def _where(port: int, path: str, form: dict[str, str] | None = None) -> str:
    """Request ``path`` without following redirects; return where Wallos sends the browser ("" when it answers)."""
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST" if form else "GET")
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=30) as response:
            response.read(1024 * 1024)
            return ""
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return exc.headers.get("Location", "")
        raise


def currency(ctx: HookContext) -> str:
    chosen = ctx.env().get("WALLOS_CURRENCY", "").strip().upper()
    return chosen if chosen in CURRENCIES else "USD"


class Hooks(AppHooks):
    def bootstrap_account(self, ctx: HookContext) -> str:
        assert ctx.owner is not None
        port = ctx.app.manifest.service.local_port
        stage = "account_bootstrap"
        try:
            # Wallos sends every page to its registration form until its first account exists.
            if "registration.php" not in _where(port, "/login.php"):
                return "Existing Wallos kept; Open uses Authentik."
            name = (ctx.owner.get("display_name") or ctx.owner["username"]).strip()
            first, _, last = name.partition(" ")
            password = secrets.token_urlsafe(32)
            landing = _where(
                port,
                "/registration.php",
                {
                    "username": ctx.owner["username"],
                    "firstname": first,
                    "lastname": last,
                    "email": ctx.owner["email"],
                    "password": password,
                    "confirm_password": password,
                    "main_currency": currency(ctx),
                    "language": "en",
                },
            )
            if "registered=true" not in landing or "registration.php" in _where(port, "/login.php"):
                ctx.fail(stage, "account_provisioning_failed", "Wallos did not create its administrator account.")
        except (OSError, urllib.error.URLError) as exc:
            ctx.fail(stage, "account_provisioning_failed", f"Wallos did not answer its setup request ({exc}).")
        ctx.set_env({SEEN_ENV: "1"})
        return "Wallos administrator created for you; Open signs you in with Authentik."

    def periodic(self, ctx: HookContext) -> str:
        state = ControlState.runtime(ctx.facts.paths) if ctx.facts else ControlState.runtime()
        installation = state.installation(ctx.app.id) if state else None
        if not installation or installation["state"] != "running":
            return ""
        seen = ctx.env().get(SEEN_ENV, "0")
        result = ctx.run_script("wallos", "scripts/household-currency.php", AS_WEB_USER, args=[currency(ctx), seen])
        if not result.ok or "MU3LAB_WALLOS_CURRENCY_OK" not in result.raw:
            return ""
        now = result.outputs.get(SEEN_ENV, seen)
        if now != seen:
            ctx.set_env({SEEN_ENV: now})
        return "" if "changed=0" in result.raw else "Wallos: new accounts now use the household's currency."
