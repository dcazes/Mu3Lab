"""Mu3Lab :: ctl/install.py

WHAT: Card-③ execution engine. Ordered identity-first remediation steps
      (host → Docker → runtime → local Vaultwarden → Tailscale → Authentik →
      protected dashboard), each check-first:
      `ready` states are SKIPPED, every other state maps to exactly one fix.
      Long user actions (docker-group relogin, tailscale join) surface as
      `waiting` prompts instead of failures; resume continues from them.
WHY:  Installing over healthy components is structurally impossible here:
      no step runs without its check reporting a gap first. Steps never
      never accept user secrets. Tailscale uses its normal browser approval
      flow, so the installer never handles reusable tailnet credentials.
RUN:  Driven by check_server.py (/api/install/*). Pure dispatch helpers
      (fix_for_state) are unit-testable with mocked actions; live probes run
      only inside fix()/check() with the real host.
DEBUG: Every fix logs its commands before running (via actions.py). Job dict
      shape: {id, status, steps:[{id,label,status,log[]}],
      events:[...], inputs:{}}. Events mirror tools/run_tests.py JSON lines:
      {"type": "step|log|prompt|summary", ...}.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

from ctl import actions, preflight, privilege
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Constants: versions, package lists, ports. Reasons inline.
# ---------------------------------------------------------------------------
BASE_PACKAGES = ["curl", "git", "ca-certificates", "gnupg",
                 "python3", "python3-pip", "python3-venv", "restic"]
# Current Node.js LTS channel for the supported v1 host. Keep this explicit so
# the bootstrap remains reviewable and reproducible; advance it deliberately
# when the LTS line changes rather than silently tracking a moving target.
NODE_LTS_MAJOR = 24
NODESOURCE_KEY_URL = "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key"
NODESOURCE_LIST = ("deb [signed-by=/etc/apt/keyrings/nodesource.gpg] "
                   f"https://deb.nodesource.com/node_{NODE_LTS_MAJOR}.x nodistro main")
DOCKER_KEY_URL = "https://download.docker.com/linux/{slug}/gpg"
DOCKER_PACKAGES = ["docker-ce", "docker-ce-cli", "containerd.io",
                   "docker-buildx-plugin", "docker-compose-plugin"]
def tailscale_key_url(distro: str, codename: str) -> str:
    """GPG key URL for the Tailscale apt repo; pure and unit-testable."""
    family = "debian" if distro == "debian" else "ubuntu"
    return f"https://pkgs.tailscale.com/stable/{family}/{codename}.gpg"
CADDY_PORT = 19460        # Caddy dashboard listener on loopback
CADDY_HEALTH_PATH = "/__mu3lab_caddy_health"
AUTHENTIK_PROXY_PORT = 19461
VAULTWARDEN_PROXY_PORT = 19462
OPEN_WEBUI_PROXY_PORT = 19463
# Authentik must own standard HTTPS. Its browser UI creates API and WebSocket
# URLs from the public origin and does not reliably preserve a high port.
AUTHENTIK_SERVE_PORT = "443"
DASHBOARD_SERVE_PORT = "8446"
VAULTWARDEN_SERVE_PORT = "8443"
OPEN_WEBUI_SERVE_PORT = "8445"
TS_HOSTNAME = "mu3lab"
TAILSCALE_JOIN_TIMEOUT = "120s"  # first-time control-plane registration can be slow
TAILSCALE_WORKER_TIMEOUT = 130    # bounds the worker beyond the CLI's own join window
AUTHENTIK_READINESS_TIMEOUT = 600  # first migrations can legitimately take minutes
AUTHENTIK_READINESS_INTERVAL = 5
AUTHENTIK_HTTP_GRACE_TIMEOUT = 60  # Compose health passed; allow host port to settle
VAULTWARDEN_HTTP_GRACE_TIMEOUT = 60  # Rocket may bind just after Compose starts


# ---------------------------------------------------------------------------
# Pure dispatch: state -> fix action name. Tested without touching the host.
# ---------------------------------------------------------------------------

# Maps (step_id, state) to the fix function suffix. "skip" means check-first
# short-circuit; "wait_*" means user action required (never failure).
DISPATCH = {
    ("host_base", "missing"): "apt_base",
    ("host_base", "ready"): "skip",
    ("node", "absent"): "nodesource_install",
    ("node", "old"): "nodesource_install",
    ("node", "ready"): "skip",
    ("venv", "no_venv"): "create_venv",
    ("venv", "ready"): "skip",
    ("pip_deps", "missing"): "pip_install",
    ("pip_deps", "ready"): "skip",
    ("dashboard_src", "missing"): "report_incomplete",
    ("dashboard_src", "ready"): "skip",
    ("dashboard_build", "stale"): "npm_build",
    ("dashboard_build", "ready"): "skip",
    ("root_env", "missing"): "write_env",
    ("root_env", "ready"): "skip",
    ("runtime_layout", "missing"): "create_runtime_layout",
    ("runtime_layout", "ready"): "skip",
    ("service", "no_unit"): "render_unit",
    ("service", "inactive"): "start_service",
    ("service", "unhealthy"): "restart_service",
    ("service", "ready"): "skip",
    ("docker", "absent"): "docker_install",
    ("docker", "daemon_down"): "docker_start",
    ("docker", "unverified"): "docker_start",
    ("docker", "old_engine"): "docker_upgrade",
    ("docker", "no_compose"): "docker_compose_plugin",
    ("docker", "no_access"): "docker_group",    ("docker", "stale_login"): "docker_group",
    ("docker", "no_group"): "docker_group",
    ("docker", "no_networks"): "docker_group",  # group first; networks later
    ("docker", "ready"): "skip",
    ("docker_networks", "missing"): "create_networks",
    ("docker_networks", "denied"): "report_denied",
    ("docker_networks", "ready"): "skip",
    ("caddy", "down"): "caddy_up",
    ("caddy", "ready"): "skip",
    ("vaultwarden", "down"): "vaultwarden_up",
    ("vaultwarden", "ready"): "skip",
    ("vaultwarden_setup", "needs_user"): "manual_vaultwarden",
    ("vaultwarden_setup", "ready"): "skip",
    ("tailscale_pkg", "absent"): "tailscale_install",
    ("tailscale_pkg", "daemon_down"): "tailscale_start",
    ("tailscale_pkg", "unjoined"): "skip",   # join is the NEXT step's job
    ("tailscale_pkg", "ready"): "skip",
    ("tailscale_join", "unjoined"): "guided_join",
    ("tailscale_join", "ready"): "skip",
    ("vaultwarden_serve", "unshared"): "share_vaultwarden",
    ("vaultwarden_serve", "ready"): "skip",
    ("authentik", "down"): "authentik_up",
    ("authentik", "ready"): "skip",
    ("authentik_serve", "unshared"): "share_authentik",
    ("authentik_serve", "ready"): "skip",
    ("open_webui_serve", "unshared"): "share_open_webui",
    ("open_webui_serve", "ready"): "skip",
    ("authentik_setup", "needs_user"): "manual_authentik",
    ("authentik_setup", "ready"): "skip",
    ("dashboard_protection", "needs_user"): "manual_dashboard_protection",
    ("dashboard_protection", "needs_apply"): "apply_dashboard_protection",
    ("dashboard_protection", "needs_attention"): "manual_dashboard_protection",
    ("dashboard_protection", "ready"): "skip",
    ("serve", "unshared"): "share_tailnet",
    ("serve", "ready"): "skip",
}


def fix_for_state(step_id: str, state: str) -> str:
    """Pure mapping used by the runner AND the tests. Unknown → "unknown"."""
    return DISPATCH.get((step_id, state), "unknown")


# ---------------------------------------------------------------------------
# Live probes (thin wrappers over preflight + subprocess; injectable).
# ---------------------------------------------------------------------------

def _dpkg_present(package: str) -> bool:
    try:
        proc = subprocess.run(["dpkg", "-s", package], capture_output=True,
                              text=True, timeout=15)
        return proc.returncode == 0 and "Status: install ok installed" in proc.stdout
    except OSError:
        return False


def _repo_codename() -> str:
    """Apt codename for Docker/NodeSource repos (noble, bookworm, ...)."""
    info = preflight._parse_os_release(
        Path("/etc/os-release").read_text(encoding="utf-8", errors="replace"))
    return info.get("UBUNTU_CODENAME") or info.get("VERSION_CODENAME", "")


def _distro_slug() -> str:
    """'ubuntu' or 'debian' for Docker's download path (derivatives → ubuntu)."""
    info = preflight._parse_os_release(
        Path("/etc/os-release").read_text(encoding="utf-8", errors="replace"))
    if info.get("ID") == "debian" and "ubuntu" not in info.get("ID_LIKE", ""):
        return "debian"
    return "ubuntu"


