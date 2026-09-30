"""Same-origin launch pages for apps whose OIDC entry point is a JSON POST."""

from __future__ import annotations

import base64
import hashlib

SCRIPTS = {
    "actual-budget": """(async function () {
  try {
    const r = await fetch('/account/login', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({loginMethod: 'openid', returnUrl: location.origin})});
    const data = await r.json();
    if (!r.ok || data.status !== 'ok' || !data.data.returnUrl) throw new Error();
    const url = new URL(data.data.returnUrl);
    if (url.protocol !== 'https:' || url.username || url.password) throw new Error();
    location.replace(url.href);
  } catch (_) {
    document.getElementById('status').textContent = 'Sign-in needs attention. Open Actual Budget to continue.';
  }
}());""",
    "lobehub": """(async function () {
  try {
    const session = await fetch('/api/auth/get-session', {credentials: 'same-origin'});
    if (session.ok && (await session.json())?.user) { location.replace('/'); return; }
    const r = await fetch('/api/auth/sign-in/oauth2', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({providerId: 'authentik', callbackURL: location.origin + '/',
        newUserCallbackURL: location.origin + '/', disableRedirect: true})});
    const data = await r.json();
    if (!r.ok || !data.url) throw new Error();
    const url = new URL(data.url);
    if (url.protocol !== 'https:' || url.username || url.password) throw new Error();
    location.replace(url.href);
  } catch (_) {
    document.getElementById('status').textContent = 'Sign-in needs attention. Open LobeChat to continue.';
  }
}());""",
}


def caddy_handler(service_id: str) -> str:
    script = SCRIPTS.get(service_id)
    if not script:
        return ""
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    page = (
        '<!doctype html><html><head><meta charset="utf-8"><title>Opening app</title></head>'
        '<body><p id="status">Signing in…</p><a href="/">Open app</a>'
        f"<script>{script}</script></body></html>"
    )
    return f"""
\thandle /__mu3lab/login {{
\t\theader Content-Type "text/html; charset=utf-8"
\t\theader Cache-Control "no-store"
\t\theader Referrer-Policy "no-referrer"
\t\theader Content-Security-Policy "default-src 'none'; script-src 'sha256-{digest}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
\t\trespond `{page}` 200
\t}}
"""
