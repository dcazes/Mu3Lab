"""Same-origin sign-in launch pages generated from a manifest.

Two kinds: a JSON POST whose reply names Authentik's address (Actual
Budget), and a form on the app's own sign-in page that must be submitted
with that page's anti-forgery token (Dawarich).
"""

from __future__ import annotations

import base64
import hashlib
import html
import json

from ctl.manifest.models import AppManifest


def _form_script(page: str, action: str) -> str:
    settings = json.dumps({"page": page, "action": action}).replace("<", "\\u003c")
    return """(async function () {
  const cfg = SETTINGS;
  try {
    const r = await fetch(cfg.page, {credentials: 'same-origin'});
    // Already signed in: the app sends its sign-in page on to the app itself.
    if (r.redirected && new URL(r.url).pathname !== cfg.page) { location.replace('/'); return; }
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    const found = [...doc.forms].find((f) => new URL(f.action, location.href).pathname === cfg.action);
    if (!r.ok || !found || (found.method || '').toLowerCase() !== 'post') throw new Error();
    const form = document.createElement('form');
    form.method = 'post';
    form.action = cfg.action;
    for (const input of found.querySelectorAll('input[type=hidden]')) {
      const copy = document.createElement('input');
      copy.type = 'hidden';
      copy.name = input.name;
      copy.value = input.value;
      form.appendChild(copy);
    }
    document.body.appendChild(form);
    form.submit();
  } catch (_) {
    document.getElementById('status').textContent = 'Sign-in needs attention. Open the app to continue.';
  }
}());""".replace("SETTINGS", settings)


def _page(manifest: AppManifest, launch_path: str, script: str, form_action: str) -> str:
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    page = (
        '<!doctype html><html><head><meta charset="utf-8"><title>Opening app</title></head>'
        f'<body><p id="status">Signing in…</p><a href="/">Open {html.escape(manifest.name)}</a>'
        f"<script>{script}</script></body></html>"
    )
    # Only the dashboard may frame this page, so its embedded chat signs in without a click.
    return f"""
\thandle {launch_path} {{
\t\theader Content-Type "text/html; charset=utf-8"
\t\theader Cache-Control "no-store"
\t\theader Referrer-Policy "no-referrer"
\t\theader Content-Security-Policy "default-src 'none'; script-src 'sha256-{digest}'; connect-src 'self'; {form_action}base-uri 'none'; frame-ancestors https://{{http.request.host}}:8446"
\t\trespond `{page}` 200
\t}}
"""


def caddy_handler(manifest: AppManifest) -> str:
    oidc = manifest.sign_in.oidc
    if oidc and oidc.form_launch:
        # The form posts to the app, which redirects the browser on to Authentik on this host.
        script = _form_script(oidc.form_launch.page, oidc.form_launch.action)
        return _page(manifest, oidc.launch_path, script, "form-action 'self' https://{http.request.host}; ")
    launch = oidc.json_launch if oidc else None
    if not oidc or not launch or not launch.launcher:
        return ""
    settings = json.dumps(
        {"path": launch.path, "body": launch.body, "keys": launch.url_key, "session": launch.session_check_path}
    ).replace("<", "\\u003c")
    script = """(async function () {
  const cfg = SETTINGS;
  // ?next= continues to one of this app's own pages; anything else opens the app's home.
  const asked = new URLSearchParams(location.search).get('next') || '/';
  const next = /^\\/(?!\\/)[\\w\\-.\\/?=&%]*$/.test(asked) ? asked : '/';
  try {
    if (cfg.session) {
      const session = await fetch(cfg.session, {credentials: 'same-origin'});
      if (session.ok && (await session.json())?.user) { location.replace(next); return; }
    }
    const body = JSON.parse(JSON.stringify(cfg.body).replaceAll('{origin}/', location.origin + next)
      .replaceAll('{origin}', location.origin));
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
    return _page(manifest, oidc.launch_path, script, "")
