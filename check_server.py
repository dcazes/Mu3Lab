"""Mu3Lab :: check_server.py
WHAT: Zero-dependency local dashboard for the fresh-user check flow. Serves
      one static page (tools/check_page.html) plus a tiny JSON API that runs
      the unit suite and the host preflight behind three GATED cards:
      ① tests → ② preflight → ③ install (remediate-only: check first,
      fix exactly the gap, skip what's ready; waiting rows need you).
WHY:  The real dashboard needs .venv + npm build + install.sh to exist. This
      scaffold needs only system python3 (stdlib), so `git clone` + one
      command is enough to SEE host status. Small on purpose: ~200 lines,
      deleted — not extended — once Phase 11 lands.
RUN:  `./check.sh` (preferred: prechecks python, proves venv, execs this).
      Direct: `python3 check_server.py [--port N] [--no-open]`.
      Open http://127.0.0.1:8799 — buttons are manual (locked decision).
DEBUG: All state is in-memory (see State). Restarting the server resets the
      card chain honestly. Logs go to stdout; nothing is written to disk.
      API errors are JSON {error, hint} with HTTP 409 for ordering/lock
      violations and 501 for the Phase-7 install stub.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------
DEFAULT_PORT = 8799   # 8787 belongs to the real dashboard; never collide
TEST_TIMEOUT = 120    # kill a hung suite run after N seconds
MAX_EVENTS = 2000     # cap in-memory test event log (oldest dropped)
ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "tools" / "check_page.html"
STATE_FILE = ROOT / ".state" / "check-progress.json"
CODE_VERSION = 3      # bump on ANY api/report-shape change (invalidates disk)


def tailnet_dashboard_url() -> str:
    """Return the node's canonical private HTTPS URL, if Tailscale is ready."""
    try:
        proc = subprocess.run(["tailscale", "status", "--json"],
                              capture_output=True, text=True, timeout=5)
        payload = json.loads(proc.stdout) if proc.returncode == 0 else {}
        name = str(payload.get("Self", {}).get("DNSName", "")).rstrip(".")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""
    if re.fullmatch(r"[A-Za-z0-9.-]+\.ts\.net", name):
        return "https://" + name + "/"
    return ""


