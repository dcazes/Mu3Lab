"""Mu3Lab :: ctl/preflight.py

WHAT: Read-only host checks for Step 0 (bootstrap) and the dashboard Setup
      page. Every `check_*()` inspects the machine and reports; none of them
      installs, modifies, or prompts for anything.
WHY:  The dashboard's first card ("is this host ready?") and install.sh's
      guard clauses both need the same answers. One module, two callers, so
      the shell script and the UI can never disagree about requirements.
RUN:  `python3 -m ctl.preflight` from the repo root prints a human table.
      (Needs no .venv, no root, no network — stdlib only.)
DEBUG: Each check returns a plain dict {name, status, detail, action} where
      status is one of "ok" | "missing" | "fail". A failing gate tells you
      the exact `action` string to fix it. `run_all()` aggregates to
      {ok, checks:[...]} for the GET /api/preflight endpoint (Phase 8).
"""

from __future__ import annotations

import grp
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants: every magic value lives here with its reason.
# ---------------------------------------------------------------------------

# Minimum supported distros. Mint is accepted via ID_LIKE=ubuntu + UBUNTU_CODENAME.
MIN_DEBIAN_MAJOR = 12      # install.sh dies below Debian 12 (docker repo needs it)
MIN_UBUNTU_MAJOR = 22      # install.sh dies below Ubuntu 22.04 (same reason)
MIN_PYTHON = (3, 10)       # `match` syntax + new typing used across ctl/
MIN_NODE_MAJOR = 20        # dashboard builds from source (Vite needs Node 18+;
                         # install.sh ensures AT LEAST v20; newer (22+) is accepted)
MIN_DOCKER_MAJOR = 24      # compose-v2 plugin era; step 3 upgrades older engines

# Checks the installer CANNOT fix. Anything else is step 3's work list and
# must never report "fail" — only "missing" (not ready, installer provides)
# or "ok". run_all() stamps each check with blocking True/False from this set;
# the single gating rule is gate_passed() (no FAIL among BLOCKING).
BLOCKING = frozenset({"os", "arch", "python", "ports"})

# Only these ports are probed. Ports for deferred Step-2 apps (e.g. 4000
# LiteLLM) are deliberately NOT checked — a missing future port is not a
# Step-0/1 failure. See BUILD_ORDER Phase 2 gate discussion.
CHECK_PORTS = (8787, 19460, 9001, 8081)

# Docker networks Step 1a must create before any compose project starts.
MU3LAB_NETWORKS = ("mu3lab_frontend", "mu3lab_backend", "mu3lab_mcp")

# Repo root = parent of this file's directory (ctl/ -> Mu3Lab/).
ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Small helpers (private). No subprocess here except via _run().
# ---------------------------------------------------------------------------

def _result(name: str, status: str, detail: str, action: str = "",
            state: str = "") -> dict:
    """Build one check dict. Status is "ok" | "missing" | "fail".

    `state` is the machine-readable dispatch key card ③ switches on
    (e.g. docker "daemon_down" → start it; "absent" → install it).
    Convention: dispatchable checks use specific states; gate-only checks
    (os/arch/python/ports) use "ready"/"blocked" mirroring status.
    """
    return {"name": name, "status": status, "detail": detail,
            "action": action, "state": state or status}


def _run(argv: list[str], timeout: int = 10) -> tuple[int, str]:
    """Run a probe command, swallowing all errors into a return code.

    Never raises: a missing binary is a normal "missing" answer, not an
    exception. stdout+stderr are merged because error text is diagnostic.
    """
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except FileNotFoundError:
        return 127, f"{argv[0]}: command not found"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: timed out after {timeout}s"
    except OSError as exc:  # e.g. permission denied on the binary
        return 126, f"{argv[0]}: {exc}"


