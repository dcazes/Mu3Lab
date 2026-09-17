"""Mu3Lab :: ctl/actions.py

WHAT: Small privileged HOST verbs used by the installer: apt repos/packages,
      systemd enable+start, group membership, docker networks. Each function
      logs its exact command first, runs it via ctl/privilege.py (fresh sudo
      → pkexec → terminal-command fallback), and returns a structured dict.
WHY:  Splitting verbs (here) from ordering (ctl/install.py) keeps every hail-
      mary shell line in ONE auditable place. Nothing here knows about
      docker-vs-tailscale sequencing; it just performs single host mutations.
RUN:  Imported by ctl/install.py. Unit-tested with mocked privilege layer —
      no test touches apt/systemd/docker.
DEBUG: Return shape is ALWAYS {"ok", "changed", "log"[, "terminal_command"]}.
      `changed=False` with ok=True means "already so, touched nothing"
      (install.sh-grade idempotency per verb).
"""

from __future__ import annotations

import os as _os
import shlex as _shlex
from collections.abc import Callable
from pathlib import Path
import urllib.request

from ctl import privilege


def _ok(log_lines: list[str], changed: bool = True, **extra) -> dict:
    """Success envelope (single constructor so the shape can't drift)."""
    return {"ok": True, "changed": changed, "log": log_lines, **extra}


def _fail(log_lines: list[str], **extra) -> dict:
    """Failure envelope (same shape, ok=False)."""
    return {"ok": False, "changed": False, "log": log_lines, **extra}


def _run_user(argv: list[str], log: Callable[[str], None],
              _exec=privilege._exec) -> tuple[int, str]:
    """Run an UNPRIVILEGED command (docker, compose, curl-equivalents).

    Logged the same way as privileged calls ($-prefixed, no sudo).
    Separated so tests can distinguish "needed root" from "ran as user".
    """
    log("$ " + " ".join(argv))  # argv here is internally built, never user input
    return _exec(argv)


def _docker_config_env() -> dict[str, str]:
    """Isolated DOCKER_CONFIG dir for OUR docker invocations.

    Why: `docker` writes ~/.docker/config.json on first run. When root-run
    and user-run docker mix (worker vs direct), the file ends up root-owned
    and later user runs fail with permission denied (Docker's own docs warn
    about this). A per-uid temp dir keeps both worlds separate. 0700.
    """
    import tempfile
    path = Path(tempfile.gettempdir()) / f"mu3lab-docker-cfg-{_os.getuid()}"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {"DOCKER_CONFIG": str(path)}


