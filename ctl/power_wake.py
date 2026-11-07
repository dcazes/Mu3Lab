"""Mu3Lab's wake page: the one thing that runs while Mu3Lab is turned off.

``ctl/power.py`` starts it (as mu3lab-wake.service) once every app, sign-in, Caddy and the
dashboard have stopped. It answers on Caddy's loopback addresses, so every Mu3Lab link that
Tailscale Serve publishes shows "Mu3Lab is off" with one button, Turn on, instead of an error.

It can do nothing else: it shows no household data and changes nothing but the power, so it
needs no sign-in (sign-in is off too). Only the household's own Tailscale devices reach it.
The button starts ``python -m ctl.power run-on`` as its own systemd unit, which stops this page
last and hands its addresses back to Caddy.

Standard library only, run with ``python -I``: it stays a few megabytes and never loads the
dashboard's code.
"""

from __future__ import annotations

import argparse
import html
import json
import secrets
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

RECORD = "power.json"  # ctl.power.RECORD
PUBLIC_KEYS = ("state", "step", "done", "total", "since", "error")  # ctl.power.public
DASHBOARD_PORT = 19460  # ctl.power.DASHBOARD_CADDY_PORT
RUN_UNIT = "mu3lab-power-on"  # ctl.power.RUN_UNITS["on"]
POWER_PATH = "/api/v1/power"
WAKE_HEADER = "X-Mu3Lab-Wake"


class Wake:
    def __init__(self, root: Path, state: Path) -> None:
        self.root = root
        self.state = state
        self.starting = threading.Lock()

    def record(self) -> dict[str, Any] | None:
        try:
            value = json.loads((self.state / RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def status(self) -> dict[str, Any]:
        record = self.record()
        if record is None:  # turning on just finished: Caddy takes these addresses back now
            return {"state": "on", "wake": True}
        return {key: record[key] for key in PUBLIC_KEYS if record.get(key) not in (None, "")} | {"wake": True}

    def turn_on(self) -> tuple[bool, str]:
        with self.starting:
            if self.status().get("state") in {"turning_on", "on"}:
                return True, "Mu3Lab is already turning on."
            argv = [
                "systemd-run",
                "--user",
                "--collect",
                f"--unit={RUN_UNIT}",
                f"--working-directory={self.root}",
                f"--setenv=MU3LAB_RUNTIME_ROOT={self.state.parent}",
                str(self.root / ".venv" / "bin" / "python"),
                "-m",
                "ctl.power",
                "run-on",
            ]
            try:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"Turning on could not start: {exc}", file=sys.stderr, flush=True)
                return False, "Mu3Lab could not start turning on. On the computer, run ./start.sh"
            output = (result.stdout + result.stderr).strip()
            if result.returncode and "already" not in output.lower():
                print(f"Turning on could not start: {output}", file=sys.stderr, flush=True)
                return False, "Mu3Lab could not start turning on. On the computer, run ./start.sh"
            return True, "Mu3Lab is turning on."


def handler(wake: Wake) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "Mu3Lab"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            pass  # every Tailscale device's visit is not worth a journal line

        def _send(self, status: int, body: bytes, kind: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, value: dict[str, Any]) -> None:
            self._send(status, json.dumps(value).encode(), "application/json")

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/__mu3lab_caddy_health":
                self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")
            elif path == POWER_PATH:
                self._json(HTTPStatus.OK, wake.status())
            elif path in {"/favicon.ico", "/manifest.webmanifest", "/sw.js"}:
                self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")
            else:
                nonce = secrets.token_urlsafe(16)
                policy = (
                    f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
                    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                )
                # Every path shows the page, so an app's bookmark says Mu3Lab is off rather than failing.
                status = HTTPStatus.OK if path == "/" else HTTPStatus.SERVICE_UNAVAILABLE
                self._send(
                    status, page(nonce).encode(), "text/html; charset=utf-8", {"Content-Security-Policy": policy}
                )

        def do_POST(self) -> None:
            # Only this page's own script sends the header; another site's form or script cannot
            # without a preflight, which this server refuses.
            if self.path.split("?", 1)[0] != f"{POWER_PATH}/on" or self.headers.get(WAKE_HEADER) != "on":
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "message": "Not allowed."})
                return
            ok, message = wake.turn_on()
            self._json(HTTPStatus.ACCEPTED if ok else HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": ok, "message": message})

        def do_OPTIONS(self) -> None:
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"", "text/plain")

    return Handler


def serve(wake: Wake, ports: list[int]) -> list[ThreadingHTTPServer]:
    servers = []
    for port in ports:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler(wake))
        except OSError as exc:
            print(f"Port {port} is busy, so it shows no wake page: {exc}", file=sys.stderr, flush=True)
            if port == DASHBOARD_PORT:
                raise
            continue
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True, name=f"wake-{port}").start()
        servers.append(server)
    return servers


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, required=True, help="the Mu3Lab checkout")
    parser.add_argument("--state", type=Path, required=True, help="Mu3Lab's state folder")
    args = parser.parse_args(argv)
    wake = Wake(args.root, args.state)
    record = wake.record()
    if record is None:
        print("Mu3Lab is on, so the wake page is not needed.", flush=True)
        return 0
    ports = sorted({DASHBOARD_PORT, *(int(port) for port in record.get("ports", []) if str(port).isdigit())})
    try:
        servers = serve(wake, ports)
    except OSError:
        return 1  # systemd tries again in a few seconds, once Caddy has let go
    print(f"Mu3Lab is off. The wake page answers on {len(servers)} addresses.", flush=True)
    threading.Event().wait()
    return 0