def _parse_os_release(text: str) -> dict[str, str]:
    """Parse /etc/os-release content into a dict (handles quoted values)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


# ---------------------------------------------------------------------------
# Checks (public). Each takes only injectable inputs so tests never touch
# the real host: file text / version tuples are passed in, and live values
# are read at the bottom in run_all()/__main__.
# ---------------------------------------------------------------------------

def check_os(release_text: str, kernel_release: str = "") -> dict:
    """Decide whether this distro family is supported (minimums, not pins).

    Accepts Debian>=12, Ubuntu>=22.04, and ANY derivative declaring ID_LIKE
    ubuntu/debian with a usable codename (needed later for apt repos) — no
    distro is special-cased by name. WSL is detected via the kernel release
    and reported ok-with-note (systemd + polkit caveats), never silently.
    Both inputs are injected so tests feed fixtures, never the live host.
    """
    info = _parse_os_release(release_text)
    distro_id = info.get("ID", "")
    like = info.get("ID_LIKE", "")
    version_id = info.get("VERSION_ID", "")
    wsl = "microsoft" in (kernel_release or "").lower()
    # WSL note: shown on success rows (action stays empty — this warns,
    # never gates). systemd units, pkexec dialogs and Docker all behave
    # differently under WSL; the user must know before installing.
    wsl_note = (" WSL detected: set `systemd=true` in /etc/wsl.conf then "
                "`wsl --shutdown`; no native polkit dialog (terminal "
                "fallback); use Docker Engine or Docker Desktop.") if wsl else ""
    try:
        major = int(version_id.split(".")[0])
    except (ValueError, IndexError):
        return _result("os", "fail",
                       f"Unparseable VERSION_ID {version_id!r} (ID={distro_id!r})." + wsl_note,
                       "Use Debian 12+ or Ubuntu 22.04+.")
    if distro_id == "debian":
        if major >= MIN_DEBIAN_MAJOR:
            return _result("os", "ok", f"Debian {version_id} supported." + wsl_note)
        return _result("os", "fail", f"Debian {version_id} too old." + wsl_note,
                       "Upgrade to Debian 12+.")
    if distro_id == "ubuntu":
        if major >= MIN_UBUNTU_MAJOR:
            return _result("os", "ok", f"Ubuntu {version_id} supported." + wsl_note)
        return _result("os", "fail", f"Ubuntu {version_id} too old." + wsl_note,
                       "Upgrade to Ubuntu 22.04+.")
    # Generic derivative path (Mint, Pop!_OS, ...): trust ID_LIKE, need a
    # codename for the Docker/NodeSource apt repos that step 3 will add.
    if "ubuntu" in like or "debian" in like:
        codename = info.get("UBUNTU_CODENAME") or info.get("VERSION_CODENAME", "")
        if major >= MIN_UBUNTU_MAJOR and codename:
            return _result("os", "ok",
                           f"{info.get('PRETTY_NAME', distro_id)} accepted as a "
                           f"Debian/Ubuntu derivative (codename {codename})." + wsl_note)
        return _result("os", "fail",
                       f"{info.get('PRETTY_NAME', distro_id)}: version {version_id} "
                       f"or missing codename." + wsl_note,
                       "Use a derivative of Ubuntu 22.04+ / Debian 12+.")
    return _result("os", "fail", f"Unsupported distro ID={distro_id!r}." + wsl_note,
                   "Use Debian 12+, Ubuntu 22.04+, or a compatible derivative.")


def check_arch(machine: str) -> dict:
    """Accept x86-64 only; ARM support is deliberately deferred for v1."""
    if machine == "x86_64":
        return _result("arch", "ok", f"CPU arch {machine} supported.")
    if machine in ("aarch64", "arm64"):
        return _result("arch", "fail", f"CPU arch {machine} is not in the v1 support matrix.",
                       "Use an x86-64 host; ARM support is planned for a later release.")
    return _result("arch", "fail", f"CPU arch {machine!r} unsupported.",
                   "Mu3Lab v1 supports x86-64 hosts.")


def check_gpu(nvidia_present: bool, amd_present: bool) -> dict:
    """Report a selectable CPU/NVIDIA/AMD profile; detection never installs drivers."""
    if nvidia_present:
        return _result("gpu", "ok", "NVIDIA GPU detected; confirm the NVIDIA profile before install.",
                       state="nvidia")
    if amd_present:
        return _result("gpu", "ok", "AMD GPU detected; confirm the AMD profile before install.",
                       state="amd")
    return _result("gpu", "ok", "No supported GPU detected; CPU profile will be offered.",
                   state="cpu")


def check_python(version: tuple[int, ...]) -> dict:
    """Require Python >= 3.10 (takes sys.version_info so tests can inject)."""
    if tuple(version[:2]) >= MIN_PYTHON:
        return _result("python", "ok",
                       f"Python {version[0]}.{version[1]} meets "
                       f">={MIN_PYTHON[0]}.{MIN_PYTHON[1]}.")
    return _result("python", "fail",
                   f"Python {version[0]}.{version[1]} too old.",
                   "Install Python 3.10+ (do NOT remove system python3).")


def check_node(node_version_output: str) -> dict:
    """Require Node >= 20 (dashboard builds from source in install.sh step 9).

    install.sh installs 20.x as the baseline, but a newer system Node (22+)
    is accepted — downgrading a working Node would be pure churn.
    Takes the raw text of `node --version` (e.g. "v22.3.0") or "" when the
    binary is absent, so tests never need Node installed.
    """
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", node_version_output or "")
    if not match:
        return _result("node", "missing", "Node.js not found.",
                       "step 3 installs Node 20+ via NodeSource.", state="absent")
    major = int(match.group(1))
    if major >= MIN_NODE_MAJOR:
        return _result("node", "ok", f"Node {match.group(0)} present (>= v20).",
                       state="ready")
    return _result("node", "missing",
                   f"Node {match.group(0)} below minimum v20.",
                   "step 3 upgrades it via the NodeSource repo.", state="old")


def check_privilege(sudo_fresh: bool, graphical_session: bool) -> dict:
    """Report HOW a future privileged step would prompt (never prompts here).

    - sudo timestamp fresh  -> steps run silently, no dialog.
    - graphical session     -> pkexec pops the native system dialog.
    - neither               -> dashboard shows copy-paste terminal commands.
    Both booleans are injected so tests (and --dry-run) don't probe the TTY.
    """
    if sudo_fresh:
        return _result("privilege", "ok",
                       "sudo timestamp fresh: privileged steps run without prompting.",
                       state="fresh_sudo")
    if graphical_session:
        return _result("privilege", "ok",
                       "No fresh sudo, but a graphical session exists: pkexec "
                       "will show the native system password dialog.",
                       state="polkit")
    return _result("privilege", "missing",
                   "No fresh sudo and no graphical session detected.",
                   "Privileged steps will show terminal commands to run by hand.",
                   state="terminal")


def check_docker(docker_info_rc: int, group_names: list[str],
                 networks_present: list[str], engine_version: str = "",
                 compose_present: bool = False,
                 binary_present: bool = True,
                 permission_denied: bool = False,
                 daemon_active: bool = False,
                 db_has_group: bool = True) -> dict:
    """Report Docker readiness as a dispatchable STATE (never "fail").

    rc!=0 is AMBIGUOUS (dead daemon vs unauthorized user), so callers pass
    the disambiguators: `permission_denied` (stderr said so) and
    `daemon_active` (systemctl, no socket needed). The group verdict splits
    three ways via live credentials vs group database: no_group (DB lacks
    you → installer adds), stale_login (DB has you, this process doesn't
    → restart the checker, not another logout), ready. States: absent |
    daemon_down | no_access | unverified | old_engine | no_compose |
    no_group | stale_login | no_networks | ready. All inputs injected; this
    function only judges.
    """
    if not binary_present:
        return _result("docker", "missing", "Docker is not installed.",
                       "step 3 installs Docker ≥24 + compose plugin.",
                       state="absent")
    if docker_info_rc != 0:
        if permission_denied and daemon_active:
            if db_has_group:
                return _result("docker", "missing",
                               "Docker runs and you're in its group — but this "
                               "checker started before your fresh login.",
                               "Restart ./check.sh (Ctrl-C, run again) — no new "
                               "login needed.",
                               state="stale_login")
            return _result("docker", "missing",
                           "Docker is installed and running — this login just "
                           "isn't authorized to use it yet.",
                           "step 3 adds you to the group, then pauses for a "
                           "fresh login.",
                           state="no_access")
        return _result("docker", "missing",
                       "Docker is installed but the daemon is not running.",
                       "step 3 enables and starts it (no reinstall).",
                       state="daemon_down")
    match = re.search(r"(\d+)\.(\d+)", engine_version or "")
    if not match:
        return _result("docker", "missing", "Daemon ok, engine version unknown.",
                       "step 3 verifies and upgrades if needed.",
                       state="unverified")
    if int(match.group(1)) < MIN_DOCKER_MAJOR:
        return _result("docker", "missing",
                       f"Engine v{match.group(0)} below minimum v{MIN_DOCKER_MAJOR}.",
                       "step 3 upgrades the engine (compose v2 era required).",
                       state="old_engine")
    if not compose_present:
        return _result("docker", "missing",
                       f"Engine v{match.group(0)} ok, compose plugin absent.",
                       "step 3 installs docker-compose-plugin.",
                       state="no_compose")
    # NOTE: group membership must be LIVE in this process. getgrouplist()
    # reads the group DATABASE (/etc/group) and would parrot back a membership
    # added minutes ago; only getgroups() reports this process's credentials.
    if "docker" not in group_names:
        if db_has_group:
            # The USER is a member but THIS process isn't: the checker started
            # before the fresh login. Restarting the checker (not logging out
            # again) is the fix — name it exactly.
            return _result("docker", "missing",
                           "You're in the docker group, but this checker "
                           "started before your fresh login.",
                           "Restart ./check.sh (Ctrl-C, run again) — no new "
                           "login needed.",
                           state="stale_login")
        return _result("docker", "missing",
                       "Installed and running; this login just needs the "
                       "`docker` group to take effect.",
                       "step 3 pauses at the restart checkpoint: fresh login, "
                       "then Resume.",
                       state="no_group")
    missing = [net for net in MU3LAB_NETWORKS if net not in networks_present]
    if missing:
        return _result("docker", "missing",
                       f"Engine ok, group ok, missing networks: {', '.join(missing)}.",
                       "step 3 creates only the missing networks.",
                       state="no_networks")
    return _result("docker", "ok",
                   f"Engine v{match.group(0)} + compose, group live, networks "
                   f"present ({', '.join(MU3LAB_NETWORKS)}).",
                   state="ready")


def check_tailscale(binary_present: bool, daemon_active: bool,
                    joined: bool) -> dict:
    """Report Tailscale state. Never blocks: install, start and join (via the
    card-③ auth key) are all step 3's job. Inputs injected."""
    if not binary_present:
        return _result("tailscale", "missing", "Tailscale is not installed.",
                       "step 3 installs it (apt repo).", state="absent")
    if not daemon_active:
        return _result("tailscale", "missing",
                       "Installed, but the background service is not running.",
                       "step 3 enables and starts it (no reinstall).",
                       state="daemon_down")
    if not joined:
        return _result("tailscale", "missing",
                       "Installed, but not connected to your tailnet yet.",
                       "step 3 guides the normal browser-based Tailscale login.",
                       state="unjoined")
    return _result("tailscale", "ok", "Installed, service running, tailnet connected.",
                   state="ready")


