"""Mu3Lab :: ctl/install.py

WHAT: Card-③ execution engine. Ordered remediation steps (host deps → node →
      venv → docker → tailscale → caddy → join → serve), each check-first:
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
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
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
# NodeSource 20.x baseline (accepts newer already-installed Nodes; the fix
# only runs when check_node reports absent/old).
NODESOURCE_KEY_URL = "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key"
NODESOURCE_LIST = ("deb [signed-by=/etc/apt/keyrings/nodesource.gpg] "
                   "https://deb.nodesource.com/node_20.x nodistro main")
DOCKER_KEY_URL = "https://download.docker.com/linux/{slug}/gpg"
DOCKER_PACKAGES = ["docker-ce", "docker-ce-cli", "containerd.io",
                   "docker-buildx-plugin", "docker-compose-plugin"]
def tailscale_key_url(distro: str, codename: str) -> str:
    """GPG key URL for the Tailscale apt repo; pure and unit-testable."""
    family = "debian" if distro == "debian" else "ubuntu"
    return f"https://pkgs.tailscale.com/stable/{family}/{codename}.gpg"
CADDY_PORT = 19460        # minimal Caddyfile serves the dashboard here
SERVE_PORT = "19460"      # `tailscale serve --bg` proxies this local port
TS_HOSTNAME = "mu3lab"
TAILSCALE_JOIN_TIMEOUT = "10s"  # prevents browser approval from blocking a job forever
TAILSCALE_WORKER_TIMEOUT = 20    # bounds the elevated worker if the CLI misbehaves


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
    ("docker_session", "relogin_required"): "wait_for_relogin",
    ("docker_session", "ready"): "skip",
    ("docker_networks", "missing"): "create_networks",
    ("docker_networks", "denied"): "report_denied",
    ("docker_networks", "ready"): "skip",
    ("tailscale_pkg", "absent"): "tailscale_install",
    ("tailscale_pkg", "daemon_down"): "tailscale_start",
    ("tailscale_pkg", "unjoined"): "skip",   # join is the NEXT step's job
    ("tailscale_pkg", "ready"): "skip",
    ("caddy", "down"): "caddy_up",
    ("caddy", "ready"): "skip",
    ("tailscale_join", "unjoined"): "guided_join",
    ("tailscale_join", "ready"): "skip",
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
    res = actions.apt_update(ctx["log_fn"]("host_base"))
    if not res["ok"]:
        return _propagate(res)
    res = actions.apt_install(missing, ctx["log_fn"]("host_base"))
    return _propagate(res)


def fix_node(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("node")
    key_dest = Path("/tmp/mu3lab-nodesource.gpg")
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
    res = actions.apt_update(log)
    if not res["ok"]:
        return _propagate(res)
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
    paths = RuntimePaths()
    required = (paths.data, paths.backups, paths.secrets, paths.runtime, paths.projects)
    if any(not path.is_dir() for path in required):
        return {"name": "runtime_layout", "status": "missing",
                "detail": "Persistent runtime layout is missing.",
                "action": "step 3 creates /srv/mu3lab with safe permissions.",
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


def _service_check(root: Path) -> dict:
    """mu3lab-ctl unit rendered, enabled, and answering /api/health?
    States: no_unit | inactive | unhealthy | ready."""
    import urllib.request as _url
    unit_path = Path.home() / ".config" / "systemd" / "user" / "mu3lab-ctl.service"
    if not unit_path.is_file():
        return {"name": "service", "status": "missing",
                "detail": "Startup entry not installed.",
                "action": "step 3 installs and starts it.",
                "state": "no_unit", "blocking": False}
    try:
        proc = subprocess.run(["systemctl", "--user", "is-active", "mu3lab-ctl"],
                              capture_output=True, text=True, timeout=15)
        active = proc.returncode == 0
    except OSError:
        active = False
    if not active:
        return {"name": "service", "status": "missing",
                "detail": "Startup entry present but not running.",
                "action": "step 3 starts it.", "state": "inactive",
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
    """Render unit → linger → reload → enable → start → poll /api/health."""
    import urllib.request as _url
    log = ctx["log_fn"]("service")
    state = check.get("state", "")
    unit_path = Path.home() / ".config" / "systemd" / "user" / "mu3lab-ctl.service"
    if state == "no_unit":
        template = ctx["root"] / "deploy" / "mu3lab-ctl.service"
        if not template.is_file():
            return {"ok": False, "error": "deploy/mu3lab-ctl.service missing from checkout"}
        rendered = template.read_text(encoding="utf-8").replace(
            "@MU3LAB_ROOT@", str(ctx["root"]))
        unit_path.parent.mkdir(parents=True, exist_ok=True)
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
        rc, out = actions.privilege._exec(
            ["systemctl", "--user", "enable", "mu3lab-ctl.service"])
        log("$ systemctl --user enable mu3lab-ctl.service")
        rc, out = actions.privilege._exec(
            ["systemctl", "--user", "restart", "mu3lab-ctl.service"])
        log("$ systemctl --user restart mu3lab-ctl.service")
        log(out or f"(exit {rc})")
        if rc != 0:
            return {"ok": False, "error": "could not start mu3lab-ctl (see log)"}
        import time as _time
        for _attempt in range(30):
            try:
                with _url.urlopen("http://127.0.0.1:8787/api/health", timeout=2) as resp:
                    if resp.status == 200:
                        log("dashboard answering on :8787")
                        return {"ok": True}
            except OSError:
                pass
            _time.sleep(2)
        return {"ok": False, "error": "service started but :8787 never answered"}
    return {"ok": True, "skipped": True}


def fix_venv(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("venv")
    venv_py = ctx["root"] / ".venv" / "bin" / "python"
    if venv_py.exists():
        return {"ok": True, "skipped": True}
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
    if state == "absent":
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


def _group_live() -> bool:
    """True iff THIS process holds the docker group (os.getgroups: live
    credentials, never the group database). Kept for diagnostics; install
    execution no longer gates on it (sg covers DB members)."""
    import grp as _grp
    try:
        return "docker" in [_grp.getgrgid(gid).gr_name
                            for gid in os.getgroups()]
    except OSError:
        return False


def _docker_session_check(ctx: dict) -> dict:
    """Require a fresh login after Docker group membership changes.

    `sg docker` is intentionally not accepted as proof here: it would let the
    bootstrap continue in a process whose normal operator session is still
    wrong. A fresh desktop/SSH login is the supported boundary.
    """
    if _group_live():
        return {"name": "docker_session", "status": "ok",
                "detail": "Current session has Docker group access.",
                "action": "", "state": "ready", "blocking": False}
    return {"name": "docker_session", "status": "missing",
            "detail": "Docker membership was added, but this login session has not refreshed.",
            "action": "Log out and back in, reopen the bootstrap dashboard, then retry.",
            "state": "relogin_required", "blocking": False}


def fix_docker_session(check: dict, ctx: dict) -> dict:
    """Pause safely for the real OS-session transition; never bypass it."""
    return {"waiting": True, "prompt": {
        "kind": "docker_relogin",
        "title": "Log out and back in to activate Docker access",
        "body": ("Mu3Lab added your account to the Docker group. Log out and back in "
                 "now, reopen the local bootstrap dashboard with ./install.sh, then "
                 "choose Retry. No Docker-backed stack has been started yet."),
        "terminal_command": "./install.sh",
    }}


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
    if state == "absent":
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
        res = actions.apt_install(["tailscale"], log)
        if not res["ok"]:
            return _propagate(res)
    res = actions.systemctl_enable_now("tailscaled", log)
    return _propagate(res)


def fix_caddy(check: dict, ctx: dict) -> dict:
    log = ctx["log_fn"]("caddy")
    projdir = ctx["root"] / "core" / "ingress"
    if not (projdir / "docker-compose.yml").is_file():
        return {"ok": False,
                "error": "core/ingress/docker-compose.yml missing from checkout."}
    rc, out = actions.compose_up(projdir, log)
    log(out or f"(exit {rc})")
    if rc != 0:
        return {"ok": False, "error": "docker compose up failed (see log)."}
    # `up -d` returns the instant the container STARTS, not when Caddy
    # listens — without this poll, verify races the boot every time
    # (the exact failure seen live: container Started, port not yet bound).
    import time as _time
    import urllib.request as _url
    for _ in range(30):
        sock_ok = _tcp_open(CADDY_PORT)
        if sock_ok:
            try:
                with _url.urlopen(f"http://127.0.0.1:{CADDY_PORT}/",
                                  timeout=3) as resp:
                    if resp.status == 200:
                        log(f"Caddy answering on :{CADDY_PORT}")
                        return {"ok": True}
            except OSError:
                pass
        _time.sleep(2)
    return {"ok": False,
            "error": f"Caddy container started but :{CADDY_PORT} never "
                     f"answered (see `docker logs ingress-caddy-1`)."}


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


def fix_tailscale_join(check: dict, ctx: dict) -> dict:
    """Join through the installer's existing Polkit worker.

    The worker already owns the one native password dialog. Its captured
    output is inspected for Tailscale's one-time login URL; the URL is opened
    immediately and is never persisted or sent back as a credential.
    """
    log = ctx["log_fn"]("tailscale_join")
    log("$ tailscale up --hostname=mu3lab --timeout=10s  (waiting up to 10 seconds for login URL)")
    result = privilege.run_privileged(
        ["tailscale", "up", "--hostname=" + TS_HOSTNAME,
         "--timeout=" + TAILSCALE_JOIN_TIMEOUT], log,
        timeout=TAILSCALE_WORKER_TIMEOUT)
    out = result.get("output", "")
    import re as _re
    match = _re.search(r"https://login\.tailscale\.com/[A-Za-z0-9/_-]+", out)
    if match:
        login_url = match.group(0).rstrip(".,);")
        try:
            webbrowser.open(login_url, new=2)
            log("opened the Tailscale login page in the default browser")
        except Exception as exc:  # noqa: BLE001 - link remains in the prompt
            log(f"could not open browser automatically: {exc}")
        return {"waiting": True, "prompt": _join_prompt(login_url)}
    if result.get("need_terminal"):
        return {"waiting": True, "prompt": _join_prompt("")}
    if result.get("ok"):
        return {"ok": True}
    return {"waiting": True, "prompt": _join_prompt("")}


def fix_serve(check: dict, ctx: dict) -> dict:
    """Expose Caddy's port on the tailnet (`tailscale serve --bg <port>`).

    Flag shape per Tailscale docs (serve <local-port>, --bg backgrounds it;
    `serve --help` is authoritative — the verify below catches any drift).
    """
    log = ctx["log_fn"]("serve")
    log(f"$ tailscale serve --bg {SERVE_PORT}  (proxies local :{SERVE_PORT})")
    try:
        proc = subprocess.run(["tailscale", "serve", "--bg", SERVE_PORT],
                              capture_output=True, text=True, timeout=60)
    except OSError as exc:
        return {"ok": False, "error": f"tailscale serve failed: {exc}"}
    log((proc.stdout + proc.stderr).strip() or "(serving)")
    if proc.returncode != 0:
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
    import urllib.request as _url
    try:
        with _url.urlopen(f"http://127.0.0.1:{CADDY_PORT}/", timeout=5) as resp:
            code = resp.status
    except OSError:
        code = 0
    if code == 0:
        return {"name": "caddy", "status": "missing",
                "detail": f"Port :{CADDY_PORT} busy but not answering HTTP.",
                "action": "step 3 restarts Caddy.", "state": "down",
                "blocking": False}
    return {"name": "caddy", "status": "ok",
            "detail": f"Caddy answering on :{CADDY_PORT}.",
            "action": "", "state": "ready", "blocking": False}


def _serve_check(ctx: dict) -> dict:
    rc, out = actions.privilege._exec(["tailscale", "serve", "status"])
    if rc == 0 and SERVE_PORT in out:
        return {"name": "serve", "status": "ok",
                "detail": f"Tailnet serving local :{SERVE_PORT}.",
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
    {"id": "service", "label": "Dashboard service",
     "check": lambda ctx: _service_check(ctx["root"]),
     "fix": fix_service},
    {"id": "docker", "label": "Docker engine",
     "check": _docker_check, "fix": fix_docker,
     # Daemon-level states are owned downstream (networks step); group
     # authorization never blocks because fixes run via sg when needed.
     # Verify passes while any of these hold (the step's own work is done).
     "verify_ok_states": ("ready", "no_group", "stale_login", "no_networks", "no_access")},
    {"id": "docker_session", "label": "Docker login session",
     "check": _docker_session_check, "fix": fix_docker_session},
    {"id": "tailscale_pkg", "label": "Tailscale app",
     "check": _tailscale_pkg_check, "fix": fix_tailscale_pkg,
     # Installed + daemon running is this step's whole job; connecting is the
     # tailscale_join step's job (same verify-tolerance pattern as docker).
     "verify_ok_states": ("ready", "unjoined")},
    {"id": "tailscale_join", "label": "Tailscale connection",
     "check": lambda ctx: (lambda r: {
         "name": "tailscale_join", "status": "ok" if r["state"] == "ready" else "missing",
         "detail": r["detail"], "action": r["action"],
         "state": ("ready" if r["state"] == "ready" else "unjoined"),
         "blocking": False})(_tailscale_pkg_check(ctx)),
     "fix": fix_tailscale_join},
    {"id": "runtime_layout", "label": "Persistent data layout",
     "check": lambda ctx: _runtime_layout_check(ctx["root"]),
     "fix": fix_runtime_layout},
    {"id": "docker_networks", "label": "Shared networks",
     "check": _networks_check, "fix": fix_networks_router},
    {"id": "caddy", "label": "Caddy",
     "check": _caddy_check, "fix": fix_caddy},
    {"id": "serve", "label": "Phone access (sharing)",
     "check": _serve_check, "fix": fix_serve},
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
            _finish_step(job, ctx, step, {"ok": True, "skipped": True})
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
        _finish_step(job, ctx, step, {"ok": True})
    job["status"] = "ready"
    ctx["emit"]({"type": "summary", "phase": "done", "status": "ready",
                 "detail": "Bootstrap checks completed; continue in the private dashboard."})


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
            step["detail"] = ""
        ctx["emit"]({"type": "step", "id": step["id"], "status": "ready",
                     "skipped": bool(result.get("skipped"))})
    else:
        step["status"] = "failed"
        step["error"] = result.get("error", "unknown error")
        step["prompt"] = None
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
                       "log": [], "prompt": None, "error": "", "detail": ""}
                      for m in STEPS],
            "events": [], "inputs": {}}
