"""Same-origin JSON sign-in launch pages generated from a manifest."""

from __future__ import annotations

import base64
import hashlib
import html
import json

from ctl.manifest.models import AppManifest


def caddy_handler(manifest: AppManifest) -> str:
    oidc = manifest.sign_in.oidc
    launch = oidc.json_launch if oidc else None
    if not oidc or not launch or not launch.launcher:
        return ""
    settings = json.dumps(
        {"path": launch.path, "body": launch.body, "keys": launch.url_key, "session": launch.session_check_path}
    ).replace("<", "\\u003c")
    script = """(async function () {
  const cfg = SETTINGS;
  try {
    if (cfg.session) {
      const session = await fetch(cfg.session, {credentials: 'same-origin'});
      if (session.ok && (await session.json())?.user) { location.replace('/'); return; }
    }
    const body = JSON.parse(JSON.stringify(cfg.body).replaceAll('{origin}', location.origin));
    const r = await fetch(cfg.path, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    let target = await r.json();
    for (const key of cfg.keys) target = target?.[key];
    if (!r.ok || typeof target !== 'string' || !target) throw new Error();
    const url = new URL(target);
    if (url.protocol !== 'https:' || url.username || url.password) throw new Error();
    location.replace(url.href);
  } catch (_) {
    document.getElementById('status').textContent = 'Sign-in needs attention. Open the app to continue.';
  }
}());""".replace("SETTINGS", settings)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    page = (
        '<!doctype html><html><head><meta charset="utf-8"><title>Opening app</title></head>'
        f'<body><p id="status">Signing in…</p><a href="/">Open {html.escape(manifest.name)}</a>'
        f"<script>{script}</script></body></html>"
    )
    return f"""
\thandle {oidc.launch_path} {{
\t\theader Content-Type "text/html; charset=utf-8"
\t\theader Cache-Control "no-store"
\t\theader Referrer-Policy "no-referrer"
\t\theader Content-Security-Policy "default-src 'none'; script-src 'sha256-{digest}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
\t\trespond `{page}` 200
\t}}
"""