def _live_group_names() -> list[str]:
    """Process credentials (what THIS process can actually use)."""
    try:
        return [grp.getgrgid(gid).gr_name for gid in os.getgroups()]
    except OSError:
        return []


def gather_docker(exec_fn=None, which_fn=None, getgroups_fn=None,
                  getuser_fn=None) -> dict:
    """Live docker signals → check dict. THE shared probe: run_all() and the
    installer's _docker_check both call this, so the two can never disagree
    again (the denied-vs-down drift came from duplicated probes).

    All inputs injectable (default = live host). exec_fn(argv, timeout?)
    returns (rc, output); which_fn mirrors shutil.which; getgroups_fn mirrors
    os.getgroups; getuser_fn mirrors getpass.getuser.
    """
    import getpass as _gp
    import shutil as _sh
    exec_fn = exec_fn or _run
    which_fn = which_fn or _sh.which
    getgroups_fn = getgroups_fn or os.getgroups
    getuser_fn = getuser_fn or _gp.getuser
    binary = which_fn("docker") is not None
    rc, out = exec_fn(["docker", "info"])
    denied = "permission denied" in (out or "").lower()
    active = exec_fn(["systemctl", "is-active", "docker"])[0] == 0
    engine, compose = "", False
    if rc == 0:
        eng_rc, eng = exec_fn(["docker", "version", "--format",
                               "{{.Server.Version}}"])
        engine = eng if eng_rc == 0 else ""
        compose = exec_fn(["docker", "compose", "version"])[0] == 0
    try:
        groups = [grp.getgrgid(gid).gr_name for gid in getgroups_fn()]
    except OSError:
        groups = []
    try:
        db_has = _db_has_group(getuser_fn(), "docker")
    except OSError:
        db_has = False
    nets = [net for net in MU3LAB_NETWORKS
            if exec_fn(["docker", "network", "inspect", net])[0] == 0]
    return check_docker(rc, groups, nets, engine_version=engine,
                        compose_present=compose, binary_present=binary,
                        permission_denied=denied, daemon_active=active,
                        db_has_group=db_has)