class State:
    """In-memory progression flags + latest results. One instance per process.

    tests_green_this_session gates card ②; preflight_passed gates card ③
    (passed = zero FAILs among BLOCKING checks; TODOs are card ③'s list).
    A restart clears both — by design (see module docstring).
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.code_version = CODE_VERSION
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.test_run: dict | None = None      # active run or None
        self.test_events: list[dict] = []      # JSON lines from the runner
        self.tests_green = False               # card ① exit-0 seen?
        self.tests_summary: dict | None = None
        self.preflight_passed = False          # card ② passed (no blocking FAILs)?
        self.preflight_report: dict | None = None
        # Install job (card ③): at most one active; threads + events live here.
        self.install_job: dict | None = None
        self.install_thread: threading.Thread | None = None
        self.install_stop = threading.Event()
        self.install_input = threading.Event()


def can_run_preflight(state: State) -> tuple[bool, str]:
    """Pure gate for card ②. Factored for tests (no HTTP involved)."""
    if state.test_run is not None:
        return False, "tests still running — wait for the suite to finish"
    if not state.tests_green:
        return False, "run card ① first: preflight unlocks on a green suite"
    return True, ""


def can_open_install(state: State) -> tuple[bool, str]:
    """Pure gate for card ③. Factored for tests (no HTTP involved)."""
    if not state.preflight_passed:
        return False, "run card ② first: install unlocks on green preflight"
    return True, ""


def _test_worker(state: State, python: str) -> None:
    """Background thread: spawn the JSON runner, buffer its lines into state.

    Fixed argv, shell=False — no user input anywhere near this call, so no
    injection surface. Kills the child on TEST_TIMEOUT. The runner path
    honors MU3LAB_TEST_RUNNER (tests only): the regression test points it at
    a 3-line fake so the suite never runs itself recursively.
    """
    runner = os.environ.get("MU3LAB_TEST_RUNNER",
                            str(ROOT / "tools" / "run_tests.py"))
    try:
        proc = subprocess.Popen(
            [python, runner],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=str(ROOT),
            env={**os.environ, "MU3LAB_BOOTSTRAP_TESTS": "1"},
        )
    except OSError as exc:
        with state.lock:
            state.test_run = None
            state.test_events.append({"type": "error",
                                      "detail": f"could not start runner: {exc}"})
        return
    with state.lock:
        if state.test_run is not None:  # killed while spawning; clean up
            state.test_run["proc"] = proc
        else:  # kill arrived before we registered: stop immediately
            proc.kill()
            return
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"type": "raw", "detail": line[:500]}
            with state.lock:
                state.test_events.append(event)
                if len(state.test_events) > MAX_EVENTS:
                    del state.test_events[:len(state.test_events) - MAX_EVENTS]
        rc = proc.wait(timeout=TEST_TIMEOUT)
        if proc.stdout is not None:
            proc.stdout.close()
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = 124
        with state.lock:
            state.test_events.append({"type": "error",
                                      "detail": f"suite timed out after {TEST_TIMEOUT}s"})
    with state.lock:
        summary = next((e for e in reversed(state.test_events)
                        if e.get("type") == "summary" and e.get("phase") == "done"),
                       None)
        state.tests_summary = summary
        # Green = runner exit 0 AND a done-summary with zero failures/errors.
        state.tests_green = (rc == 0 and summary is not None
                             and not summary.get("failed") and not summary.get("errored"))
        state.test_run = None
    # OUTSIDE the lock on purpose: save_progress() takes state.lock itself,
    # and threading.Lock is not reentrant — nesting here self-deadlocks the
    # worker forever and wedges every endpoint (the indefinite stall).
    save_progress(state)


def _install_worker(state: State) -> None:
    """Background thread: run install.run_job with a server-backed ctx.

    Tailscale uses a browser approval flow; no tailnet credential is accepted
    by this server or stored in the job.
    """
    from ctl import install
    job = state.install_job
    assert job is not None
    MAX_INSTALL_EVENTS = 2000

    def emit(event: dict) -> None:
        with state.lock:
            job["events"].append(event)
            if len(job["events"]) > MAX_INSTALL_EVENTS:
                del job["events"][:len(job["events"]) - MAX_INSTALL_EVENTS]

    def log_fn(step_id: str):
        def _log(line: str) -> None:
            with state.lock:
                for step in job["steps"]:
                    if step["id"] == step_id:
                        step["log"].append(line)
                        break
                job["events"].append({"type": "log", "id": step_id,
                                      "line": line[:2000]})
        return _log

    def wait_input(step_id: str) -> dict:
        # Blocks until the UI posts a key/continue (or kill). Returns a COPY
        # so later wipes can't race the runner.
        state.install_input.clear()
        state.install_input.wait()
        with state.lock:
            return dict(job["inputs"])

    ctx = {"root": ROOT, "log_fn": log_fn, "inputs": job["inputs"],
           "wait_input": wait_input,
           "stopped": state.install_stop.is_set, "emit": emit}
    try:
        install.run_job(job, ctx)
    except Exception as exc:  # noqa: BLE001 (job must end, never hang)
        with state.lock:
            job["status"] = "failed"
            job["events"].append({"type": "error",
                                  "detail": f"installer crashed: {exc}"})
    finally:
        # Stop the elevated worker when the job ends (no lingering root process).
        from ctl import privilege as _priv
        _priv.release_elevation()
        emit({"type": "summary", "phase": "done", "status": job["status"]})


def _serialize_job(job: dict | None) -> dict | None:
    """Job shape the page renders (per-step logs capped at 50 lines)."""
    if job is None:
        return None
    return {"id": job["id"], "status": job["status"],
            "steps": [{"id": s["id"], "label": s["label"], "status": s["status"],
                       "log": s["log"][-50:], "prompt": s.get("prompt"),
                       "error": s.get("error", ""), "detail": s.get("detail", "")}
                      for s in job["steps"]]}


def _repo_head() -> str:
    """Current git HEAD (or "" when unavailable). Used to invalidate saved
    progress after code changes — a green suite from older code proves
    nothing about the current tree."""
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10, cwd=str(ROOT))
    except OSError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def build_identity() -> dict:
    """Answerable build stamp for /api/state + page footer: short HEAD and
    dirty flag. Added so "which code is answering?" never again needs a
    paste-and-deduce session (the Caddy skipped+failed contradiction)."""
    head = _repo_head()
    dirty = False
    if head:
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True,
                text=True, timeout=10, cwd=str(ROOT))
            dirty = proc.returncode == 0 and bool(proc.stdout.strip())
        except OSError:
            dirty = False
    return {"head": head[:12], "dirty": dirty,
            "code_version": CODE_VERSION}


def save_progress(state: State, path: Path = STATE_FILE) -> None:
    """Persist unlock-level progress to disk (gitignored .state/).

    Stores ONLY verdicts + the preflight report — never logs, keys, or
    secrets. Test EVENTS are deliberately excluded: card ①'s table rebuilds
    from a fresh run (the page says so when restoring).
    """
    with state.lock:
        payload = {"code_version": state.code_version,
                   "repo_head": _repo_head(),
                   "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "tests_green": state.tests_green,
                   "tests_summary": state.tests_summary,
                   "preflight_passed": state.preflight_passed,
                   "preflight_report": state.preflight_report}
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # persistence is convenience; a failed save must never break runs


def load_progress(state: State, path: Path = STATE_FILE) -> str:
    """Restore unlocks saved by save_progress(). Returns "restored", "stale"
    (code changed → re-run required), or "none". Never raises."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "none"
    if payload.get("code_version") != state.code_version:
        return "stale"
    head = _repo_head()
    if head and payload.get("repo_head") and payload["repo_head"] != head:
        return "stale"
    with state.lock:
        state.tests_green = bool(payload.get("tests_green"))
        state.tests_summary = payload.get("tests_summary")
        state.preflight_passed = bool(payload.get("preflight_passed"))
        state.preflight_report = payload.get("preflight_report")
    return "restored" if (state.tests_green or state.preflight_passed) else "none"


