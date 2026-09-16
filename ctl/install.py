"""Mu3Lab :: ctl/install.py

WHAT: Card-③ execution engine. Ordered remediation steps (host deps → node →
      venv → docker → tailscale → caddy → join → serve), each check-first:
      `ready` states are SKIPPED, every other state maps to exactly one fix.
      Long user actions (docker-group relogin, tailscale join) surface as
      `waiting` prompts instead of failures; resume continues from them.
WHY:  Installing over healthy components is structurally impossible here:
      no step runs without its check reporting a gap first. Steps never
      accept secrets as arguments — the tailscale auth key arrives via the
      job's memory-only `inputs` dict (server-held, never logged).
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
from collections.abc import Callable
from pathlib import Path

from ctl import actions, preflight

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Constants: versions, package lists, ports. Reasons inline.
# ---------------------------------------------------------------------------
BASE_PACKAGES = ["curl", "git", "ca-certificates", "gnupg",
                 "python3", "python3-pip", "python3-venv"]
# NodeSource 20.x baseline (accepts newer already-installed Nodes; the fix
# only runs when check_node reports absent/old).
NODESOURCE_KEY_URL = "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key"
NODESOURCE_LIST = ("deb [signed-by=/etc/apt/keyrings/nodesource.gpg] "
                   "https://deb.nodesource.com/node_20.x nodistro main")
DOCKER_KEY_URL = "https://download.docker.com/linux/{slug}/gpg"
DOCKER_PACKAGES = ["docker-ce", "docker-ce-cli", "containerd.io",
                   "docker-buildx-plugin", "docker-compose-plugin"]
TAILSCALE_KEY_URL = "https://pkgs.tailscale.com/stable/{distro}.{codename}.gpg"
CADDY_PORT = 19460        # minimal Caddyfile serves the dashboard here
SERVE_PORT = "19460"      # `tailscale serve --bg` proxies this local port
TS_HOSTNAME = "mu3lab"


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
    ("docker", "absent"): "docker_install",
    ("docker", "daemon_down"): "docker_start",
    ("docker", "unverified"): "docker_start",
    ("docker", "old_engine"): "docker_upgrade",
    ("docker", "no_compose"): "docker_compose_plugin",
    ("docker", "no_group"): "docker_group",
    ("docker", "no_networks"): "docker_networks",
    ("docker", "ready"): "skip",
    ("tailscale_pkg", "absent"): "tailscale_install",
    ("tailscale_pkg", "daemon_down"): "tailscale_start",
    ("tailscale_pkg", "unjoined"): "skip",   # join is the NEXT step's job
    ("tailscale_pkg", "ready"): "skip",
    ("caddy", "down"): "caddy_up",
    ("caddy", "ready"): "skip",
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
                 "unverified", "no_group", "no_networks"):
        # Group checkpoint (fresh installs AND pre-existing gaps land here):
        # ensure membership, then require a LIVE group before continuing.
        user = getpass.getuser()
        res = actions.usermod_add_group(user, "docker", log)
        if not res["ok"]:
            return _propagate(res)
        import grp as _grp
        try:
            live = [ _grp.getgrgid(gid).gr_name
                     for gid in os.getgrouplist(user, os.getgid()) ]
        except OSError:
            live = []
        if "docker" not in live:
            return {"waiting": True, "prompt": {
                "kind": "relogin",
                "title": "One logout needed",
                "body": ("Docker installed your user into the `docker` group, "
                         "but this login doesn't have it yet. Run `newgrp docker` "
                         "in a terminal (or log out and back in), then press Retry."),
                "commands": ["newgrp docker"],
            }}
    if state in ("absent", "old_engine", "no_compose", "daemon_down",
                 "unverified", "no_group", "no_networks"):
        nets = preflight.MU3LAB_NETWORKS
        have = [net for net in nets
                if actions.privilege._exec(["docker", "network", "inspect", net])[0] == 0]
        for net in nets:
            if net in have:
                continue
            internal = (net == "mu3lab_backend")
            res = actions.docker_network_create(net, log, internal=internal)
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
            TAILSCALE_KEY_URL.format(
                distro="debian" if distro == "debian" else "ubuntu",
                codename=codename),
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
                "error": "core/ingress/docker-compose.yml missing (Phase 9 config)."}
    log("$ docker compose up -d  (in core/ingress)")
    try:
        proc = subprocess.run(["docker", "compose", "up", "-d"],
                              capture_output=True, text=True, timeout=300,
                              cwd=str(projdir))
    except OSError as exc:
        return {"ok": False, "error": f"compose failed: {exc}"}
    log((proc.stdout + proc.stderr).strip() or "(up)")
    if proc.returncode != 0:
        return {"ok": False, "error": "docker compose up failed (see log)."}
    return {"ok": True}


def fix_tailscale_join(check: dict, ctx: dict) -> dict:
    """Guided join. Automatic ONLY with a user-supplied auth key (memory-only,
    from job inputs); otherwise run `tailscale up` to surface its login URL
    and wait for the user to approve it in a browser."""
    log = ctx["log_fn"]("tailscale_join")
    key = (ctx.get("inputs") or {}).get("tailscale_authkey", "")
    if key:
        log("$ tailscale up --authkey=*** --hostname=mu3lab (key redacted)")
        try:
            proc = subprocess.run(
                ["sudo", "-n", "tailscale", "up", f"--authkey={key}",
                 "--hostname=" + TS_HOSTNAME],
                capture_output=True, text=True, timeout=120)
        except OSError as exc:
            return {"ok": False, "error": f"tailscale up failed: {exc}"}
        key = ""  # wipe local reference immediately after use
        if proc.returncode != 0:
            return {"ok": False,
                    "error": "tailscale up rejected the key: " + (proc.stdout + proc.stderr).strip()[:300]}
        return {"ok": True}
    # No key: interactive login-URL flow. `tailscale up` prints a URL and
    # blocks; run it briefly to capture the URL, then wait for approval.
    log("$ tailscale up --hostname=mu3lab  (capturing login URL)")
    try:
        proc = subprocess.run(["sudo", "-n", "tailscale", "up",
                               "--hostname=" + TS_HOSTNAME],
                              capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or b"").decode(errors="replace")
               + (exc.stderr or b"").decode(errors="replace"))
        import re as _re
        match = _re.search(r"https?://\S+", out)
        return {"waiting": True, "prompt": {
            "kind": "tailscale_login",
            "title": "Approve Tailscale login",
            "body": ("Open this URL on any device where you're logged into "
                     "Tailscale, approve the machine, then press Check again."),
            "login_url": match.group(0) if match else "",
            "terminal_command": "sudo tailscale up --hostname=mu3lab",
        }}
    except OSError as exc:
        return {"ok": False, "error": f"tailscale up failed: {exc}"}
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode == 0:
        return {"ok": True}  # already logged in, nothing to approve
    import re as _re
    match = _re.search(r"https?://\S+", out)
    return {"waiting": True, "prompt": {
        "kind": "tailscale_login",
        "title": "Approve Tailscale login",
        "body": ("Open this URL on any device where you're logged into "
                 "Tailscale, approve the machine, then press Check again."),
        "login_url": match.group(0) if match else "",
        "terminal_command": "sudo tailscale up --hostname=mu3lab",
    }}


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
    rc, _ = actions.privilege._exec(["docker", "info"])
    eng_rc, eng = actions.privilege._exec(
        ["docker", "version", "--format", "{{.Server.Version}}"])
    comp = actions.privilege._exec(["docker", "compose", "version"])[0] == 0
    user = getpass.getuser()
    try:
        import grp as _grp
        groups = [_grp.getgrgid(g).gr_name
                  for g in os.getgrouplist(user, os.getgid())]
    except OSError:
        groups = []
    nets = [n for n in preflight.MU3LAB_NETWORKS
            if actions.privilege._exec(["docker", "network", "inspect", n])[0] == 0]
    import shutil as _sh
    return preflight.check_docker(rc, groups, nets,
                                  engine_version=eng if eng_rc == 0 else "",
                                  compose_present=comp,
                                  binary_present=_sh.which("docker") is not None)


def _tailscale_pkg_check(ctx: dict) -> dict:
    import shutil as _sh
    binary = _sh.which("tailscale") is not None
    active = actions.privilege._exec(
        ["systemctl", "is-active", "tailscaled"])[0] == 0
    joined = actions.privilege._exec(["tailscale", "status"])[0] == 0 if binary else False
    return preflight.check_tailscale(binary, active, joined)


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
    {"id": "venv", "label": "Project workspace",
     "check": lambda ctx: preflight.check_bundle(root=ctx["root"]),
     "fix": fix_venv},
    {"id": "docker", "label": "Docker",
     "check": _docker_check, "fix": fix_docker},
    {"id": "tailscale_pkg", "label": "Tailscale app",
     "check": _tailscale_pkg_check, "fix": fix_tailscale_pkg},
    {"id": "caddy", "label": "Caddy",
     "check": _caddy_check, "fix": fix_caddy},
    {"id": "tailscale_join", "label": "Tailscale connection",
     "check": lambda ctx: (lambda r: {
         "name": "tailscale_join", "status": "ok" if r["state"] == "ready" else "missing",
         "detail": r["detail"], "action": r["action"],
         "state": ("ready" if r["state"] == "ready" else "unjoined"),
         "blocking": False})(_tailscale_pkg_check(ctx)),
     "fix": fix_tailscale_join},
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
                result = meta["fix"](check, ctx)  # retry with key or re-poll
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
        if verify.get("status") != "ok":
            _finish_step(job, ctx, step, {"ok": False,
                                          "error": "fix ran but verify still red: "
                                                   + verify.get("detail", "")})
            job["status"] = "failed"
            return
        _finish_step(job, ctx, step, {"ok": True})
    job["status"] = "ready"
    ctx["emit"]({"type": "summary", "phase": "done", "status": "ready"})


def _finish_step(job: dict, ctx: dict, step: dict, result: dict) -> None:
    if result.get("waiting"):
        step["status"] = "waiting"
        step["prompt"] = result.get("prompt", {})
        ctx["emit"]({"type": "prompt", "id": step["id"], "prompt": step["prompt"]})
    elif result.get("ok"):
        step["status"] = "ready"
        if result.get("skipped"):
            step["detail"] = "already done — skipped"
        ctx["emit"]({"type": "step", "id": step["id"], "status": "ready",
                     "skipped": bool(result.get("skipped"))})
    else:
        step["status"] = "failed"
        step["error"] = result.get("error", "unknown error")
        ctx["emit"]({"type": "step", "id": step["id"], "status": "failed",
                     "error": step["error"]})


def new_job() -> dict:
    """Blank job with one entry per STEPS id (UI renders rows from this)."""
    return {"id": "", "status": "queued",
            "steps": [{"id": m["id"], "label": m["label"], "status": "pending",
                       "log": [], "prompt": None, "error": "", "detail": ""}
                      for m in STEPS],
            "events": [], "inputs": {}}