def gather_tailscale(exec_fn=None, which_fn=None) -> dict:
    """Live tailscale signals → check dict. Shared by run_all() and the
    installer (same anti-drift contract as gather_docker)."""
    import shutil as _sh
    exec_fn = exec_fn or _run
    which_fn = which_fn or _sh.which
    binary = which_fn("tailscale") is not None
    active = exec_fn(["systemctl", "is-active", "tailscaled"])[0] == 0
    joined = exec_fn(["tailscale", "status"])[0] == 0 if binary else False
    return check_tailscale(binary, active, joined)


def gate_passed(checks: list[dict]) -> bool:
    """THE gating rule (single source of truth): no FAIL among BLOCKING
    checks. Card ②'s unlock, the server, and the tests all use this —
    nothing maintains a parallel definition."""
    return not any(check.get("status") == "fail" and check.get("blocking")
                   for check in checks)
    """Process credentials (what THIS process can actually use)."""
    try:
        return [grp.getgrgid(gid).gr_name for gid in os.getgroups()]
    except OSError:
        return []


def _db_has_group(user: str, group: str) -> bool:
    """Group-database membership (/etc/group): what a FRESH login would hold.

    Distinguishing this from _live_group_names() is the whole ballgame: DB
    yes + live no means "restart this checker" (not "log out again").
    Absent group name → False (installer will create/fill it).
    """
    import pwd as _pwd
    try:
        entry = grp.getgrnam(group)
    except KeyError:
        return False
    if user in entry.gr_mem:
        return True
    try:
        return _pwd.getpwnam(user).pw_gid == entry.gr_gid
    except KeyError:
        return False


