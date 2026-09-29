"""Fetch a provider API key through the provider's own sign-in page (OAuth PKCE).

Only OpenRouter offers this today. The dashboard sends the owner to
openrouter.ai with a PKCE challenge; OpenRouter sends them back with a
one-time code, and this module trades that code (plus the verifier only the
owner's browser knows) for a normal API key. The key then goes through the
same encrypted save-and-verify path as a pasted key and is never logged.
"""

from __future__ import annotations

import re

import httpx

OPENROUTER_KEYS_URL = "https://openrouter.ai/api/v1/auth/keys"
# RFC 7636: 43-128 characters from the unreserved set.
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class OAuthError(Exception):
    """A user-presentable failure; never contains the code or key."""


def exchange_openrouter_code(code: str, code_verifier: str, *, client: httpx.Client | None = None) -> str:
    code = code.strip()
    if not code or len(code) > 512 or not _VERIFIER.fullmatch(code_verifier):
        raise OAuthError("The OpenRouter sign-in did not complete. Start it again from AI providers.")
    owned = client is None
    http = client or httpx.Client(timeout=20.0)
    try:
        response = http.post(
            OPENROUTER_KEYS_URL,
            json={"code": code, "code_verifier": code_verifier, "code_challenge_method": "S256"},
        )
    except httpx.HTTPError as exc:
        raise OAuthError("OpenRouter is not answering. Try again in a minute.") from exc
    finally:
        if owned:
            http.close()
    try:
        key = str(response.json().get("key") or "") if response.status_code < 400 else ""
    except ValueError:
        key = ""
    if not key:
        raise OAuthError(
            "OpenRouter did not hand over a key (the sign-in link may have expired). Start it again from AI providers."
        )
    return key
