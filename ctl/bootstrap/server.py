"""Mu3Lab :: ctl/bootstrap/server.py

WHAT: The local first-run setup page. Serves ctl/bootstrap/page.html and a
      small JSON API that scans this computer, runs the check-first installer
      (ctl/install.py) in a background thread, and relays the steps that need
      the user (creating accounts, approving Tailscale).
WHY:  ./install.sh prepares Python and an administrator session in the
      terminal, then hands the rest to this page so progress and next steps
      are visible in plain language.
RUN:  Started by ./install.sh (python -m ctl.bootstrap.server). It prints a
      one-time link containing a session token and opens it in the browser.
SECURITY: Binds 127.0.0.1 only. Every API call must carry the session token
      (X-Mu3Lab-Token), which web pages from other origins cannot read or
      send, and a Host header naming this loopback port, which defeats DNS
      rebinding. The page itself contains no secrets.
DEBUG: All state is in memory; restarting the server re-scans the host and
      the installer skips every step that is already done.
"""

from __future__ import annotations

import argparse
import hmac
import json
import re
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ctl import install

DEFAULT_PORT = 8799
ROOT = Path(__file__).resolve().parents[2]
PAGE = Path(__file__).with_name("page.html")
MAX_EVENTS = 2000
MAX_BODY = 64 * 1024


def tailnet_dashboard_url() -> str:
    """The node's private HTTPS dashboard URL, once Tailscale is connected."""
    try:
        proc = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
        payload = json.loads(proc.stdout) if proc.returncode == 0 else {}
        name = str(payload.get("Self", {}).get("DNSName", "")).rstrip(".")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""
    return f"https://{name}:{install.DASHBOARD_SERVE_PORT}/" if re.fullmatch(r"[A-Za-z0-9.-]+\.ts\.net", name) else ""


class Session:
    """Everything one running setup page knows. One instance per process."""

    def __init__(self, token: str, root: Path = ROOT) -> None:
        self.token = token
        self.root = root
        self.lock = threading.Lock()
        self.job: dict[str, Any] = install.new_job()
        self.job["status"] = "idle"
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self.user_input = threading.Event()
        self.scan: dict[str, dict] = {}
        self.scan_running = False
        self.scanned_at = 0.0

    # -- installer context -------------------------------------------------
    def _log_fn(self, step_id: str):
        def log(line: str) -> None:
            with self.lock:
                for step in self.job["steps"]:
                    if step["id"] == step_id:
                        step["log"].append(line[:2000])
                        del step["log"][:-400]
                        break

        return log

    def _emit(self, event: dict) -> None:
        with self.lock:
            self.job["events"].append(event)
            del self.job["events"][:-MAX_EVENTS]

    def _progress(self, step_id: str, update: dict) -> None:
        with self.lock:
            for step in self.job["steps"]:
                if step["id"] == step_id:
                    existing = step.get("progress") or {}
                    started = existing.get("started_at", time.time())
                    step["progress"] = {**existing, **update, "started_at": started, "updated_at": time.time()}
                    break

    def _wait_input(self, _step_id: str) -> dict:
        self.user_input.clear()
        self.user_input.wait()
        with self.lock:
            return dict(self.job["inputs"])

    def context(self) -> dict:
        return {
            "root": self.root,
            "log_fn": self._log_fn,
            "inputs": self.job["inputs"],
            "wait_input": self._wait_input,
            "stopped": self.stop.is_set,
            "emit": self._emit,
            "progress": self._progress,
        }

    # -- actions -----------------------------------------------------------
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start_scan(self) -> bool:
        with self.lock:
            if self.scan_running or self.busy():
                return False
            self.scan_running = True

        def run() -> None:
            try:
                results = install.scan({**self.context(), "log_fn": lambda _step: lambda _line: None})
            except Exception as exc:  # never leave the page spinning
                results = {"_error": {"done": False, "blocked": True, "detail": f"Scan failed: {exc}"}}
            with self.lock:
                self.scan = results
                self.scanned_at = time.time()
                self.scan_running = False

        threading.Thread(target=run, name="mu3lab-scan", daemon=True).start()
        return True

    def start_install(self) -> bool:
        with self.lock:
            if self.busy():
                return False
            self.job["status"] = "queued"
            self.stop.clear()
            self.user_input.clear()
            self.thread = threading.Thread(target=self._run_install, name="mu3lab-install", daemon=True)
            self.thread.start()
        return True

    def _run_install(self) -> None:
        try:
            install.run_job(self.job, self.context())
        except Exception as exc:
            with self.lock:
                self.job["status"] = "failed"
                self.job["error"] = f"The installer stopped unexpectedly: {exc}"
        # A finished run changes what is done; refresh the checklist.
        self.start_scan()

    def continue_install(self) -> None:
        """The user finished a manual step: resume the paused installer."""
        with self.lock:
            waiting = next((step for step in self.job["steps"] if step.get("status") == "waiting"), None)
            manual = bool(waiting and (waiting.get("prompt") or {}).get("kind") == "manual_setup")
            if manual and waiting is not None:
                self.job["inputs"][f"{waiting['id']}_confirmed"] = True
            self.user_input.set()
            thread = self.thread
        if thread is not None and thread.is_alive() and not manual:
            return  # the Tailscale step is paused inside the run and resumes itself
        # Other prompts end the run; start a new one that resumes at that step.
        if thread is not None:
            thread.join(timeout=2.0)
        self.start_install()

    def cancel(self) -> bool:
        with self.lock:
            running = self.busy()
            self.stop.set()
            self.user_input.set()
        return running

    def reset_authentik_admin(self) -> dict:
        with self.lock:
            waiting = next((step for step in self.job["steps"] if step.get("status") == "waiting"), None)
            allowed = bool(
                waiting
                and waiting["id"] == "authentik_setup"
                and (waiting.get("prompt") or {}).get("recovery_action") == "reset_authentik_admin"
            )
        if not allowed:
            return {"ok": False, "error": "Administrator recovery is only available at the Authentik account step."}
        return install.reset_authentik_admin_password({"log_fn": self._log_fn})

    def status(self) -> dict:
        with self.lock:
            job = self.job
            return {
                "job": {
                    "status": job["status"],
                    "error": job.get("error", ""),
                    "steps": [
                        {
                            key: step.get(key)
                            for key in ("id", "label", "phase", "status", "prompt", "error", "detail", "progress")
                        }
                        | {"log": step["log"][-60:]}
                        for step in job["steps"]
                    ],
                },
                "phases": [title for title, _ids in install.PHASES],
                "scan": dict(self.scan),
                "scanning": self.scan_running,
                "scanned_at": self.scanned_at,
                "dashboard_url": tailnet_dashboard_url() if job["status"] == "ready" else "",
            }