def _port_owner(port: int) -> dict | None:
    """Best-effort owner of a loopback listener: {pid, process, ours}.

    Parses `ss -tlnp` (no privilege needed for own sockets; foreign ones may
    show pid "-"). `ours` is True when the command line belongs to this Mu3Lab
    checkout (uvicorn ctl.app / mu3lab-ctl) — only OUR processes are ever
    stoppable from the dashboard (see /api/service/stop).
    """
    rc, out = _run(["ss", "-tlnp"])
    if rc != 0:
        return None
    pid: int | None = None
    process = ""
    for line in out.splitlines():
        if f"127.0.0.1:{port}" not in line and f"[::1]:{port}" not in line:
            continue
        match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        if match:
            process, pid = match.group(1), int(match.group(2))
            break
    if pid is None:
        # Host-network Compose containers often hide their PID from an
        # unprivileged `ss` invocation. Confirm ownership through Compose
        # labels, but only when the working directory is this checkout.
        project_by_port = {19460: "ingress", 9001: "authentik", 8081: "vaultwarden"}
        project = project_by_port.get(port)
        if project:
            names_rc, names = _run([
                "docker", "ps", "--filter", f"label=com.docker.compose.project={project}",
                "--format", "{{.Names}}"])
            for name in names.splitlines() if names_rc == 0 else []:
                label_rc, working_dir = _run([
                    "docker", "inspect", "--format",
                    "{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}",
                    name.strip()])
                if label_rc == 0 and Path(working_dir).resolve() == (ROOT / "core" / project).resolve():
                    return {"pid": None, "process": name.strip(), "ours": True}
        return {"pid": None, "process": process or "unknown", "ours": False}
    ours = False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(
            errors="replace").replace("\x00", " ")
        ours = ("ctl.app" in cmdline or "mu3lab-ctl" in cmdline) and str(ROOT) in cmdline
        process = cmdline.split()[0] if cmdline.split() else process
    except OSError:
        pass  # foreign user process: owner known, cmdline unreadable
    return {"pid": pid, "process": process, "ours": ours}