def _tcp_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def _caddy_health_status() -> int:
    """Probe Caddy's own local health handler, not its upstream dashboard."""
    import urllib.error as _url_error
    import urllib.request as _url

    class _NoRedirect(_url.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            return None

    request = _url.Request(
        f"http://127.0.0.1:{CADDY_PORT}{CADDY_HEALTH_PATH}")
    try:
        with _url.build_opener(_NoRedirect).open(request, timeout=5) as response:
            return int(response.status)
    except _url_error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def _repair_managed_apt_keys(log: Callable[[str], None]) -> dict:
    """Refresh keys for every Mu3Lab APT source that is currently present.

    ``apt-get update`` validates all configured sources, not only the source
    needed by the current install step.  A test reset can therefore leave a
    valid ``docker.list`` or ``nodesource.list`` beside a deleted keyring and
    make an otherwise unrelated Node/Tailscale step fail.  Repair only the
    three source files owned by Mu3Lab; unrelated repositories are untouched.
    """
    managed = [
        ((Path("/etc/apt/sources.list.d/nodesource.list"),
          Path("/etc/apt/sources.list.d/nodesource.sources")),
         NODESOURCE_KEY_URL,
         (Path("/etc/apt/keyrings/nodesource.gpg"),
          Path("/usr/share/keyrings/nodesource.gpg"))),
        ((Path("/etc/apt/sources.list.d/docker.list"),
          Path("/etc/apt/sources.list.d/docker.sources")),
         DOCKER_KEY_URL.format(slug=_distro_slug()),
         (Path("/etc/apt/keyrings/docker.asc"),)),
        ((Path("/etc/apt/sources.list.d/tailscale.list"),
          Path("/etc/apt/sources.list.d/tailscale.sources")),
         tailscale_key_url(_distro_slug(), _repo_codename() or "noble"),
         (Path("/etc/apt/keyrings/tailscale.gpg"),)),
    ]
    for sources, url, keyrings in managed:
        present = [source for source in sources if source.is_file()]
        if not present:
            continue
        tmp = Path("/tmp") / f"mu3lab-repair-{keyrings[0].name}"
        res = actions.fetch_url(url, tmp, log)
        if not res["ok"]:
            return _propagate(res)
        data = tmp.read_bytes()
        for keyring in keyrings:
            res = actions.write_root_bytes(str(keyring), data, log, mode="644")
            if not res["ok"]:
                return _propagate(res)
        log("repaired signing key for " + ", ".join(source.name for source in present))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Fix implementations. Each takes (check, ctx) and returns a result dict:
# {"ok": True} | {"ok": False, "error"} | {"waiting": True, "prompt": {...}}.
# ctx = {"root": Path, "log": fn(step_id, line), "inputs": dict,
#        "wait_input": fn(step_id) -> dict, "stopped": fn() -> bool}.
# ---------------------------------------------------------------------------

def fix_host_base(check: dict, ctx: dict) -> dict:
    missing = [pkg for pkg in BASE_PACKAGES if not _dpkg_present(pkg)]
    if not missing:
        return {"ok": True, "skipped": True}
    _update_progress(ctx, "host_base", phase="preparing_packages",
                     activity="Preparing the host package sources.", timeout_seconds=600)
    # A previous Mu3Lab install may have left our repo definitions behind
    # after a test reset removed their keyrings. Remove only the legacy files
    # owned by this installer before the first apt update.
    for repo_file in ("/etc/apt/sources.list.d/nodesource.list",
                      "/etc/apt/sources.list.d/nodesource.sources",
                      "/etc/apt/sources.list.d/docker.list",
                      "/etc/apt/sources.list.d/docker.sources",
                      "/etc/apt/sources.list.d/tailscale.list",
                      "/etc/apt/sources.list.d/tailscale.sources"):
        res = actions.remove_root_file(repo_file, ctx["log_fn"]("host_base"))
        if not res["ok"]:
            return _propagate(res)
    res = actions.apt_update(ctx["log_fn"]("host_base"))
    if not res["ok"]:
        return _propagate(res)
    _update_progress(ctx, "host_base", phase="installing_packages",
                     activity="Installing the required host packages.", timeout_seconds=600)
    res = actions.apt_install(missing, ctx["log_fn"]("host_base"))
    return _propagate(res)


def fix_node(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("node")
    _update_progress(ctx, "node", phase="preparing_repository",
                     activity="Preparing the signed Node.js LTS repository.", timeout_seconds=600)
    res = _repair_managed_apt_keys(log)
    if not res["ok"]:
        return _propagate(res)
    key_dest = Path("/tmp/mu3lab-nodesource.gpg")
    _update_progress(ctx, "node", phase="downloading_key",
                     activity="Downloading the Node.js repository signing key.", timeout_seconds=600)
    res = actions.fetch_url(NODESOURCE_KEY_URL, key_dest, log)
    if not res["ok"]:
        return _propagate(res)
    # Binary key bytes: write_root_bytes, never a str round-trip.
    res = actions.write_root_bytes("/etc/apt/keyrings/nodesource.gpg",
                                   key_dest.read_bytes(), log)
    if not res["ok"]:
        return _propagate(res)
    res = actions.write_root_file("/etc/apt/sources.list.d/nodesource.list",
                                  NODESOURCE_LIST + "\n", log)
    if not res["ok"]:
        return _propagate(res)
    _update_progress(ctx, "node", phase="refreshing_packages",
                     activity="Refreshing package metadata for Node.js.", timeout_seconds=600)
    res = actions.apt_update(log)
    if not res["ok"]:
        return _propagate(res)
    _update_progress(ctx, "node", phase="installing_package",
                     activity="Installing the current supported Node.js LTS.", timeout_seconds=600)
    return _propagate(actions.apt_install(["nodejs"], log))


def _venv_check(root: Path) -> dict:
    """Venv-only readiness (NOT the combined bundle check: dist belongs to
    the dashboard_build step). States: no_venv | ready."""
    if (root / ".venv" / "bin" / "python").exists():
        return {"name": "venv", "status": "ok",
                "detail": "Project virtualenv present.",
                "action": "", "state": "ready", "blocking": False}
    return {"name": "venv", "status": "missing",
            "detail": "Project virtualenv (.venv) missing.",
            "action": "step 3 creates it.", "state": "no_venv",
            "blocking": False}


def _runtime_layout_check(root: Path) -> dict:
    """Check the approved persistent-data root without creating it."""
    paths = RuntimePaths(root)
    user_paths = (paths.data, paths.backups, paths.runtime, paths.projects)
    try:
        missing = [path for path in user_paths if not path.is_dir()]
        # Secrets are deliberately root-only. Checking the directory itself
        # must not require the operator to read its contents.
        secrets_ready = paths.secrets.is_dir()
        accessible = [path for path in user_paths
                      if not os.access(path, os.R_OK | os.X_OK)]
    except OSError:
        missing, secrets_ready, accessible = list(user_paths), False, list(user_paths)
    if missing or not secrets_ready or accessible:
        return {"name": "runtime_layout", "status": "missing",
                "detail": "Persistent runtime layout is missing or inaccessible to the operator.",
                "action": "step 3 repairs /srv/mu3lab ownership and permissions.",
                "state": "missing", "blocking": False}
    return {"name": "runtime_layout", "status": "ok",
            "detail": "Persistent runtime layout is ready.", "action": "",
            "state": "ready", "blocking": False}


def fix_runtime_layout(check: dict, ctx: dict) -> dict:
    """Create persistent paths using the auditable privilege boundary."""
    return _propagate(actions.ensure_runtime_layout(RuntimePaths().root,
                                                    getpass.getuser(),
                                                    ctx["log_fn"]("runtime_layout")))


def _pip_check(root: Path) -> dict:
    """Control-plane deps importable from the venv? States: missing | ready.

    Probes the real import (not a marker file) so half-finished installs are
    detected. fastapi stands in for the whole requirements file.
    """
    venv_py = root / ".venv" / "bin" / "python"
    if not venv_py.exists():
        return {"name": "pip_deps", "status": "missing",
                "detail": "No virtualenv yet (venv step runs first).",
                "action": "step 3 creates it, then installs packages.",
                "state": "missing", "blocking": False}
    try:
        proc = subprocess.run([str(venv_py), "-c", "import fastapi"],
                              capture_output=True, text=True, timeout=30)
    except OSError as exc:
        return {"name": "pip_deps", "status": "missing",
                "detail": f"Cannot probe venv: {exc}.",
                "action": "step 3 reinstalls packages.", "state": "missing",
                "blocking": False}
    if proc.returncode != 0:
        return {"name": "pip_deps", "status": "missing",
                "detail": "Control-plane packages not installed in .venv.",
                "action": "step 3 installs them.", "state": "missing",
                "blocking": False}
    return {"name": "pip_deps", "status": "ok",
            "detail": "Control-plane packages importable.",
            "action": "", "state": "ready", "blocking": False}


def _src_check(root: Path) -> dict:
    """Dashboard source present? States: ready | missing (unfixable live —
    a missing source tree means a broken checkout, not a gap to fill)."""
    needed = [root / "dashboard" / "package.json",
              root / "dashboard" / "src" / "main.tsx"]
    absent = [str(path.relative_to(root)) for path in needed if not path.is_file()]
    if absent:
        return {"name": "dashboard_src", "status": "fail",
                "detail": f"Missing from checkout: {', '.join(absent)}.",
                "action": "Re-clone the repository (files, not setup).",
                "state": "missing", "blocking": True}
    return {"name": "dashboard_src", "status": "ok",
            "detail": "Dashboard source present.",
            "action": "", "state": "ready", "blocking": False}


def _build_check(root: Path) -> dict:
    """Built bundle fresh? States: ready | stale (missing OR older than src).

    Compares dist/index.html mtime against the newest file under src/ so
    edited sources rebuild automatically on re-run.
    """
    dist_index = root / "dashboard" / "dist" / "index.html"
    src_dir = root / "dashboard" / "src"
    if not dist_index.is_file():
        return {"name": "dashboard_build", "status": "missing",
                "detail": "Built dashboard (dist/) missing.",
                "action": "step 3 builds it.", "state": "stale",
                "blocking": False}
    try:
        newest_src = max(path.stat().st_mtime for path in src_dir.rglob("*")
                         if path.is_file())
    except OSError:
        newest_src = 0.0
    if newest_src > dist_index.stat().st_mtime:
        return {"name": "dashboard_build", "status": "missing",
                "detail": "Sources newer than built bundle.",
                "action": "step 3 rebuilds it.", "state": "stale",
                "blocking": False}
    return {"name": "dashboard_build", "status": "ok",
            "detail": "Built dashboard fresh.",
            "action": "", "state": "ready", "blocking": False}


def _env_check(root: Path) -> dict:
    """Root .env holds both MU3LAB_* tokens? States: ready | missing."""
    from ctl import secrets as _secrets
    values: dict[str, str] = {}
    env_path = root / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    absent = [key for key in _secrets.ROOT_ENV_KEYS if not values.get(key)]
    if absent:
        return {"name": "root_env", "status": "missing",
                "detail": f"Missing secret keys: {', '.join(absent)}.",
                "action": "step 3 generates them (existing keys kept).",
                "state": "missing", "blocking": False}
    return {"name": "root_env", "status": "ok",
            "detail": "Secret keys present.",
            "action": "", "state": "ready", "blocking": False}


def _user_service_active(unit: str) -> bool:
    """Return whether one user-level service is active, without raising."""
    try:
        return subprocess.run(["systemctl", "--user", "is-active", unit],
                              capture_output=True, text=True,
                              timeout=15).returncode == 0
    except OSError:
        return False


def _service_check(root: Path) -> dict:
    """Web and durable-worker units installed and healthy?
    States: no_unit | inactive | unhealthy | ready."""
    import urllib.request as _url
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    units = ("mu3lab-ctl.service", "mu3lab-worker.service")
    if any(not (unit_dir / unit).is_file() for unit in units):
        return {"name": "service", "status": "missing",
                "detail": "Control-plane startup entries are not installed.",
                "action": "step 3 installs and starts it.",
                "state": "no_unit", "blocking": False}
    dashboard_active = _user_service_active("mu3lab-ctl.service")
    worker_active = _user_service_active("mu3lab-worker.service")
    if not dashboard_active:
        return {"name": "service", "status": "missing",
                "detail": "Dashboard startup entry is present but the dashboard is not running.",
                "action": "step 3 starts the dashboard service.", "state": "inactive",
                "blocking": False}
    if not worker_active:
        return {"name": "service", "status": "missing",
                "detail": "Dashboard is running, but the background workflow worker is not.",
                "action": "step 3 restarts the workflow worker.", "state": "inactive",
                "blocking": False}
    try:
        with _url.urlopen("http://127.0.0.1:8787/api/health", timeout=5) as resp:
            healthy = resp.status == 200
    except OSError:
        healthy = False
    if not healthy:
        return {"name": "service", "status": "missing",
                "detail": "Running but not answering health checks.",
                "action": "step 3 restarts it.", "state": "unhealthy",
                "blocking": False}
    return {"name": "service", "status": "ok",
            "detail": "Dashboard service answering on :8787.",
            "action": "", "state": "ready", "blocking": False}


def fix_pip_deps(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("pip_deps")
    venv_pip = ctx["root"] / ".venv" / "bin" / "pip"
    if not venv_pip.exists():
        return {"ok": False, "error": "no venv pip (venv step must run first)"}
    _update_progress(ctx, "pip_deps", phase="installing_packages",
                     activity="Installing control-plane packages into the project virtualenv.", timeout_seconds=600)
    log("$ .venv/bin/pip install -r ctl/requirements.txt")
    try:
        proc = subprocess.run([str(venv_pip), "install", "-r",
                               str(ctx["root"] / "ctl" / "requirements.txt")],
                              capture_output=True, text=True, timeout=600,
                              cwd=str(ctx["root"]))
    except OSError as exc:
        return {"ok": False, "error": f"pip failed: {exc}"}
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
    for line in tail:
        log(line)
    if proc.returncode != 0:
        return {"ok": False, "error": "pip install failed (see log)."}
    return {"ok": True}


def fix_dashboard_src(check: dict, ctx: dict) -> dict:
    # Unfixable by design: reached only when the checkout itself lacks files.
    return {"ok": False, "error": check.get("detail", "dashboard source missing")}


def fix_dashboard_build(check: dict, ctx: dict) -> dict:
    """npm ci (only if node_modules absent) + npm run build, as the USER.

    Long step (minutes on first run); output tailed into the log so the UI
    never looks stuck. No privilege involved at any point.
    """
    log = ctx["log_fn"]("dashboard_build")
    dashdir = ctx["root"] / "dashboard"
    _update_progress(ctx, "dashboard_build", phase="preparing_build",
                     activity="Preparing the dashboard dependency/build step.", timeout_seconds=900)
    # The 5-line tail once hid the real cause (missing lockfile); on failure
    # log every "npm error" line plus a wider tail, and always name the npm
    # debug log so the cause is one copy-paste away.
    def _log_failure(proc, what: str) -> dict:
        lines = (proc.stdout + proc.stderr).strip().splitlines()
        error_lines = [line for line in lines if "npm error" in line.lower()]
        log(f"$ {what} failed:")
        for line in (error_lines + lines[-30:])[:40]:
            log(line)
        log("Full npm log: ~/.npm/_logs/ (latest debug-*.log)")
        return {"ok": False, "error": f"{what} failed (see log)."}
    if not (dashdir / "node_modules").is_dir():
        if (dashdir / "package-lock.json").is_file():
            _update_progress(ctx, "dashboard_build", phase="downloading_packages",
                             activity="Downloading the locked dashboard packages.", timeout_seconds=900)
            log("$ npm ci  (in dashboard/)")
            try:
                proc = subprocess.run(["npm", "ci"], capture_output=True,
                                      text=True, timeout=900, cwd=str(dashdir))
            except OSError as exc:
                return {"ok": False,
                        "error": f"npm ci failed: {exc} (is Node installed?)"}
            if proc.returncode != 0:
                return _log_failure(proc, "npm ci")
            for line in (proc.stdout + proc.stderr).strip().splitlines()[-5:]:
                log(line)
        else:
            # No lockfile (shouldn't happen — repo commits one): npm install
            # resolves fresh instead of failing like `ci` would.
            log("$ npm install  (no lockfile; resolving fresh)")
            _update_progress(ctx, "dashboard_build", phase="downloading_packages",
                             activity="Downloading dashboard packages.", timeout_seconds=900)
            try:
                proc = subprocess.run(["npm", "install"], capture_output=True,
                                      text=True, timeout=900, cwd=str(dashdir))
            except OSError as exc:
                return {"ok": False,
                        "error": f"npm install failed: {exc} (is Node installed?)"}
            if proc.returncode != 0:
                return _log_failure(proc, "npm install")
            for line in (proc.stdout + proc.stderr).strip().splitlines()[-5:]:
                log(line)
    _update_progress(ctx, "dashboard_build", phase="building_dashboard",
                     activity="Compiling the dashboard interface.", timeout_seconds=900)
    log("$ npm run build  (in dashboard/)")
    try:
        proc = subprocess.run(["npm", "run", "build"], capture_output=True,
                              text=True, timeout=900, cwd=str(dashdir))
    except OSError as exc:
        return {"ok": False, "error": f"npm run build failed: {exc}"}
    for line in (proc.stdout + proc.stderr).strip().splitlines()[-10:]:
        log(line)
    if proc.returncode != 0:
        return {"ok": False, "error": "dashboard build failed (see log)."}
    return {"ok": True}


def fix_root_env(check: dict, ctx: dict) -> dict:
    from ctl import secrets as _secrets
    _values, added = _secrets.ensure_root_env(ctx["root"])
    log = ctx["log_fn"]("root_env")
    if added:
        log(f"generated keys (names only): {', '.join(added)} → .env (0600)")
    else:
        log("all keys already present — touched nothing")
    return {"ok": True, "skipped": not added}


def fix_service(check: dict, ctx: dict) -> dict:
    """Render web/worker units → linger → enable → start → verify web health."""
    import urllib.request as _url
    log = ctx["log_fn"]("service")
    state = check.get("state", "")
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_names = ("mu3lab-ctl.service", "mu3lab-worker.service")
    if state == "no_unit":
        unit_dir.mkdir(parents=True, exist_ok=True)
        for unit_name in unit_names:
            template = ctx["root"] / "deploy" / unit_name
            if not template.is_file():
                return {"ok": False, "error": f"deploy/{unit_name} missing from checkout"}
            rendered = template.read_text(encoding="utf-8").replace(
                "@MU3LAB_ROOT@", str(ctx["root"]))
            unit_path = unit_dir / unit_name
            unit_path.write_text(rendered, encoding="utf-8")
            log(f"rendered {unit_path}")
        res = actions.privilege.run_privileged(
            ["loginctl", "enable-linger", getpass.getuser()], log)
        if res.get("need_terminal") or not res.get("ok"):
            return _propagate(res)
    if state in ("no_unit", "inactive", "unhealthy"):
        rc, out = actions.privilege._exec(
            ["systemctl", "--user", "daemon-reload"])
        log("$ systemctl --user daemon-reload")
        for unit_name in unit_names:
            rc, out = actions.privilege._exec(
                ["systemctl", "--user", "enable", unit_name])
            log(f"$ systemctl --user enable {unit_name}")
            if rc == 0:
                rc, out = actions.privilege._exec(
                    ["systemctl", "--user", "restart", unit_name])
                log(f"$ systemctl --user restart {unit_name}")
            log(out or f"(exit {rc})")
            if rc != 0:
                return {"ok": False, "error": f"could not start {unit_name} (see log)"}
        import time as _time
        for _attempt in range(30):
            try:
                with _url.urlopen("http://127.0.0.1:8787/api/health", timeout=2) as resp:
                    if (resp.status == 200
                            and _user_service_active("mu3lab-ctl.service")
                            and _user_service_active("mu3lab-worker.service")):
                        log("dashboard and workflow worker are running")
                        return {"ok": True}
            except OSError:
                pass
            _time.sleep(2)
        dashboard_active = _user_service_active("mu3lab-ctl.service")
        worker_active = _user_service_active("mu3lab-worker.service")
        if not dashboard_active:
            return {"ok": False, "error": "dashboard service did not stay running"}
        if not worker_active:
            return {"ok": False, "error": "background workflow worker did not stay running"}
        return {"ok": False, "error": "dashboard service started but :8787 never answered"}
    return {"ok": True, "skipped": True}


def fix_venv(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("venv")
    venv_py = ctx["root"] / ".venv" / "bin" / "python"
    if venv_py.exists():
        return {"ok": True, "skipped": True}
    _update_progress(ctx, "venv", phase="creating_environment",
                     activity="Creating the isolated Python environment.", timeout_seconds=300)
    log("$ python3 -m venv .venv")
    try:
        proc = subprocess.run(["python3", "-m", "venv", str(ctx["root"] / ".venv")],
                              capture_output=True, text=True, timeout=300,
                              cwd=str(ctx["root"]))
    except OSError as exc:
        return {"ok": False, "error": f"venv creation failed: {exc}"}
    log((proc.stdout + proc.stderr).strip() or "(created)")
    if proc.returncode != 0 or not venv_py.exists():
        return {"ok": False, "error": "venv creation failed (need python3-venv?)"}
    return {"ok": True}


def fix_docker(check: dict, ctx: dict) -> dict:
    """Dispatch on the granular docker state — exactly one remedy each."""
    log = ctx["log_fn"]("docker")
    state = check.get("state", "")
    _update_progress(ctx, "docker", phase="preparing_engine",
                     activity="Preparing Docker and its signed package source.", timeout_seconds=900)
    if state == "absent":
        res = _repair_managed_apt_keys(log)
        if not res["ok"]:
            return _propagate(res)
        slug = _distro_slug()
        codename = _repo_codename()
        key_tmp = Path("/tmp/mu3lab-docker.asc")
        res = actions.fetch_url(DOCKER_KEY_URL.format(slug=slug), key_tmp, log)
        if not res["ok"]:
            return _propagate(res)
        res = actions.write_root_bytes("/etc/apt/keyrings/docker.asc",
                                       key_tmp.read_bytes(), log, mode="644")
        if not res["ok"]:
            return _propagate(res)
        arch = "amd64" if platform.machine() == "x86_64" else "arm64"
        repo = (f"deb [arch={arch} signed-by=/etc/apt/keyrings/docker.asc] "
                f"https://download.docker.com/linux/{slug} {codename} stable\n")
        res = actions.write_root_file("/etc/apt/sources.list.d/docker.list", repo, log)
        if not res["ok"]:
            return _propagate(res)
        res = actions.apt_update(log)
        if not res["ok"]:
            return _propagate(res)
        _update_progress(ctx, "docker", phase="installing_engine",
                         activity="Downloading and installing Docker Engine.", timeout_seconds=900)
        res = actions.apt_install(DOCKER_PACKAGES, log)
        if not res["ok"]:
            return _propagate(res)
        res = actions.systemctl_enable_now("docker", log)
        if not res["ok"]:
            return _propagate(res)
    elif state in ("daemon_down", "unverified"):
        res = actions.systemctl_enable_now("docker", log)
        if not res["ok"]:
            return _propagate(res)
    elif state == "old_engine":
        res = _repair_managed_apt_keys(log)
        if not res["ok"]:
            return _propagate(res)
        res = actions.apt_update(log)
        if not res["ok"]:
            return _propagate(res)
        res = actions.apt_install(DOCKER_PACKAGES, log)
        if not res["ok"]:
            return _propagate(res)
    elif state == "no_compose":
        res = actions.apt_install(["docker-compose-plugin"], log)
        if not res["ok"]:
            return _propagate(res)
    if state in ("absent", "old_engine", "no_compose", "daemon_down",
                 "unverified", "no_group", "stale_login", "no_networks",
                 "no_access"):
        # Membership (idempotent): ensures the user is in the group. Whether
        # THIS process can use it yet is the restart checkpoint's question —
        # getgrouplist() would lie here (it reads /etc/group, not process
        # credentials), so this step never judges liveness.
        user = getpass.getuser()
        res = actions.usermod_add_group(user, "docker", log)
        if not res["ok"]:
            return _propagate(res)
    return {"ok": True}


def _networks_check(ctx: dict) -> dict:
    """Shared-network presence, probed the SAME way the fix executes.

    Symmetry rule (learned the hard way): a step verifies with the transport
    it fixes with. The fix runs through actions.docker_cmd (sg when the
    process lacks the group), so the check does too — otherwise a stale
    checker reports failure on networks that exist. Raw-socket honesty
    belongs to card ②'s preflight probes, never to install verification.
    """
    rc, out = actions.docker_cmd(["docker", "info"],
                                 lambda line: None)
    if rc != 0:
        return {"name": "docker_networks", "status": "missing",
                "detail": "Docker not usable yet: " + (out or "unknown cause"),
                "action": "step 3 proceeds via on-demand group access.",
                "state": "denied", "blocking": False}
    missing = [net for net in preflight.MU3LAB_NETWORKS
               if actions.docker_cmd(
                   ["docker", "network", "inspect", net],
                   lambda line: None)[0] != 0]
    if missing:
        return {"name": "docker_networks", "status": "missing",
                "detail": f"Missing networks: {', '.join(missing)}.",
                "action": "step 3 creates only the missing ones.",
                "state": "missing", "blocking": False}
    return {"name": "docker_networks", "status": "ok",
            "detail": "All shared networks present.",
            "action": "", "state": "ready", "blocking": False}


def fix_networks_router(check: dict, ctx: dict) -> dict:
    """All non-ready networks states fix the same way (docker_cmd adds sg
    when the process lacks the group). Kept as a router so new states get
    an explicit branch instead of falling through."""
    return fix_networks(check, ctx)


def fix_networks(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("docker_networks")
    for net in preflight.MU3LAB_NETWORKS:
        rc, _ = actions.docker_cmd(
            ["docker", "network", "inspect", net], lambda line: None)
        if rc == 0:
            continue
        res = actions.docker_network_create(
            net, log, internal=(net == "mu3lab_backend"))
        if not res["ok"]:
            return _propagate(res)
    return {"ok": True}


def fix_tailscale_pkg(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("tailscale_pkg")
    state = check.get("state", "")
    _update_progress(ctx, "tailscale_pkg", phase="preparing_package",
                     activity="Preparing the signed Tailscale package source.", timeout_seconds=600)
    if state == "absent":
        res = _repair_managed_apt_keys(log)
        if not res["ok"]:
            return _propagate(res)
        distro = _distro_slug()
        codename = _repo_codename() or "noble"
        key_tmp = Path("/tmp/mu3lab-tailscale.gpg")
        res = actions.fetch_url(
            tailscale_key_url(_distro_slug(), codename),
            key_tmp, log)
        if not res["ok"]:
            return _propagate(res)
        res = actions.write_root_bytes("/etc/apt/keyrings/tailscale.gpg",
                                       key_tmp.read_bytes(), log)
        if not res["ok"]:
            return _propagate(res)
        repo = (f"deb [signed-by=/etc/apt/keyrings/tailscale.gpg] "
                f"https://pkgs.tailscale.com/stable/{distro}/ {codename} main\n")
        res = actions.write_root_file("/etc/apt/sources.list.d/tailscale.list", repo, log)
        if not res["ok"]:
            return _propagate(res)
        res = actions.apt_update(log)
        if not res["ok"]:
            return _propagate(res)
        _update_progress(ctx, "tailscale_pkg", phase="installing_package",
                         activity="Downloading and installing Tailscale.", timeout_seconds=600)
        res = actions.apt_install(["tailscale"], log)
        if not res["ok"]:
            return _propagate(res)
    res = actions.systemctl_enable_now("tailscaled", log)
    return _propagate(res)


def fix_caddy(check: dict, ctx: dict) -> dict:
    from ctl import secrets as _secrets
    log = ctx["log_fn"]("caddy")
    projdir = ctx["root"] / "core" / "ingress"
    if not (projdir / "docker-compose.yml").is_file():
        return {"ok": False,
                "error": "core/ingress/docker-compose.yml missing from checkout."}
    runtime_caddy = RuntimePaths().projects / "ingress" / "Caddyfile"
    source_caddy = runtime_caddy if runtime_caddy.is_file() else projdir / "Caddyfile"
    _update_progress(ctx, "caddy", phase="pulling_images",
                     activity="Pulling the reviewed Caddy image and starting private ingress.",
                     timeout_seconds=300)

    def compose_activity(line: str) -> None:
        clean = re.sub(r"\s+", " ", line).strip()
        if clean:
            _update_progress(ctx, "caddy", phase="starting_ingress",
                             activity=clean[:180], timeout_seconds=300)

    ingress_token = _secrets.read_runtime_env(ctx["root"] / ".env").get("MU3LAB_INGRESS_TOKEN", "")
    if not ingress_token:
        return {"ok": False, "error": "The private ingress token is missing; repair the Secret keys file step."}
    rc, out = actions.compose_up(projdir, log,
                                 env={"MU3LAB_CADDYFILE": str(source_caddy),
                                      "MU3LAB_INGRESS_TOKEN": ingress_token},
                                 on_output=compose_activity)
    log(out or f"(exit {rc})")
    if rc != 0:
        return {"ok": False, "error": "docker compose up failed (see log)."}
    # `up -d` returns the instant the container STARTS, not when Caddy
    # listens — without this poll, verify races the boot every time
    # (the exact failure seen live: container Started, port not yet bound).
    import time as _time
    for _ in range(30):
        if _tcp_open(CADDY_PORT) and _caddy_health_status() == 204:
            log(f"Caddy health endpoint answering on :{CADDY_PORT}")
            return {"ok": True}
        _time.sleep(2)
    return {"ok": False,
            "error": f"Caddy container started but its health endpoint on "
                     f":{CADDY_PORT} never answered (see `docker logs ingress-caddy-1`)."}


def _manual_prompt(title: str, body: str, url: str, done: str) -> dict:
    """Return a safe human step; URLs are destinations only, never secrets."""
    return {"kind": "manual_setup", "title": title, "body": body,
            "url": url, "done_label": done,
            "copy_url": url, "check_label": "I completed this — check again"}


def _runtime_marker(step: str, ctx: dict) -> bool:
    from ctl import bootstrap_state
    return bootstrap_state.is_complete(step, inputs=ctx.get("inputs", {}))


def _compose_health(port: int, url: str) -> dict:
    if not _tcp_open(port):
        return {"status": "missing", "state": "down",
                "detail": f"Service is not answering on loopback :{port}."}
    import urllib.request as _url
    try:
        with _url.urlopen(url, timeout=5) as response:
            if 200 <= response.status < 400:
                return {"status": "ok", "state": "ready", "detail": f"Service is healthy on :{port}."}
    except OSError as exc:
        return {"status": "missing", "state": "down", "detail": str(exc)}
    return {"status": "missing", "state": "down", "detail": f"Health check failed on :{port}."}


def _compose_readiness(ctx: dict, step: dict, check: Callable[[], dict],
                      service_name: str, timeout: int) -> dict:
    """Wait for an already-started service to answer its real health check.

    Compose ``up -d`` reports container creation/start, not application
    readiness. A short connection reset during Rocket/Caddy startup is
    expected and must not turn a successful install into a false failure.
    """
    started = time.monotonic()
    while True:
        if ctx.get("stopped", lambda: False)():
            return {"ok": False, "error": f"{service_name} readiness wait was stopped."}
        health = check()
        if health.get("status") == "ok":
            _update_progress(ctx, step["id"], phase="ready",
                             activity=f"{service_name} is healthy and accepting requests.",
                             timeout_seconds=timeout)
            return {"ok": True}
        elapsed = int(time.monotonic() - started)
        _update_progress(ctx, step["id"], phase="waiting_for_health_checks",
                         activity=health.get("detail", f"Waiting for {service_name} to become ready."),
                         timeout_seconds=timeout)
        if elapsed >= timeout:
            return {"ok": False,
                    "error": f"{service_name} did not become ready within "
                             f"{max(1, (timeout + 59) // 60)} minute(s): "
                             + health.get("detail", "health check failed")}
        time.sleep(2)


def _update_progress(ctx: dict, step_id: str, *, phase: str, activity: str,
                     timeout_seconds: int | None = None,
                     containers: list[dict] | None = None) -> None:
    """Publish factual long-step progress when the bootstrap server supports it.

    The pure installer remains usable outside check_server.py, so progress is
    an optional context capability rather than a hidden global dependency.
    """
    publish = ctx.get("progress")
    if callable(publish):
        publish(step_id, {"phase": phase, "activity": activity,
                          "timeout_seconds": timeout_seconds,
                          "containers": containers or []})


def _authentik_containers(ctx: dict) -> list[dict]:
    """Read container state from Docker's Compose label without secrets."""
    rc, output = actions.docker_container_statuses("authentik")
    if rc != 0:
        return []
    containers = []
    for line in output.splitlines():
        name, _, status = line.partition("\t")
        if name:
            containers.append({"name": name, "status": status or "unknown"})
    return containers


def _authentik_readiness(ctx: dict, step: dict,
                         timeout: int = AUTHENTIK_READINESS_TIMEOUT) -> dict:
    """Wait honestly for the first Authentik boot, including migrations.

    Compose only promises that containers have started.  Authentik's own
    health checks plus its HTTP readiness endpoint are the actual completion
    condition.  Connection resets are expected while the server is warming
    up, so this retries through the supplied bounded grace period unless a
    container exits.
    """
    started = time.monotonic()
    previous = ""
    while True:
        if ctx.get("stopped", lambda: False)():
            return {"ok": False, "error": "Authentik readiness wait was stopped."}
        containers = _authentik_containers(ctx)
        summary = "; ".join(f"{item['name']}: {item['status']}" for item in containers)
        if any("Exited" in item["status"] or "Dead" in item["status"] for item in containers):
            return {"ok": False,
                    "error": "Authentik container exited during startup: " + summary}
        health = _authentik_check(ctx)
        elapsed = int(time.monotonic() - started)
        if health.get("status") == "ok":
            _update_progress(ctx, step["id"], phase="ready",
                             activity="Authentik is healthy and accepting requests.",
                             timeout_seconds=timeout,
                             containers=containers)
            return {"ok": True}
        phase = ("Waiting for Authentik's web service" if containers else
                 "Waiting for Authentik containers to appear")
        activity = health.get("detail", "Authentik is still starting.")
        if summary and summary != previous:
            activity = summary + " — " + activity
            previous = summary
        _update_progress(ctx, step["id"], phase=phase, activity=activity,
                         timeout_seconds=timeout,
                         containers=containers)
        if elapsed >= timeout:
            minutes = max(1, (timeout + 59) // 60)
            return {"ok": False,
                    "error": f"Authentik did not become ready within {minutes} minute(s). "
                             + (summary or health.get("detail", "No container status was available."))}
        time.sleep(AUTHENTIK_READINESS_INTERVAL)


def _vaultwarden_check(ctx: dict) -> dict:
    return _compose_health(VAULTWARDEN_PROXY_PORT, "http://127.0.0.1:8081/alive")


def _vaultwarden_account_exists() -> bool:
    """Read only whether Vaultwarden has at least one local user account."""
    import sqlite3
    database = RuntimePaths().data / "vaultwarden" / "db.sqlite3"
    if not database.is_file():
        return False
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True,
                                     timeout=2)
        count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        connection.close()
        return int(count) > 0
    except (OSError, sqlite3.Error, TypeError, ValueError):
        # The manual row remains waiting if the database is still being
        # initialized or its schema is not readable yet. Never infer success
        # from a stale acknowledgement alone.
        return False


def fix_vaultwarden(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("vaultwarden")
    projdir = ctx["root"] / "core" / "vaultwarden"
    _update_progress(ctx, "vaultwarden", phase="pulling_images",
                     activity="Pulling the reviewed Vaultwarden image and starting the password manager.",
                     timeout_seconds=300)

    def compose_activity(line: str) -> None:
        clean = re.sub(r"\s+", " ", line).strip()
        if clean:
            _update_progress(ctx, "vaultwarden", phase="starting_service",
                             activity=clean[:180], timeout_seconds=300)

    rc, out = actions.compose_up(
        projdir, log, env={"MU3LAB_DATA_ROOT": str(RuntimePaths().data)},
        on_output=compose_activity)
    log(out or f"(exit {rc})")
    if rc != 0:
        return {"ok": False, "error": "Vaultwarden could not start (see log)."}
    return {"ok": True}


def check_vaultwarden_setup(ctx: dict) -> dict:
    if _vaultwarden_account_exists():
        return {"status": "ok", "state": "ready",
                "detail": "Vaultwarden account detected in its local database."}
    return {"status": "waiting", "state": "needs_user",
            "detail": "Create the first Vaultwarden account in the local setup page."}


def fix_vaultwarden_setup(check: dict, ctx: dict) -> dict:
    return {"waiting": True, "prompt": _manual_prompt(
        "Create your first Vaultwarden account",
        "Vaultwarden is running locally. Create the first account in its official UI. Mu3Lab never receives or stores the master password. After saving it, return here and check again.",
        f"http://127.0.0.1:{VAULTWARDEN_PROXY_PORT}/#/signup", "I created the account")}


def _tailscale_serve_port(port: str, target: str, log: Callable[[str], None]) -> dict:
    result = privilege.run_privileged(
        ["tailscale", "serve", "--bg", f"--https={port}", target], log, timeout=60)
    if result.get("need_terminal"):
        return {"waiting": True, "prompt": {"kind": "terminal",
                "title": "Tailscale needs one administrator command",
                "body": "Run this command, then press Retry.",
                "terminal_command": result["terminal_command"]}}
    return {"ok": bool(result.get("ok")), "error": "Tailscale Serve could not publish this route."}


def _serve_port_check(port: str) -> dict:
    try:
        result = subprocess.run(["tailscale", "serve", "status"], capture_output=True,
                                text=True, timeout=10)
    except OSError:
        return {"status": "missing", "state": "unshared", "detail": "Tailscale Serve is unavailable."}
    ready = result.returncode == 0 and (f":{port}" in result.stdout or f"https={port}" in result.stdout)
    return {"status": "ok" if ready else "missing", "state": "ready" if ready else "unshared",
            "detail": f"Private HTTPS route {'is' if ready else 'is not'} published on :{port}."}


def fix_vaultwarden_serve(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("vaultwarden_serve")
    domain = vaultwarden_tailnet_domain(_tailscale_dns_name_for_install())
    if not domain:
        return {"ok": False,
                "error": "Tailscale did not provide a valid MagicDNS name for Vaultwarden."}
    projdir = ctx["root"] / "core" / "vaultwarden"
    _update_progress(ctx, "vaultwarden_serve", phase="applying_private_url",
                     activity="Applying Vaultwarden's private HTTPS origin.", timeout_seconds=300)
    rc, out = actions.compose_up(
        projdir, log,
        env={"MU3LAB_DATA_ROOT": str(RuntimePaths().data),
             "VAULTWARDEN_DOMAIN": domain},
        extra_files=[projdir / "docker-compose.tailnet.yml"])
    log(out or f"(exit {rc})")
    if rc != 0:
        return {"ok": False,
                "error": "Vaultwarden could not apply its private HTTPS URL (see log)."}
    return _tailscale_serve_port(VAULTWARDEN_SERVE_PORT,
                                 f"http://127.0.0.1:{VAULTWARDEN_PROXY_PORT}", log)


def fix_authentik_serve(check: dict, ctx: dict) -> dict:
    _update_progress(ctx, "authentik_serve", phase="publishing_private_url",
                     activity="Publishing Authentik on its private Tailscale HTTPS route.",
                     timeout_seconds=120)
    return _tailscale_serve_port(AUTHENTIK_SERVE_PORT,
                                 f"http://127.0.0.1:{AUTHENTIK_PROXY_PORT}",
                                 ctx["log_fn"]("authentik_serve"))


def fix_open_webui_serve(check: dict, ctx: dict) -> dict:
    """Reserve Open WebUI's stable private origin before core reconciliation."""
    return _tailscale_serve_port(OPEN_WEBUI_SERVE_PORT,
                                 f"http://127.0.0.1:{OPEN_WEBUI_PROXY_PORT}",
                                 ctx["log_fn"]("open_webui_serve"))


def _authentik_check(ctx: dict) -> dict:
    return _compose_health(9001, "http://127.0.0.1:9001/-/health/ready/")


def fix_authentik(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("authentik")
    from ctl import secrets as _secrets
    from ctl.authentik_blueprints import write_dashboard_blueprint
    dns_name = _tailscale_dns_name_for_install()
    if not dns_name:
        return {"ok": False,
                "error": "Tailscale did not provide a valid MagicDNS name for Authentik configuration."}
    blueprint = write_dashboard_blueprint(
        RuntimePaths().root, dns_name,
        tailnet_https_origin(dns_name, AUTHENTIK_SERVE_PORT).rstrip("/"),
        tailnet_https_origin(dns_name, DASHBOARD_SERVE_PORT).rstrip("/"))
    log("rendered the Authentik dashboard Blueprint (no credentials)")
    env_file, added = _secrets.ensure_authentik_env(RuntimePaths().root)
    if added:
        log("generated Authentik runtime configuration: " + ", ".join(added))
    generated = _secrets.read_runtime_env(env_file)
    values = {"AUTHENTIK_ENV_FILE": str(env_file),
              "AUTHENTIK_TAG": generated.get("AUTHENTIK_TAG", "2026.5.0"),
              "AUTHENTIK_SECRET_KEY": generated.get("AUTHENTIK_SECRET_KEY", ""),
              "AUTHENTIK_POSTGRESQL__PASSWORD": generated.get("AUTHENTIK_POSTGRESQL__PASSWORD", ""),
              "AUTHENTIK_BLUEPRINTS_DIR": str(blueprint.parent)}
    # Compose needs these values for interpolation. actions.compose_up passes
    # them in the process environment but never includes env values in logs.
    _update_progress(ctx, "authentik", phase="pulling_images",
                     activity="Pulling reviewed Authentik images and creating containers.",
                     timeout_seconds=AUTHENTIK_READINESS_TIMEOUT)

    def compose_activity(line: str) -> None:
        # Progress output is Docker-controlled; retain only a concise current
        # activity line in state while the complete bounded log stays below.
        clean = re.sub(r"\s+", " ", line).strip()
        if clean:
            _update_progress(ctx, "authentik", phase="pulling_images",
                             activity=clean[:180],
                             timeout_seconds=AUTHENTIK_READINESS_TIMEOUT)

    result: dict[str, object] = {}

    def start_and_wait() -> None:
        # Docker Compose's documented --wait is the primary completion
        # signal: it waits for the declared container health checks rather
        # than merely reporting that processes were created.
        result["rc"], result["out"] = actions.compose_up(
            ctx["root"] / "core" / "authentik", log, env=values,
            timeout=1200, wait_timeout=AUTHENTIK_READINESS_TIMEOUT,
            on_output=compose_activity)

    worker = threading.Thread(target=start_and_wait, daemon=True)
    worker.start()
    while worker.is_alive():
        containers = _authentik_containers(ctx)
        if containers:
            _update_progress(ctx, "authentik", phase="waiting_for_health_checks",
                             activity="Docker is waiting for Authentik health checks.",
                             timeout_seconds=AUTHENTIK_READINESS_TIMEOUT,
                             containers=containers)
        time.sleep(2)
    worker.join()
    rc = int(result.get("rc", 1))
    out = str(result.get("out", ""))
    # Streaming compose output was already added to the step log; only the
    # non-streaming error fallback belongs here.
    if not out:
        log(f"(exit {rc})")
    if rc != 0:
        return {"ok": False, "error": "Authentik could not start (see log)."}
    _update_progress(ctx, "authentik", phase="containers_started",
                     activity="Containers started; waiting for Authentik readiness.",
                     timeout_seconds=AUTHENTIK_READINESS_TIMEOUT,
                     containers=_authentik_containers(ctx))
    return {"ok": True}


def tailnet_https_origin(host: str, port: str) -> str:
    """Return a private HTTPS origin, omitting the standard HTTPS port."""
    host = host.rstrip(".")
    return f"https://{host}/" if port == "443" else f"https://{host}:{port}/"


def check_authentik_setup(ctx: dict) -> dict:
    host = _tailscale_dns_name_for_install() or "127.0.0.1"
    setup_pending = _authentik_initial_setup_pending()
    if not setup_pending:
        return {"status": "ok", "state": "ready",
                "detail": "Authentik owner account exists; authenticated access is verified at dashboard handoff."}
    return {"status": "waiting", "state": "needs_user",
            "detail": "Create the first Authentik administrator in the official setup flow.",
            # Authentik 2026.5 routes its root itself to first-run setup. Do
            # not hard-code its version-sensitive internal flow path: a
            # direct legacy flow URL is explicitly denied by this release.
            "setup_url": tailnet_https_origin(host, AUTHENTIK_SERVE_PORT)}


def _authentik_initial_setup_pending() -> bool:
    """Whether Authentik's unauthenticated root still redirects to setup.

    This is a read-only, version-tolerant clue for the human-facing prompt.
    It is not used as proof of an authenticated login; that proof belongs to
    the later protected-dashboard check.
    """
    import http.client
    try:
        connection = http.client.HTTPConnection("127.0.0.1", AUTHENTIK_PROXY_PORT,
                                                timeout=5)
        connection.request("GET", "/")
        response = connection.getresponse()
        location = response.getheader("Location", "")
        connection.close()
        return response.status in {301, 302, 303, 307, 308} and location.startswith("/setup")
    except (OSError, http.client.HTTPException):
        # The setup row already follows a healthy-service row.  If a transient
        # local probe fails, preserve the safe first-run instructions.
        return True


def _tailscale_dns_name_for_install() -> str:
    try:
        proc = subprocess.run(["tailscale", "status", "--json"], capture_output=True,
                              text=True, timeout=5)
        data = json.loads(proc.stdout) if proc.returncode == 0 else {}
        return str(data.get("Self", {}).get("DNSName", "")).rstrip(".")
    except (OSError, ValueError, TypeError):
        return ""


def vaultwarden_tailnet_domain(dns_name: str) -> str:
    """Build Vaultwarden's final private URL, rejecting local/bare values."""
    name = dns_name.rstrip(".")
    if not re.fullmatch(r"[A-Za-z0-9.-]+\.ts\.net", name):
        return ""
    return f"https://{name}:{VAULTWARDEN_SERVE_PORT}"


def fix_authentik_setup(check: dict, ctx: dict) -> dict:
    host = _tailscale_dns_name_for_install() or "127.0.0.1"
    return {"waiting": True, "prompt": _manual_prompt(
        "Create the Authentik administrator",
        "Open Authentik’s official first-run page and create the administrator. "
        "Mu3Lab never receives or stores that password. If Authentik instead "
        "shows a sign-in page and you do not know the administrator password, "
        "you can deliberately reset only the built-in akadmin account below. "
        "After a recovery reset, sign in and change the temporary password "
        "before continuing. Mu3Lab will detect when the owner account exists.",
        tailnet_https_origin(host, AUTHENTIK_SERVE_PORT), "Check owner account again") | {
            "recovery_action": "reset_authentik_admin",
            "recovery_username": "akadmin",
        }}


def reset_authentik_admin_password(ctx: dict) -> dict:
    """Generate and apply a one-time recovery password without persisting it."""
    # Token output contains no whitespace and is sufficiently long for a
    # temporary administrator credential.  It lives only in this call and its
    # HTTPS-local API response; never in job state, logs, or disk.
    temporary_password = "Mu3Lab-" + secrets.token_urlsafe(24)
    result = actions.reset_authentik_admin_password(
        temporary_password, ctx["log_fn"]("authentik_setup"))
    if not result.get("ok"):
        return result
    return {"ok": True, "username": "akadmin",
            "temporary_password": temporary_password}


def check_dashboard_protection(ctx: dict) -> dict:
    host = _tailscale_dns_name_for_install()
    if _runtime_marker("dashboard_protection", ctx):
        target = RuntimePaths().projects / "ingress" / "Caddyfile"
        if not target.is_file():
            return {"status": "missing", "state": "needs_apply",
                    "detail": "Dashboard protection was confirmed; applying the verified Caddy policy."}
        verdict = _dashboard_access_probe(host)
        if verdict["state"] == "ready":
            return verdict
        return {"status": "waiting", "state": "needs_attention", "detail": verdict["detail"]}
    host = host or "127.0.0.1"
    target = RuntimePaths().projects / "ingress" / "Caddyfile"
    if target.is_file():
        verdict = _dashboard_access_probe(host)
        if verdict["state"] == "ready":
            return {"status": "waiting", "state": "needs_user",
                    "detail": "Authentik protection is active; verify one signed-in dashboard request, then confirm.",
                    "setup_url": tailnet_https_origin(host, DASHBOARD_SERVE_PORT)}
        return {"status": "missing", "state": "needs_attention", "detail": verdict["detail"]}
    return {"status": "waiting", "state": "needs_apply",
            "detail": "Mu3Lab will create the Authentik provider, application, and embedded-outpost assignment automatically.",
            "setup_url": tailnet_https_origin(host, DASHBOARD_SERVE_PORT)}


def fix_dashboard_protection(check: dict, ctx: dict) -> dict:
    from ctl import secrets as _secrets
    ingress_token = _secrets.read_runtime_env(ctx["root"] / ".env").get("MU3LAB_INGRESS_TOKEN", "")
    if not ingress_token:
        return {"ok": False, "error": "The private ingress token is missing; repair the Secret keys file step."}
    if _runtime_marker("dashboard_protection", ctx):
        source = ctx["root"] / "core" / "ingress" / "Caddyfile.authenticated"
        target = RuntimePaths().projects / "ingress" / "Caddyfile"
        if target.is_file():
            host = _tailscale_dns_name_for_install() or "127.0.0.1"
            return {"waiting": True, "prompt": _manual_prompt(
                "Verify the protected dashboard",
                "Open the protected dashboard in an anonymous browser window. It must redirect to the private Authentik HTTPS page, not localhost or an HTTP URL. Sign in as a Mu3Lab operator and confirm the dashboard loads, then return here and check again.",
                tailnet_https_origin(host, DASHBOARD_SERVE_PORT), "I verified the protected dashboard")}
        target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        log = ctx["log_fn"]("dashboard_protection")
        rc, out = actions.compose_up(ctx["root"] / "core" / "ingress", log,
                                     env={"MU3LAB_CADDYFILE": str(target),
                                          "MU3LAB_INGRESS_TOKEN": ingress_token})
        log(out or f"(exit {rc})")
        return {"ok": rc == 0, "error": "Caddy could not apply dashboard protection." if rc else ""}
    host = _tailscale_dns_name_for_install()
    if not host:
        return {"ok": False, "error": "Tailscale MagicDNS name is unavailable; cannot create a private Authentik application."}

    # Authentik already watches /blueprints/custom. The authentik step mounts
    # this directory before starting the worker, so do not restart Compose
    # here: a restart would generate another discovery event and can race two
    # otherwise-idempotent Blueprint applies.
    from ctl.authentik_blueprints import write_dashboard_blueprint
    blueprint = write_dashboard_blueprint(
        RuntimePaths().root, host,
        tailnet_https_origin(host, AUTHENTIK_SERVE_PORT).rstrip("/"),
        tailnet_https_origin(host, DASHBOARD_SERVE_PORT).rstrip("/"))
    log = ctx["log_fn"]("dashboard_protection")
    _update_progress(ctx, "dashboard_protection", phase="configuring_identity",
                     activity="Waiting for Authentik to publish the generated provider and embedded outpost.",
                     timeout_seconds=240)

    source = ctx["root"] / "core" / "ingress" / "Caddyfile.authenticated"
    target = RuntimePaths().projects / "ingress" / "Caddyfile"
    target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    _update_progress(ctx, "dashboard_protection", phase="starting_auth_gate",
                     activity="Starting Caddy with the Authentik forward-auth gate.", timeout_seconds=240)
    ingress_result: dict[str, object] = {}

    def ingress_activity(line: str) -> None:
        clean = re.sub(r"\s+", " ", line).strip()
        if clean:
            _update_progress(ctx, "dashboard_protection", phase="starting_auth_gate",
                             activity=clean[:180], timeout_seconds=240)

    ingress_result["rc"], ingress_result["out"] = actions.compose_up(
        ctx["root"] / "core" / "ingress", log,
        env={"MU3LAB_CADDYFILE": str(target),
             "MU3LAB_INGRESS_TOKEN": ingress_token}, on_output=ingress_activity)
    log(str(ingress_result.get("out") or f"(exit {ingress_result['rc']})"))
    if int(ingress_result.get("rc", 1)) != 0:
        return {"ok": False, "error": "Caddy could not apply the Authentik protection policy."}

    # Blueprint discovery is asynchronous. Wait for the actual security
    # property, not a container-start message. Then ask for one browser-based
    # signed-in verification; the user never configures a provider manually.
    started = time.monotonic()
    while time.monotonic() - started < 180:
        verdict = _dashboard_access_probe(host)
        if verdict["state"] == "ready":
            return {"waiting": True, "prompt": _manual_prompt(
                "Verify the protected Mu3Lab dashboard",
                "Mu3Lab created the Authentik provider, application, and embedded outpost automatically. Open the protected dashboard in an anonymous window; it must redirect to the private Authentik HTTPS page, not localhost or an HTTP URL. Sign in as a Mu3Lab operator and confirm the dashboard loads. Mu3Lab never receives the Authentik password.",
                tailnet_https_origin(host, DASHBOARD_SERVE_PORT), "I verified the protected dashboard")}
        _update_progress(ctx, "dashboard_protection", phase="waiting_for_identity",
                         activity="Waiting for Authentik to publish the generated provider to the embedded outpost.",
                         timeout_seconds=180)
        time.sleep(3)
    return {"ok": False,
            "error": "Authentik did not publish the generated dashboard provider within 3 minutes; configuration and logs were preserved."}


def _authentik_redirect_is_expected(location: str | None,
                                    host: str | None = None) -> bool:
    """Accept only a private HTTPS redirect to this install's Authentik."""
    from urllib.parse import urlsplit
    expected_host = (host or _tailscale_dns_name_for_install() or "").rstrip(".").lower()
    if not location or not expected_host:
        return False
    parsed = urlsplit(location)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (parsed.scheme == "https" and parsed.hostname == expected_host and
            port in (None, 443) and bool(parsed.path))


def _dashboard_access_probe(host: str | None = None) -> dict:
    """Require an unauthenticated request to be denied by the auth gate."""
    import urllib.error as _url_error
    import urllib.request as _url_request

    class _NoRedirect(_url_request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            return None

    opener = _url_request.build_opener(_NoRedirect)
    request = _url_request.Request(
        f"http://127.0.0.1:{CADDY_PORT}/",
        headers={
            # Caddy is loopback-bound, but Authentik selects the forward-auth
            # provider by the original tailnet host. A loopback Host header is
            # not a valid security test and correctly returns 404.
            "Host": host or (_tailscale_dns_name_for_install() or "127.0.0.1"),
            "X-Forwarded-Proto": "https",
        },
    )
    def redirect_verdict(status: int, headers) -> dict:  # noqa: ANN001
        location = headers.get("Location")
        if _authentik_redirect_is_expected(location, host):
            return {"status": "ok", "state": "ready",
                    "detail": "Anonymous dashboard access redirects to private Authentik."}
        return {"status": "missing", "state": "needs_attention",
                "detail": (f"Dashboard returned HTTP {status} with an unexpected "
                           "authentication redirect; expected private Authentik "
                           f"at {tailnet_https_origin(host or 'the tailnet host', AUTHENTIK_SERVE_PORT)}.")}

    try:
        with opener.open(request, timeout=5) as response:
            if response.status in (401, 403):
                return {"status": "ok", "state": "ready", "detail": "Anonymous dashboard access is denied by Authentik."}
            if response.status in (302, 303, 307, 308):
                return redirect_verdict(response.status, response.headers)
            return {"status": "missing", "state": "needs_attention",
                    "detail": f"Dashboard returned HTTP {response.status} without authentication."}
    except _url_error.HTTPError as exc:
        if exc.code in (302, 303, 307, 308):
            return redirect_verdict(exc.code, exc.headers)
        if exc.code in (401, 403):
            return {"status": "ok", "state": "ready", "detail": "Anonymous dashboard access is denied by Authentik."}
        return {"status": "missing", "state": "needs_attention",
                "detail": f"Dashboard protection probe returned HTTP {exc.code}."}
    except OSError as exc:
        return {"status": "missing", "state": "needs_attention",
                "detail": f"Dashboard protection probe could not reach Caddy: {exc}"}


def _join_prompt(login_url: str) -> dict:
    """Guided Tailscale connection prompt (shown in the waiting row).

    The user approves the normal Tailscale web login, then resumes this job.
    """
    return {
        "kind": "tailscale_login",
        "title": "Connect this computer to your tailnet",
        "body": ("Tailscale is installed — approve this computer in the normal "
                 "Tailscale web login. This bootstrapper never receives an auth key "
                 "or account password. When approval is complete, press Check again."),
        "signup_url": "https://tailscale.com",
        "login_url": login_url.strip().rstrip(".,);"),
        "terminal_command": "./tools/open_tailscale_login.sh",
    }


def _tailscale_auth_url() -> str:
    """Read a pending login URL from the local daemon without logging status data."""
    try:
        proc = subprocess.run(["tailscale", "status", "--json"],
                              capture_output=True, text=True, timeout=10)
    except OSError:
        return ""
    if proc.returncode != 0:
        return ""
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return ""
    url = payload.get("AuthURL", "")
    return url if isinstance(url, str) else ""


def fix_tailscale_join(check: dict, ctx: dict) -> dict:
    """Join through the installer's existing Polkit worker.

    ``tailscale up`` can keep running while the operator approves the device,
    so waiting for its captured stdout would hide the login URL for the whole
    CLI timeout.  Run the bounded privileged command in one thread while the
    installer polls the local daemon's status in this thread.  The URL is
    opened as soon as the daemon exposes it, and the same command is reused
    when the operator presses ``Check again``.
    """
    log = ctx["log_fn"]("tailscale_join")
    command = ["tailscale", "up", "--hostname=" + TS_HOSTNAME,
               "--timeout=" + TAILSCALE_JOIN_TIMEOUT]
    state = ctx.setdefault("_tailscale_join_state", {})
    thread = state.get("thread")
    if thread is None:
        log("$ tailscale up --hostname=mu3lab --timeout=120s  (waiting for login link)")
        state["result"] = None

        def run_join() -> None:
            state["result"] = privilege.run_privileged(
                command, log, timeout=TAILSCALE_WORKER_TIMEOUT)

        thread = threading.Thread(target=run_join, daemon=True,
                                  name="mu3lab-tailscale-join")
        state["thread"] = thread
        thread.start()
        # Unit-test fakes complete immediately; this also avoids an
        # unnecessary polling interval for a fast already-authorized join.
        thread.join(timeout=0.2)

    import re as _re
    # If the command completed quickly, use its result now.  Otherwise keep
    # polling the daemon while it waits for the browser approval.  This makes
    # the manual prompt appear as soon as AuthURL exists instead of after 120s.
    while thread.is_alive():
        if ctx.get("stopped", lambda: False)():
            return {"ok": False, "error": "Tailscale join was stopped."}
        _update_progress(ctx, "tailscale_join", phase="waiting_for_login",
                         activity="Waiting for Tailscale to provide the approval link.",
                         timeout_seconds=TAILSCALE_WORKER_TIMEOUT)
        time.sleep(1)
        login_url = _tailscale_auth_url()
        if login_url:
            if not state.get("opened"):
                try:
                    webbrowser.open(login_url, new=2)
                    log("opened the Tailscale login page in the default browser")
                except Exception as exc:  # noqa: BLE001
                    log(f"could not open browser automatically: {exc}")
                state["opened"] = True
            return {"waiting": True, "prompt": _join_prompt(login_url)}

    result = state.get("result") or {"ok": False, "output": ""}
    out = result.get("output", "")
    match = _re.search(r"https://login\.tailscale\.com/[A-Za-z0-9/_-]+", out)
    login_url = match.group(0).rstrip(".,);") if match else _tailscale_auth_url()
    if login_url:
        if not state.get("opened"):
            try:
                webbrowser.open(login_url, new=2)
                log("opened the Tailscale login page in the default browser")
            except Exception as exc:  # noqa: BLE001
                log(f"could not open browser automatically: {exc}")
            state["opened"] = True
        return {"waiting": True, "prompt": _join_prompt(login_url)}
    if result.get("need_terminal"):
        return {"waiting": True, "prompt": _join_prompt("")}
    if result.get("ok"):
        return {"ok": True}
    return {"waiting": True, "prompt": _join_prompt("")}


def fix_serve(check: dict, ctx: dict) -> dict:
    """Publish the dashboard on a nonstandard private port.

    Authentik owns tailnet HTTPS :443 because its browser UI requires a
    standard HTTPS origin for API and WebSocket requests.
    """
    log = ctx["log_fn"]("serve")
    # `tailscale serve` changes daemon configuration. Some installations
    # require root unless an operator was configured explicitly, so it must
    # use the same audited elevation boundary as every other host mutation.
    result = privilege.run_privileged(
        ["tailscale", "serve", "--bg", f"--https={DASHBOARD_SERVE_PORT}",
         f"http://127.0.0.1:{CADDY_PORT}"], log, timeout=60)
    if result.get("need_terminal"):
        return {"waiting": True, "prompt": {
            "kind": "terminal",
            "title": "Tailscale needs one administrator command",
            "body": "Run this command, then press Retry.",
            "terminal_command": result["terminal_command"],
        }}
    if not result.get("ok"):
        return {"ok": False, "error": "tailscale serve failed (see log)."}
    return {"ok": True}


def _propagate(res: dict) -> dict:
    """Translate an actions.py result into a fix result (terminal fallback
    becomes a `waiting` prompt with the exact command, never a dead end)."""
    if res.get("need_terminal") or res.get("terminal_command"):
        return {"waiting": True, "prompt": {
            "kind": "terminal",
            "title": "Needs one terminal command",
            "body": ("No system password dialog is available (e.g. SSH "
                     "session). Run this yourself, then press Retry."),
            "terminal_command": res.get("terminal_command", ""),
        }}
    if not res.get("ok"):
        return {"ok": False, "error": "; ".join(res.get("log", []))[-500:]}
    return {"ok": True}


# ---------------------------------------------------------------------------
# Step table + runner. check fns reuse preflight probes; verify == re-check.
# ---------------------------------------------------------------------------


def _docker_check(ctx: dict) -> dict:
    # Delegates to the SHARED probe (preflight.gather_docker) with the
    # installer's exec backend — the two can never disagree about docker
    # state again (that drift caused the denied-vs-down misdiagnosis).
    from ctl import preflight as _pre
    return _pre.gather_docker(exec_fn=actions.privilege._exec)


def _tailscale_pkg_check(ctx: dict) -> dict:
    # Shared probe (same anti-drift contract as docker above).
    from ctl import preflight as _pre
    return _pre.gather_tailscale(exec_fn=actions.privilege._exec)


def _caddy_check(ctx: dict) -> dict:
    if not _tcp_open(CADDY_PORT):
        return {"name": "caddy", "status": "missing",
                "detail": f"Nothing listening on :{CADDY_PORT}.",
                "action": "step 3 starts Caddy.", "state": "down",
                "blocking": False}
    if _caddy_health_status() != 204:
        return {"name": "caddy", "status": "missing",
                "detail": f"Caddy health endpoint on :{CADDY_PORT} is not answering.",
                "action": "step 3 restarts Caddy.", "state": "down",
                "blocking": False}
    return {"name": "caddy", "status": "ok",
            "detail": f"Caddy health endpoint answering on :{CADDY_PORT}.",
            "action": "", "state": "ready", "blocking": False}


def _serve_check(ctx: dict) -> dict:
    rc, out = actions.privilege._exec(["tailscale", "serve", "status"])
    if rc == 0 and f":{DASHBOARD_SERVE_PORT}" in out:
        return {"name": "serve", "status": "ok",
                "detail": f"Tailnet serving the dashboard on :{DASHBOARD_SERVE_PORT}.",
                "action": "", "state": "ready", "blocking": False}
    return {"name": "serve", "status": "missing",
            "detail": "Tailnet sharing not configured yet.",
            "action": "step 3 shares it after Tailscale connects.",
            "state": "unshared", "blocking": False}


STEPS = [
    {"id": "host_base", "label": "System packages",
     "check": lambda ctx: ({"name": "host_base", "status": "missing",
                            "detail": "curl/git/venv tooling.",
                            "action": "step 3 installs them.",
                            "state": "missing", "blocking": False}
                           if any(not _dpkg_present(p) for p in BASE_PACKAGES)
                           else {"name": "host_base", "status": "ok",
                                 "detail": "Base packages present.",
                                 "action": "", "state": "ready", "blocking": False}),
     "fix": fix_host_base},
    {"id": "node", "label": "Node.js",
     "check": lambda ctx: preflight.check_node(
         actions.privilege._exec(["node", "--version"])[1]),
     "fix": fix_node},
    {"id": "venv", "label": "Project workspace folder",
     "check": lambda ctx: _venv_check(ctx["root"]),
     "fix": fix_venv},
    {"id": "pip_deps", "label": "Control-plane packages",
     "check": lambda ctx: _pip_check(ctx["root"]),
     "fix": fix_pip_deps},
    {"id": "dashboard_src", "label": "Dashboard source",
     "check": lambda ctx: _src_check(ctx["root"]),
     "fix": fix_dashboard_src},
    {"id": "dashboard_build", "label": "Dashboard interface",
     "check": lambda ctx: _build_check(ctx["root"]),
     "fix": fix_dashboard_build},
    {"id": "root_env", "label": "Secret keys file",
     "check": lambda ctx: _env_check(ctx["root"]),
     "fix": fix_root_env},
    # The worker service needs this root for its durable SQLite queue.  It is
    # deliberately created before the service is started, not after Docker.
    {"id": "runtime_layout", "label": "Persistent data layout",
     "check": lambda ctx: _runtime_layout_check(RuntimePaths().root),
     "fix": fix_runtime_layout},
    {"id": "service", "label": "Dashboard and workflow services",
     "check": lambda ctx: _service_check(ctx["root"]),
     "fix": fix_service},
    {"id": "docker", "label": "Docker engine",
     "check": _docker_check, "fix": fix_docker,
     # Daemon-level states are owned downstream (networks step); group
     # authorization never blocks because fixes run via sg when needed.
     # Verify passes while any of these hold (the step's own work is done).
     "verify_ok_states": ("ready", "no_group", "stale_login", "no_networks", "no_access")},
    {"id": "docker_networks", "label": "Shared networks",
     "check": _networks_check, "fix": fix_networks_router},
    {"id": "caddy", "label": "Private ingress",
     "check": _caddy_check, "fix": fix_caddy},
    {"id": "vaultwarden", "label": "Vaultwarden password manager",
     "check": _vaultwarden_check, "fix": fix_vaultwarden,
     "readiness": lambda ctx, step: _compose_readiness(
         ctx, step, lambda: _vaultwarden_check(ctx), "Vaultwarden",
         VAULTWARDEN_HTTP_GRACE_TIMEOUT)},
    {"id": "vaultwarden_setup", "label": "Vaultwarden first account",
     "check": check_vaultwarden_setup, "fix": fix_vaultwarden_setup},
    {"id": "tailscale_pkg", "label": "Tailscale app",
     "check": _tailscale_pkg_check, "fix": fix_tailscale_pkg,
     "verify_ok_states": ("ready", "unjoined")},
    {"id": "tailscale_join", "label": "Tailscale connection",
     "check": lambda ctx: (lambda r: {
         "name": "tailscale_join", "status": "ok" if r["state"] == "ready" else "missing",
         "detail": r["detail"], "action": r["action"],
         "state": ("ready" if r["state"] == "ready" else "unjoined"),
         "blocking": False})(_tailscale_pkg_check(ctx)),
     "fix": fix_tailscale_join},
    {"id": "vaultwarden_serve", "label": "Vaultwarden private access",
     "check": lambda ctx: _serve_port_check(VAULTWARDEN_SERVE_PORT),
     "fix": fix_vaultwarden_serve},
    {"id": "authentik", "label": "Authentik identity service",
     "check": _authentik_check, "fix": fix_authentik,
     "readiness": lambda ctx, step: _authentik_readiness(
         ctx, step, timeout=AUTHENTIK_HTTP_GRACE_TIMEOUT)},
    {"id": "authentik_serve", "label": "Authentik private access",
     "check": lambda ctx: _serve_port_check(AUTHENTIK_SERVE_PORT),
     "fix": fix_authentik_serve},
    {"id": "open_webui_serve", "label": "Open WebUI private route",
     "check": lambda ctx: _serve_port_check(OPEN_WEBUI_SERVE_PORT),
     "fix": fix_open_webui_serve},
    {"id": "authentik_setup", "label": "Authentik administrator",
     "check": check_authentik_setup, "fix": fix_authentik_setup},
    # Publish the private dashboard route before the operator tests the
    # Authentik gate; the route is tailnet-only while the gate is configured.
    {"id": "serve", "label": "Mu3Lab private dashboard route",
     "check": _serve_check, "fix": fix_serve},
    {"id": "dashboard_protection", "label": "Protect the Mu3Lab dashboard",
     "check": check_dashboard_protection, "fix": fix_dashboard_protection},
]


def run_job(job: dict, ctx: dict) -> None:
    """Execute steps in order inside the caller's thread. Mutates job dict:
    step statuses pending→installing→verifying→ready|failed|waiting, plus
    job["events"] JSON lines and job["status"]. Stops at first failed/waiting;
    ctx["wait_input"] blocks the join step until the UI supplies a key or a
    continue signal; ctx["stopped"] aborts between steps."""
    job["status"] = "running"
    # ONE elevation for the whole job (single system dialog, not per step).
    # ensure_elevation() is a no-op when sudo is already fresh; any worker it
    # spawns is released in the finally at the end of this function.
    from ctl import privilege as _priv
    mode = _priv.ensure_elevation(
        lambda line: ctx["emit"]({"type": "log", "id": "_install_",
                                  "line": line}))
    ctx["emit"]({"type": "log", "id": "_install_",
                 "line": f"elevation mode: {mode} "
                         f"({'no dialogs expected' if mode != 'terminal' else 'terminal commands will be shown'})"})
    start_at = next((i for i, s in enumerate(job["steps"])
                     if s["status"] not in ("ready",)), 0)
    for step in job["steps"][start_at:]:
        if ctx["stopped"]():
            job["status"] = "cancelled"
            return
        meta = next(m for m in STEPS if m["id"] == step["id"])
        was_waiting = step["status"] == "waiting"
        step["status"] = "installing"
        ctx["emit"]({"type": "step", "id": step["id"], "status": "installing"})
        try:
            check = meta["check"](ctx)
        except Exception as exc:  # noqa: BLE001 (a probe must never kill the job)
            _finish_step(job, ctx, step, {"ok": False, "error": f"check crashed: {exc}"})
            job["status"] = "failed"
            return
        action = fix_for_state(step["id"], check.get("state", ""))
        if action == "skip" or check.get("status") == "ok":
            _finish_step(job, ctx, step, {
                "ok": True,
                "skipped": not was_waiting,
                "detail": check.get("detail", ""),
            })
            continue
        if action == "unknown":
            _finish_step(job, ctx, step, {"ok": False,
                                          "error": f"no fix mapped for state {check.get('state')!r}"})
            job["status"] = "failed"
            return
        try:
            result = meta["fix"](check, ctx)
        except Exception as exc:  # noqa: BLE001 (same reason)
            _finish_step(job, ctx, step, {"ok": False, "error": f"fix crashed: {exc}"})
            job["status"] = "failed"
            return
        if result.get("waiting"):
            # Join steps may resolve via user input: block here until the UI
            # supplies a key/continue, then run the fix ONCE more.
            prompt = result.get("prompt", {})
            if prompt.get("kind") == "tailscale_login" and ctx.get("wait_input"):
                step["status"] = "waiting"
                step["prompt"] = prompt
                ctx["emit"]({"type": "prompt", "id": step["id"], "prompt": prompt})
                ctx["wait_input"](step["id"])  # returns on key, continue, or kill
                if ctx["stopped"]():
                    job["status"] = "cancelled"
                    return
                # Approval can finish while the worker is paused.  The check
                # captured before the browser flow is therefore stale; a
                # fresh probe must decide whether the step is already ready
                # before reusing the join fix and its old worker result.
                try:
                    check = meta["check"](ctx)
                except Exception as exc:  # noqa: BLE001
                    _finish_step(job, ctx, step,
                                 {"ok": False, "error": f"resume check crashed: {exc}"})
                    job["status"] = "failed"
                    return
                if check.get("status") == "ok" or fix_for_state(
                        step["id"], check.get("state", "")) == "skip":
                    result = {"ok": True, "detail": check.get("detail", "")}
                else:
                    result = meta["fix"](check, ctx)  # retry after browser approval
                if result.get("waiting"):
                    _finish_step(job, ctx, step, result)
                    job["status"] = "waiting"
                    return
            else:
                _finish_step(job, ctx, step, result)
                job["status"] = "waiting"
                return
        if not result.get("ok"):
            _finish_step(job, ctx, step, result)
            job["status"] = "failed"
            return
        step["status"] = "verifying"
        ctx["emit"]({"type": "step", "id": step["id"], "status": "verifying"})
        readiness = meta.get("readiness")
        if callable(readiness):
            readiness_result = readiness(ctx, step)
            if not readiness_result.get("ok"):
                _finish_step(job, ctx, step, readiness_result)
                job["status"] = "failed"
                return
        try:
            verify = meta["check"](ctx)
        except Exception as exc:  # noqa: BLE001
            _finish_step(job, ctx, step, {"ok": False, "error": f"verify crashed: {exc}"})
            job["status"] = "failed"
            return
        # Most steps must verify fully green. Steps with downstream-owned
        # states (docker → checkpoint/networks) pass while any listed state
        # holds — their own work is done, the rest has a dedicated row.
        acceptable = meta.get("verify_ok_states", ("ready",))
        if verify.get("status") != "ok" and verify.get("state") not in acceptable:
            _finish_step(job, ctx, step, {"ok": False,
                                          "error": "fix ran but verify still red: "
                                                   + verify.get("detail", "")})
            job["status"] = "failed"
            return
        _finish_step(job, ctx, step, {
            "ok": True,
            "detail": verify.get("detail", ""),
        })
    # The bootstrap and dashboard now share a durable workflow record.  The
    # early bootstrap UI may still be closed or refreshed, but the real
    # dashboard can always explain exactly what remains after hand-off.
    from ctl.provisioning import ProvisioningStore
    provisioning = ProvisioningStore.runtime()
    if provisioning:
        provisioning.update("foundation", "verified",
                            detail="Host foundation, private ingress, and tailnet route are ready.")
        provisioning.update("identity", "verified",
                            detail="Dashboard identity protection was verified through Authentik.")
        provisioning.update("core", "pending",
                            detail="Core platform reconciliation will start automatically.")
        provisioning.update("configuration", "pending",
                            detail="Inference-provider enrollment will be requested only after core services start.")
        provisioning.update("verification", "pending",
                            detail="Mu3Lab will verify routes and application contracts before handoff.")
    # There is no second "install core" decision. The bootstrap already has
    # the user-approved elevation session and has established every required
    # host dependency, so it launches one resumable core job automatically.
    try:
        from ctl.core_setup import start as start_core_setup
        from ctl.jobs import JobStore
        store = JobStore.runtime()
        if store is not None:
            existing = [item for item in store.jobs()
                        if item["service_id"] == "core-suite" and item["state"] in {
                            "queued", "running", "waiting_for_confirmation", "succeeded"}]
            if not existing:
                start_core_setup(store, "bootstrap", ctx["root"])
                ctx["emit"]({"type": "log", "id": "_install_",
                             "line": "started durable core-platform reconciliation"})
    except Exception as exc:  # noqa: BLE001 - dashboard exposes the durable error path
        if provisioning:
            provisioning.update("core", "failed", error="Could not launch core reconciliation.")
        ctx["emit"]({"type": "log", "id": "_install_",
                     "line": f"core-platform launch deferred: {type(exc).__name__}"})
    job["status"] = "ready"
    ctx["emit"]({"type": "summary", "phase": "done", "status": "ready",
                 "detail": "Foundation complete; Mu3Lab is finishing its durable core setup in the private dashboard."})


def _finish_step(job: dict, ctx: dict, step: dict, result: dict) -> None:
    # Write-on-transition (logs excepted): every terminal state sets ALL of
    # status/error/prompt/detail, so a retried step can never display a
    # previous attempt's error under a new success. Logs accumulate only.
    if result.get("waiting"):
        step["status"] = "waiting"
        step["prompt"] = result.get("prompt", {})
        step["error"] = ""
        step["detail"] = ""
        ctx["emit"]({"type": "prompt", "id": step["id"], "prompt": step["prompt"]})
    elif result.get("ok"):
        step["status"] = "ready"
        step["error"] = ""
        step["prompt"] = None
        if result.get("skipped"):
            step["detail"] = "already done — skipped"
        else:
            step["detail"] = result.get("detail", "")
        if step.get("progress"):
            step["progress"]["phase"] = "complete"
        ctx["emit"]({"type": "step", "id": step["id"], "status": "ready",
                     "skipped": bool(result.get("skipped"))})
    else:
        step["status"] = "failed"
        step["error"] = result.get("error", "unknown error")
        step["prompt"] = None
        if step.get("progress"):
            step["progress"]["phase"] = "failed"
        ctx["emit"]({"type": "step", "id": step["id"], "status": "failed",
                     "error": step["error"]})


def collect_privileged_script(job: dict) -> str:
    """Assemble every recorded admin command into ONE copy-paste script.

    Source of truth: step logs' `$ sudo ...` lines (written by
    privilege.run_privileged for every elevation, worker or direct).
    Headless-session fallback: run the output with `sudo bash script.sh`.
    Empty string when nothing privileged has run yet.
    """
    seen: list[str] = []
    for step in job.get("steps", []):
        for line in step.get("log", []):
            text = line.strip()
            if text.startswith("$ sudo ") and text[7:] not in seen:
                seen.append(text[7:])
    if not seen:
        return ""
    from ctl import elevate
    return elevate.build_combined_script(seen)


def new_job() -> dict:
    """Blank job with one entry per STEPS id (UI renders rows from this)."""
    return {"id": "", "status": "queued",
            "steps": [{"id": m["id"], "label": m["label"], "status": "pending",
                       "log": [], "prompt": None, "error": "", "detail": "",
                       "progress": None}
                      for m in STEPS],
            "events": [], "inputs": {}}