def response_headers(content_type: str, length: int) -> dict[str, str]:
    """Headers for every response. no-store is load-bearing (see _json)."""
    return {"Content-Type": content_type,
            "Cache-Control": "no-store",
            "Content-Length": str(length)}


class Handler(BaseHTTPRequestHandler):
    """Routes. server_version is pinned down to avoid fingerprint noise."""

    server_version = "Mu3LabCheck/0.1"

    # -- helpers -----------------------------------------------------------
    def _json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        for key, value in response_headers("application/json",
                                           len(body)).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, *args):  # keep stdout for app logs, not HTTP noise
        pass

    # -- GET ---------------------------------------------------------------
    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler hook, keep it)
        state = self._state()
        if self.path == "/":
            try:
                page = PAGE.read_bytes()
            except OSError:
                self._json({"error": "page missing",
                            "hint": "tools/check_page.html not found"}, 500)
                return
            self.send_response(200)
            for key, value in response_headers("text/html; charset=utf-8",
                                               len(page)).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(page)
        elif self.path == "/api/state":
            with state.lock:
                self._json({
                    "server_started_at": state.started_at,
                    "code_version": state.code_version,
                    "build": build_identity(),
                    "tests_running": state.test_run is not None,
                    "tests_green": state.tests_green,
                    "tests_summary": state.tests_summary,
                    "preflight_passed": state.preflight_passed,
                    "preflight_report": state.preflight_report,
                    "install_job": _serialize_job(state.install_job),
                    "tailnet_dashboard_url": tailnet_dashboard_url(),
                })
        elif self.path == "/api/tests/events":
            with state.lock:
                self._json({"events": list(state.test_events)})
        elif self.path == "/api/install/script":            # Combined admin script (headless fallback): every privileged
            # command recorded so far, as one `sudo bash` script.
            from ctl import install as _install
            with state.lock:
                job = state.install_job
                script = _install.collect_privileged_script(job) if job else ""
            self._json({"script": script})
        elif self.path == "/api/install/state":
            # GET alias (the page polls state/events with GET; POST works too).
            with state.lock:
                self._json({"job": _serialize_job(state.install_job)})
        elif self.path == "/api/install/events":
            with state.lock:
                job = state.install_job
                self._json({"events": list(job["events"]) if job else []})
        else:
            self._json({"error": "not found",
                        "hint": "see / for the dashboard"}, 404)

    # -- POST --------------------------------------------------------------
    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler hook, keep it)
        state = self._state()
        length = int(self.headers.get("Content-Length", 0))
        self._body = self.rfile.read(length)
        if self.path == "/api/tests/run":
            with state.lock:
                if state.test_run is not None:
                    self._json({"error": "already running",
                                "hint": "kill the active run first"}, 409)
                    return
                state.test_events = []
                state.tests_green = False
                state.preflight_passed = False  # new suite invalidates chain
                state.preflight_report = None
                state.test_run = {"status": "running"}
            thread = threading.Thread(target=_test_worker,
                                      args=(state, sys.executable),
                                      daemon=True)
            thread.start()
            self._json({"started": True})
        elif self.path == "/api/tests/kill":
            with state.lock:
                run = state.test_run
                proc = (run or {}).get("proc")
                state.test_run = None
                if proc is not None:
                    try:
                        proc.kill()  # worker sees rc!=0, marks suite red
                    except OSError:
                        pass
            if run is None:
                self._json({"error": "nothing running"}, 409)
            else:
                self._json({"killed": True})
        elif self.path == "/api/preflight/run":
            ok, reason = can_run_preflight(state)
            if not ok:
                self._json({"error": "locked", "hint": reason}, 409)
                return
            # In-process: milliseconds, no subprocess. The running server
            # always executes ITS OWN startup code — edited files take effect
            # on restart, which the version banner enforces (no hot-reload
            # hacks: reload() leaves stale closures and double state).
            try:
                from ctl import preflight
                report = preflight.run_all()
            except Exception as exc:  # noqa: BLE001 (must survive, report it)
                self._json({"error": "preflight crashed",
                            "hint": f"{type(exc).__name__}: {exc}"}, 500)
                return
            with state.lock:
                state.preflight_report = report
                # Gate: install_ready (zero FAILs among BLOCKING checks).
                # Fall back to legacy "ok" for reports predating the field.
                state.preflight_passed = bool(report.get("install_ready",
                                                         report.get("ok")))
            save_progress(state)
            self._json(report)
        elif self.path == "/api/install/start":
            ok, reason = can_open_install(state)
            if not ok:
                self._json({"error": "locked", "hint": reason}, 409)
                return
            from ctl import install as _install
            with state.lock:
                thread = state.install_thread
                alive = thread is not None and thread.is_alive()
                if alive:
                    self._json({"error": "already running",
                                "hint": "kill the active install first"}, 409)
                    return
                job = _install.new_job()
                import uuid as _uuid
                job["id"] = _uuid.uuid4().hex[:12]
                job["status"] = "queued"
                state.install_job = job
                state.install_stop.clear()
                state.install_input.clear()
                state.install_thread = threading.Thread(
                    target=_install_worker, args=(state,), daemon=True)
                state.install_thread.start()
            self._json({"started": True, "job_id": job["id"]})
        elif self.path == "/api/install/state":
            with state.lock:
                self._json({"job": _serialize_job(state.install_job)})
        elif self.path == "/api/install/events":
            with state.lock:
                job = state.install_job
                self._json({"events": list(job["events"]) if job else []})
        elif self.path == "/api/install/continue":
            # "Check again" for login-URL / relogin waits: wake the runner to
            # re-poll (join status, group liveness).
            restart_manual = False
            with state.lock:
                if state.install_job is None:
                    self._json({"error": "no job"}, 409)
                    return
                waiting = next((step for step in state.install_job["steps"]
                                if step.get("status") == "waiting"), None)
                if waiting and (waiting.get("prompt") or {}).get("kind") == "manual_setup":
                    state.install_job["inputs"][f"{waiting['id']}_confirmed"] = True
                    marker = {
                        "vaultwarden_setup": "vaultwarden_account",
                        "authentik_setup": "authentik_admin",
                        "authentik_users": "authentik_users",
                        "dashboard_protection": "dashboard_protection",
                    }.get(waiting["id"])
                    if marker:
                        from ctl import bootstrap_state as _bootstrap_state
                        _bootstrap_state.confirm(marker)
                    restart_manual = True
                state.install_input.set()
            if restart_manual:
                with state.lock:
                    thread = state.install_thread
                    if thread is None or not thread.is_alive():
                        state.install_stop.clear()
                        state.install_input.clear()
                        state.install_thread = threading.Thread(
                            target=_install_worker, args=(state,), daemon=True)
                        state.install_thread.start()
            self._json({"continued": True})
        elif self.path == "/api/install/kill":
            with state.lock:
                thread = state.install_thread
                alive = thread is not None and thread.is_alive()
                state.install_stop.set()
                state.install_input.set()  # unblock a waiting join
            if not alive:
                self._json({"error": "nothing running"}, 409)
            else:
                self._json({"killed": True})
        elif self.path == "/api/install/retry":
            # Resume from the first non-ready step (run_job skips readies).
            with state.lock:
                job = state.install_job
                thread = state.install_thread
                if job is None:
                    self._json({"error": "no job",
                                "hint": "start an install first"}, 409)
                    return
                if thread is not None and thread.is_alive():
                    self._json({"error": "already running"}, 409)
                    return
                job["status"] = "queued"
                state.install_stop.clear()
                state.install_input.clear()
                state.install_thread = threading.Thread(
                    target=_install_worker, args=(state,), daemon=True)
                state.install_thread.start()
            self._json({"retried": True})
        elif self.path == "/api/service/stop":
            # Stop OUR dashboard service ONLY (mu3lab-ctl, user scope — no
            # privilege involved). Refuses anything else: the dashboard must
            # never kill arbitrary processes. Body: {"mode": "once"|"disable"}
            # ("disable" also turns off start-at-boot).
            try:
                payload = json.loads(self._body.decode() or "{}")
            except (ValueError, AttributeError):
                self._json({"error": "bad request",
                            "hint": 'send JSON {"mode": "once"|"disable"}'}, 400)
                return
            mode = payload.get("mode", "once")
            if mode not in ("once", "disable"):
                self._json({"error": "bad request",
                            "hint": 'mode must be "once" or "disable"'}, 400)
                return
            import subprocess as _sp
            stop = _sp.run(["systemctl", "--user", "stop", "mu3lab-ctl"],
                           capture_output=True, text=True, timeout=30)
            if stop.returncode != 0:
                self._json({"error": "stop failed",
                            "hint": (stop.stdout + stop.stderr).strip()
                            or "is mu3lab-ctl running?"}, 500)
                return
            if mode == "disable":
                _sp.run(["systemctl", "--user", "disable", "mu3lab-ctl"],
                        capture_output=True, timeout=30)
            self._json({"stopped": True, "autostart_off": mode == "disable"})
        else:
            self._json({"error": "not found"}, 404)


def main(argv: list[str] | None = None) -> int:
    """Parse flags, serve forever on 127.0.0.1. Ctrl-C stops (no cleanup needed)."""
    parser = argparse.ArgumentParser(description="Mu3Lab zero-install check dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true",
                        help="print the URL instead of opening a browser")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.state = State()  # type: ignore[attr-defined]
    restored = load_progress(server.state)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Mu3Lab check dashboard: {url}")
    print("Cards unlock in order: ① tests → ② preflight → ③ install.")
    if restored == "restored":
        print("Previous progress restored (cards ①② unlocks kept).")
    elif restored == "stale":
        print("Previous progress is stale (code changed) — re-run cards.")
    if not args.no_open:
        webbrowser.open(url)  # best effort; SSH sessions just keep the URL
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ncheck dashboard stopped (nothing was installed or changed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