class Handler(BaseHTTPRequestHandler):
    server_version = "Mu3LabSetup"

    @property
    def session(self) -> Session:
        return self.server.session  # type: ignore[attr-defined]

    def log_message(self, *_args: Any) -> None:  # keep the terminal readable
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: dict, status: int = 200) -> None:
        self._send(status, json.dumps(value).encode("utf-8"), "application/json")

    def _host_allowed(self) -> bool:
        port = self.server.server_port  # type: ignore[attr-defined]
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Mu3Lab-Token", "")
        return self._host_allowed() and hmac.compare_digest(supplied, self.session.token)

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._json({"error": "This page only answers on 127.0.0.1."}, 403)
            return
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            if not self._authorized():
                self._json({"error": "session"}, 401)
                return
            self._json(self.session.status())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json({"error": "request too large"}, 413)
            return
        self.rfile.read(length)
        if not self._authorized():
            self._json({"error": "session"}, 401)
            return
        session = self.session
        if self.path == "/api/scan":
            self._json({"started": session.start_scan()})
        elif self.path == "/api/install":
            if not session.start_install():
                self._json({"error": "The installer is already running."}, 409)
                return
            self._json({"started": True})
        elif self.path == "/api/continue":
            session.continue_install()
            self._json({"continued": True})
        elif self.path == "/api/cancel":
            self._json({"cancelled": session.cancel()})
        elif self.path == "/api/authentik-admin/reset":
            result = session.reset_authentik_admin()
            self._json(result, 200 if result.get("ok") else 409)
        else:
            self._json({"error": "not found"}, 404)


def serve(port: int, open_browser: bool) -> int:
    session = Session(secrets.token_urlsafe(32))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"Could not open the setup page on port {port}: {exc.strerror}.")
        print("Another setup page may already be running. Close it, or run ./install.sh --port 8800.")
        return 1
    server.session = session  # type: ignore[attr-defined]
    link = f"http://127.0.0.1:{port}/#token={session.token}"
    session.start_scan()
    opening = "Mu3Lab setup is open in your browser." if open_browser else "Open Mu3Lab setup in your browser:"
    print(
        f"\n  {opening}\n"
        + ("  If it did not open, paste this link into your browser:\n" if open_browser else "")
        + f"    {link}\n\n"
        + "  Keep this window open until setup finishes. Press Ctrl+C to stop.\n",
        flush=True,
    )
    if open_browser:
        webbrowser.open(link)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        session.cancel()
        print("\nSetup page stopped. Run ./install.sh to continue where you left off.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mu3Lab first-run setup page")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="print the link instead of opening a browser")
    args = parser.parse_args(argv)
    return serve(args.port, not args.no_open)


if __name__ == "__main__":
    raise SystemExit(main())