def page(nonce: str) -> str:
    return PAGE.replace("{nonce}", html.escape(nonce)).replace("{power}", POWER_PATH).replace("{header}", WAKE_HEADER)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Mu3Lab is off</title>
<style nonce="{nonce}">
:root {
  --bg: #fbfbfc; --surface: #ffffff; --border: #e7e7ec; --text: #1b1c20; --text-2: #5d616a;
  --accent: #5e6ad2; --accent-hover: #4e5ac4; --on-accent: #ffffff; --track: #f1f1f4;
  --red-text: #b8321f; --red-soft: rgba(211, 63, 44, 0.09);
  --shadow: 0 4px 16px rgba(18, 19, 24, 0.06), 0 1px 3px rgba(18, 19, 24, 0.05);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0e0f12; --surface: #141519; --border: rgba(255, 255, 255, 0.07); --text: #eeeff2;
    --text-2: #a0a4ad; --accent: #6c77d8; --accent-hover: #7b86e2; --track: #222328;
    --red-text: #f37a6a; --red-soft: rgba(240, 100, 82, 0.13); --shadow: none;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
  background: var(--bg); color: var(--text);
  font: 15px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
main {
  width: 100%; max-width: 400px; padding: 28px; border-radius: 12px;
  background: var(--surface); border: 1px solid var(--border); box-shadow: var(--shadow);
}
.mark { display: flex; align-items: center; gap: 8px; color: var(--text-2); font-size: 13px; font-weight: 500; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #a0a4ac; }
.dot.is-busy { background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
h1 { margin: 16px 0 6px; font-size: 20px; font-weight: 600; letter-spacing: -0.01em; }
p { margin: 0; color: var(--text-2); }
button {
  margin-top: 22px; width: 100%; height: 40px; border: 0; border-radius: 8px; cursor: pointer;
  background: var(--accent); color: var(--on-accent); font: inherit; font-weight: 500;
}
button:hover { background: var(--accent-hover); }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
button[hidden], .progress[hidden], .error[hidden] { display: none; }
.progress { margin-top: 22px; height: 6px; border-radius: 3px; background: var(--track); overflow: hidden; }
.bar { height: 100%; width: 4%; background: var(--accent); transition: width 0.4s ease; }
.error { margin-top: 16px; padding: 10px 12px; border-radius: 8px; background: var(--red-soft); color: var(--red-text); }
@keyframes pulse { 50% { opacity: 0.35; } }
@media (prefers-reduced-motion: reduce) { .dot.is-busy { animation: none; } .bar { transition: none; } }
</style>
</head>
<body>
<main>
  <div class="mark"><span class="dot" id="dot"></span>Mu3Lab</div>
  <h1 id="title">Mu3Lab is off</h1>
  <p id="detail">Your apps, chat assistants and sign-in are turned off. Turning on takes a few minutes.</p>
  <div class="progress" id="progress" hidden><div class="bar" id="bar"></div></div>
  <p class="error" id="error" role="alert" hidden></p>
  <button type="button" id="on">Turn on Mu3Lab</button>
</main>
<script nonce="{nonce}">
const $ = (id) => document.getElementById(id);
let busy = false;
let opening = 0;
function show(status) {
  const state = status.state;
  const turning = state === 'turning_on' || state === 'turning_off';
  busy = turning || state === 'on';
  $('dot').className = busy ? 'dot is-busy' : 'dot';
  $('on').hidden = busy;
  $('progress').hidden = !busy;
  $('error').hidden = !status.error;
  $('error').textContent = status.error || '';
  if (state === 'turning_off') {
    $('title').textContent = 'Mu3Lab is turning off';
    $('detail').textContent = (status.step || 'Stopping') + '…';
  } else if (busy) {
    $('title').textContent = 'Turning on Mu3Lab';
    $('detail').textContent = (state === 'on' ? 'Opening the dashboard' : status.step || 'Starting') + '…';
  } else {
    $('title').textContent = status.error ? 'Mu3Lab did not turn on' : 'Mu3Lab is off';
    $('detail').textContent = status.error
      ? 'Some of Mu3Lab may already be running again.'
      : 'Your apps, chat assistants and sign-in are turned off. Turning on takes a few minutes.';
    $('on').textContent = status.error ? 'Try again' : 'Turn on Mu3Lab';
  }
  const share = status.total ? Math.max(4, Math.round((100 * (status.done || 0)) / status.total)) : 4;
  $('bar').style.width = (state === 'on' ? 100 : share) + '%';
}
async function poll() {
  let response;
  try {
    response = await fetch('{power}', { cache: 'no-store', redirect: 'manual' });
  } catch {
    return;  // the moment between this page stopping and the dashboard starting
  }
  const wake = (response.headers.get('content-type') || '').startsWith('application/json')
    ? await response.json().then((body) => (body && body.wake ? body : null), () => null)
    : null;
  if (wake) {
    show(wake);
    return;
  }
  // Something else answers this address now: the dashboard (or its sign-in) is back.
  if (busy && ![502, 503, 504].includes(response.status) && ++opening > 1) location.reload();
}
$('on').addEventListener('click', async () => {
  show({ state: 'turning_on', step: 'Starting' });
  try {
    const response = await fetch('{power}/on', { method: 'POST', headers: { '{header}': 'on' } });
    const body = await response.json();
    if (!body.ok) show({ state: 'off', error: body.message });
  } catch {
    show({ state: 'off', error: 'Mu3Lab could not be reached. Try again in a moment.' });
  }
});
poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