def check_ports(connect_fn=None) -> dict:
    """Probe that Step-0/1 ports are free (or already ours).

    `connect_fn(port) -> bool` defaults to a real loopback connect; tests
    inject a stub. A port that ACCEPTS a connection is reported "in use" —
    on a clean box all four must be free. Busy ports carry an `owners` map
    (see _port_owner) so the dashboard can offer a Stop button for OUR
    service instead of a dead-end error.
    """
    if connect_fn is None:
        def connect_fn(port: int) -> bool:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            try:
                return sock.connect_ex(("127.0.0.1", port)) == 0
            finally:
                sock.close()
    busy = [port for port in CHECK_PORTS if connect_fn(port)]
    if busy:
        owners = {str(port): _port_owner(port) for port in busy}
        # Our own autostarted dashboard is EXPECTED here post-install (it
        # starts at boot by design): report it as info, not a conflict.
        # Only foreign holders block.
        ours = [port for port in busy
                if owners.get(str(port)) and owners[str(port)]["ours"]]
        foreign = [port for port in busy if port not in ours]
        if not foreign:
            return {"name": "ports", "status": "ok",
                    "detail": ("Port(s) " + ", ".join(map(str, ours)) +
                               " held by our dashboard service (autostarted — "
                               "normal once installed)."),
                    "action": "", "state": "ready", "blocking": True,
                    "owners": owners}
        return {"name": "ports", "status": "fail",
                "detail": f"Ports already in use: {', '.join(map(str, foreign))}.",
                "action": ("Free the foreign ports before installing; ours "
                           "(if any) can stay or be stopped below."),
                "state": "blocked", "blocking": True, "owners": owners}
    return {"name": "ports", "status": "ok",
            "detail": f"Ports free ({', '.join(map(str, CHECK_PORTS))}).",
            "action": "", "state": "ready", "blocking": True}


def check_bundle(root: Path = ROOT) -> dict:
    """Verify the repo-root venv and the built dashboard bundle exist."""
    problems: list[str] = []
    venv_ok = (root / ".venv" / "bin" / "python").exists()
    if not venv_ok:
        problems.append("project workspace folder (.venv) missing")
    index = root / "dashboard" / "dist" / "index.html"
    if not index.is_file():
        problems.append("built dashboard missing")
    else:
        # Every /assets/* file referenced by index.html must exist on disk.
        html = index.read_text(encoding="utf-8")
        for marker in ('src="/assets/', 'href="/assets/'):
            start = 0
            while True:
                found = html.find(marker, start)
                if found == -1:
                    break
                asset = html[found + len(marker):].split('"', 1)[0]
                if not (index.parent / "assets" / asset).is_file():
                    problems.append(f"bundle references missing asset: {asset}")
                start = found + len(marker)
    if problems:
        state = "no_venv" if not venv_ok else "no_build"
        return _result("bundle", "missing", "; ".join(problems),
                       "step 3 sets up the workspace.", state=state)
    return _result("bundle", "ok", "Workspace ready (.venv + built dashboard).",
                   state="ready")