def docker_cmd(argv: list[str], log: Callable[[str], None],
               timeout: int = 300, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run a docker CLI command with whatever access exists. THE choke point:
    every installer docker invocation flows through here (never raw).

    - live group → direct, as the user.
    - DB-member-only → `sg docker -c` (group added on the fly, no logout;
      `sg` ships in stock Ubuntu's login package; probed, not assumed).
    - neither → (1, message) without executing anything.
    """
    import getpass as _gp
    import shutil as _sh
    from ctl import preflight as _pre
    log("$ docker " + " ".join(argv[1:] if argv[:1] == ["docker"] else argv))
    command_env = _docker_config_env()
    if env:
        command_env.update(env)
    try:
        live = privilege._exec  # resolved late for test patching
        import grp as _grp
        live_groups = [_grp.getgrgid(gid).gr_name for gid in _os.getgroups()]
    except OSError:
        live_groups = []
    if "docker" in live_groups:
        return privilege._exec(argv, timeout=timeout, env=command_env)
    if _sh.which("sg") and _pre._db_has_group(_gp.getuser(), "docker"):
        return privilege._exec(
            ["sg", "docker", "-c", _shlex.join(argv)],
            timeout=timeout, env=command_env)
    return 1, ("docker unavailable: no live group and no DB membership "
               "(installer should have added you — report this)")


def compose_up(projdir: Path, log: Callable[[str], None],
               timeout: int = 300, env: dict[str, str] | None = None) -> tuple[int, str]:
    """`docker compose up -d` for a project dir, via docker_cmd (sg-aware).

    Uses -f/--project-directory flags instead of cwd= so `sg -c` (single
    string, no shell games beyond one quoted layer) stays exact.
    """
    return docker_cmd(
        ["docker", "compose",
         "-f", str(projdir / "docker-compose.yml"),
         "--project-directory", str(projdir),
         "up", "-d"],
        log, timeout=timeout, env=env)


def apt_update(log: Callable[[str], None]) -> dict:
    """`apt-get update`. Always runs (cheap, makes installs deterministic)."""
    lines: list[str] = []
    res = privilege.run_privileged(["apt-get", "update"], lines.append)
    if res.get("need_terminal"):
        return _fail(lines, terminal_command=res["terminal_command"])
    if not res["ok"]:
        lines.append("apt-get update failed; fix sources and retry.")
        return _fail(lines)
    return _ok(lines)


def apt_install(packages: list[str], log: Callable[[str], None]) -> dict:
    """`apt-get install -y <packages>`. Non-empty list required."""
    lines: list[str] = []
    if not packages:
        return _fail(lines + ["apt_install called with empty package list"])
    res = privilege.run_privileged(["apt-get", "install", "-y"] + packages,
                                   lines.append)
    if res.get("need_terminal"):
        return _fail(lines, terminal_command=res["terminal_command"])
    if not res["ok"]:
        lines.append(f"install failed for: {' '.join(packages)}")
        return _fail(lines)
    return _ok(lines)


def fetch_url(url: str, dest: Path, log: Callable[[str], None],
              mode: int = 0o644) -> dict:
    """Download a repo GPG key with urllib (no curl dependency) as the USER.

    Writes only under `dest`'s existing parent; keyring dirs (/etc/apt/...)
    are created by write_root_file() below (privileged). Pure-Python fetch
    keeps the bootstrap curl-free.
    """
    lines: list[str] = []
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
    except OSError as exc:
        return _fail(lines + [f"download failed: {url}: {exc}"])
    try:
        dest.write_bytes(data)
        dest.chmod(mode)
    except OSError as exc:
        return _fail(lines + [f"cannot write {dest}: {exc}"])
    lines.append(f"downloaded {len(data)} bytes: {url} -> {dest}")
    return _ok(lines)


def write_root_file(path: str, content: str, log: Callable[[str], None],
                    mode: str = "644") -> dict:
    """Write a root-owned TEXT file (apt .list) as root. See write_root_bytes
    for binary payloads (GPG keys). Content travels via staged files, never
    on a command line.
    """
    return write_root_bytes(path, content.encode("utf-8"), log, mode=mode)


def write_root_bytes(path: str, data: bytes, log: Callable[[str], None],
                     mode: str = "644") -> dict:
    """Write a root-owned BINARY file (repo GPG keys) as root.

    Staged in a user-private temp dir (0700), then `cp`+`chmod` privileged.
    Separate from write_root_file because binary bytes must never pass
    through a str round-trip (utf-8 would corrupt bytes >= 0x80).
    """
    lines: list[str] = []
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="mu3lab-"))
    tmpdir.chmod(0o700)
    staged = tmpdir / "payload"
    staged.write_bytes(data)
    lines.append(f"staged {len(data)} bytes for {path}")
    cp = privilege.run_privileged(["cp", str(staged), path], lines.append)
    if cp.get("need_terminal") or not cp["ok"]:
        lines.append(f"could not place {path}")
        return _fail(lines, **({"terminal_command": cp["terminal_command"]}
                               if cp.get("need_terminal") else {}))
    ch = privilege.run_privileged(["chmod", mode, path], lines.append)
    if not ch["ok"] and not ch.get("need_terminal"):
        return _fail(lines)
    if ch.get("need_terminal"):
        return _fail(lines, terminal_command=ch["terminal_command"])
    return _ok(lines)


def remove_root_file(path: str, log: Callable[[str], None]) -> dict:
    """Remove one installer-owned file through the normal privilege boundary.

    This is intentionally narrower than cleaning an APT directory.  It lets
    a fresh reinstall repair stale Mu3Lab repository definitions whose keys
    were removed, without touching unrelated user repositories.
    """
    lines: list[str] = []
    res = privilege.run_privileged(["rm", "-f", path], lines.append)
    if res.get("need_terminal"):
        return _fail(lines, terminal_command=res["terminal_command"])
    return _ok(lines) if res["ok"] else _fail(lines)


def systemctl_enable_now(unit: str, log: Callable[[str], None],
                         user_scope: bool = False) -> dict:
    """`systemctl enable --now <unit>` (system scope) or --user variant."""
    lines: list[str] = []
    if user_scope:
        rc, out = privilege._exec(["systemctl", "--user", "enable", "--now", unit])
        lines.append(f"$ systemctl --user enable --now {unit}")
        lines.append(out or f"(exit {rc})")
        if rc != 0:
            return _fail(lines)
        return _ok(lines)
    res = privilege.run_privileged(["systemctl", "enable", "--now", unit],
                                   lines.append)
    if res.get("need_terminal"):
        return _fail(lines, terminal_command=res["terminal_command"])
    if not res["ok"]:
        return _fail(lines)
    return _ok(lines)


def usermod_add_group(user: str, group: str, log: Callable[[str], None]) -> dict:
    """`usermod -aG <group> <user>` (append-only, never replaces groups)."""
    lines: list[str] = []
    res = privilege.run_privileged(["usermod", "-aG", group, user],
                                   lines.append)
    if res.get("need_terminal"):
        return _fail(lines, terminal_command=res["terminal_command"])
    if not res["ok"]:
        return _fail(lines)
    return _ok(lines, changed=True)


def docker_network_create(name: str, log: Callable[[str], None],
                          internal: bool = False) -> dict:
    """`docker network create [--internal] <name>`, sg-aware via docker_cmd.

    Unprivileged by design (relies on group membership OR the sg fallback,
    both handled inside docker_cmd) — never needs the pkexec worker.
    """
    argv = (["docker", "network", "create"]
            + (["--internal"] if internal else []) + [name])
    rc, out = docker_cmd(argv, log)
    lines = [out or f"(exit {rc})"]
    if rc != 0:
        return _fail(lines)
    return _ok(lines)


def ensure_runtime_layout(root: Path, user: str, log: Callable[[str], None]) -> dict:
    """Create the approved /srv layout with root-only secrets and user state.

    The root itself is group-traversable by the operator. Without that one
    permission, user-owned children such as `data/` remain unreachable.
    """
    lines: list[str] = []
    paths = [root, root / "data", root / "backups", root / "runtime", root / "projects"]
    for path in paths:
        res = privilege.run_privileged(["install", "-d", "-m", "0750", str(path)], lines.append)
        if res.get("need_terminal"):
            return _fail(lines, terminal_command=res["terminal_command"])
        if not res["ok"]:
            return _fail(lines)
    root_owner = privilege.run_privileged(
        ["chown", f"root:{user}", str(root)], lines.append)
    if root_owner.get("need_terminal"):
        return _fail(lines, terminal_command=root_owner["terminal_command"])
    if not root_owner["ok"]:
        return _fail(lines)
    secret = privilege.run_privileged(["install", "-d", "-m", "0700", str(root / "secrets")], lines.append)
    if secret.get("need_terminal"):
        return _fail(lines, terminal_command=secret["terminal_command"])
    if not secret["ok"]:
        return _fail(lines)
    owned = [str(path) for path in paths[1:]]
    res = privilege.run_privileged(["chown", "-R", f"{user}:{user}"] + owned, lines.append)
    if res.get("need_terminal"):
        return _fail(lines, terminal_command=res["terminal_command"])
    return _ok(lines) if res["ok"] else _fail(lines)