def check_compose_projects(root: Path = ROOT) -> dict:
    """Report per-project .env presence and container state (Step 1c–1e).

    Read-only: parses compose files and queries `docker compose ps` via _run.
    On a clean box every project reports "not installed" (status missing),
    which is the CORRECT pre-Step-1 answer — not an error.
    """
    lines: list[str] = []
    for project in ("ingress", "authentik", "vaultwarden"):
        projdir = root / "core" / project
        if not (projdir / "docker-compose.yml").is_file():
            lines.append(f"{project}: no compose file yet")
            continue
        env_note = ".env present" if (projdir / ".env").is_file() else ".env missing"
        rc, out = _run(["docker", "compose", "ps", "--format", "{{.State}}"],
                       timeout=15)
        if rc != 0 and "command not found" in out:
            lines.append(f"{project}: docker unavailable ({env_note})")
        else:
            states = out.split() if out else []
            running = sum(1 for state in states if state == "running")
            lines.append(f"{project}: {running} running ({env_note})")
    return _result("compose", "ok" if lines else "missing", "; ".join(lines) or
                   "no compose projects defined yet")


# ---------------------------------------------------------------------------
# Aggregation + live wiring (the only part that touches the real host).
# ---------------------------------------------------------------------------

def run_all() -> dict:
    """Run every check against the live host. Powers GET /api/preflight."""
    # OS release text (best effort; missing file = explicit fail, not crash).
    try:
        release_text = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError as exc:
        release_text = f"ID=unknown\nVERSION_ID=0\nPRETTY_NAME=unreadable ({exc})"
    # Node version (absent binary is a normal "missing").
    _, node_out = _run(["node", "--version"])
    # Privilege signals: fresh sudo? graphical session?
    sudo_fresh = _run(["sudo", "-n", "true"])[0] == 0
    graphical = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    # Docker + tailscale signals: shared gatherers (install/verify use the
    # same code path, so the two can never disagree again).
    docker_check = gather_docker()
    tailscale_check = gather_tailscale()

    _, lspci_out = _run(["lspci", "-nn"])
    checks = [
        check_os(release_text, kernel_release=platform.release()),
        check_arch(platform.machine()),
        check_gpu(_run(["nvidia-smi", "-L"])[0] == 0,
                  "amd" in lspci_out.lower() or "advanced micro devices" in lspci_out.lower()),
        check_python(tuple(sys.version_info)),
        check_node(node_out),
        check_privilege(sudo_fresh, graphical),
        docker_check,
        tailscale_check,
        check_ports(),
        check_bundle(),
        check_compose_projects(),
    ]
    # Each check is stamped blocking True/False from BLOCKING so the dashboard
    # can split "fix this yourself" from "step ③ provides it" with no extra
    # logic. "ok" (all green) remains for exactness but gates nothing; the
    # gate itself is gate_passed() below (single rule, shared with server).
    for check in checks:
        check["blocking"] = check["name"] in BLOCKING
    return {"ok": all(check["status"] == "ok" for check in checks),
            "install_ready": gate_passed(checks), "checks": checks}


def main() -> int:
    """Pretty-print run_all() for humans. Exit 0 only when all green."""
    report = run_all()
    width = max(len(check["name"]) for check in report["checks"])
    for check in report["checks"]:
        mark = {"ok": "PASS", "missing": "TODO", "fail": "FAIL"}[check["status"]]
        print(f"[{mark}] {check['name']:<{width}}  {check['detail']}")
        if check["action"]:
            print(f"       {check['action']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
