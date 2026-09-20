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
import json as _json
import queue as _queue
import shlex as _shlex
import subprocess as _subprocess
import threading as _threading
import time as _time
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
    log("$ docker " + " ".join(argv[1:] if argv[:1] == ["docker"] else argv))
    command_env = _docker_config_env()
    if env:
        command_env.update(env)
    command = _docker_invocation(argv)
    if command is None:
        return 1, ("docker unavailable: no live group and no DB membership "
                   "(installer should have added you — report this)")
    return privilege._exec(command, timeout=timeout, env=command_env)


def _docker_invocation(argv: list[str]) -> list[str] | None:
    """Return the direct/``sg`` Docker command without running it.

    Kept separate so streaming and buffered Docker commands use exactly the
    same group-access policy.  It is deliberately internal: callers still go
    through ``docker_cmd`` or ``docker_cmd_stream`` for logging and isolation.
    """
    import getpass as _gp
    import shutil as _sh
    from ctl import preflight as _pre
    try:
        import grp as _grp
        live_groups = [_grp.getgrgid(gid).gr_name for gid in _os.getgroups()]
    except OSError:
        live_groups = []
    if "docker" in live_groups:
        return argv
    if _sh.which("sg") and _pre._db_has_group(_gp.getuser(), "docker"):
        return ["sg", "docker", "-c", _shlex.join(argv)]
    return None


def docker_cmd_stream(argv: list[str], log: Callable[[str], None], *,
                      timeout: int = 300, env: dict[str, str] | None = None,
                      on_output: Callable[[str], None] | None = None) -> tuple[int, str]:
    """Run a Docker command while forwarding bounded, line-oriented output.

    ``subprocess.run(capture_output=True)`` is appropriate for short probes,
    but makes a first image pull look frozen.  This helper is intentionally
    limited to installer-owned Docker commands.  It never invokes a shell and
    keeps the same group and isolated-DOCKER_CONFIG rules as ``docker_cmd``.
    """
    log("$ docker " + " ".join(argv[1:] if argv[:1] == ["docker"] else argv))
    command = _docker_invocation(argv)
    if command is None:
        return 1, ("docker unavailable: no live group and no DB membership "
                   "(installer should have added you — report this)")
    command_env = _docker_config_env()
    if env:
        command_env.update(env)
    try:
        proc = _subprocess.Popen(command, stdout=_subprocess.PIPE,
                                 stderr=_subprocess.STDOUT, text=True,
                                 bufsize=1, env={**_os.environ, **command_env})
    except OSError as exc:
        return 126, f"{command[0]}: {exc}"
    assert proc.stdout is not None
    lines: list[str] = []
    inbox: _queue.Queue[str | None] = _queue.Queue()

    def read_output() -> None:
        try:
            for line in proc.stdout:
                inbox.put(line.rstrip())
        finally:
            inbox.put(None)

    reader = _threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = _time.monotonic() + timeout
    finished = False
    while not finished:
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            proc.kill()
            proc.wait()
            return 124, "docker compose timed out"
        try:
            line = inbox.get(timeout=min(1.0, remaining))
        except _queue.Empty:
            if proc.poll() is not None:
                finished = True
            continue
        if line is None:
            finished = True
            continue
        # Docker progress can be extremely verbose.  The complete command
        # output remains diagnostic material, while a caller receives each
        # bounded line promptly for a useful current-activity indicator.
        line = line[:2000]
        lines.append(line)
        log(line)
        if on_output:
            on_output(line)
    rc = proc.wait()
    return rc, "\n".join(lines).strip()


def docker_cmd_with_stdin(argv: list[str], stdin_data: str,
                          log: Callable[[str], None], *,
                          timeout: int = 60) -> tuple[int, str]:
    """Run one installer-owned Docker command with private standard input.

    This is deliberately separate from :func:`docker_cmd`: its input must
    never be reflected in the command log, environment, or returned output.
    It exists for tools such as Authentik's interactive password-reset command
    whose supported interface reads the new value from standard input.
    """
    log("$ docker " + " ".join(argv[1:] if argv[:1] == ["docker"] else argv)
        + "  (private input supplied securely)")
    command = _docker_invocation(argv)
    if command is None:
        return 1, ("docker unavailable: no live group and no DB membership "
                   "(installer should have added you — report this)")
    try:
        proc = _subprocess.Popen(
            command, stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
            stderr=_subprocess.STDOUT, text=True,
            env={**_os.environ, **_docker_config_env()},
        )
        output, _ = proc.communicate(stdin_data, timeout=timeout)
    except _subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return 124, "docker command timed out"
    except OSError as exc:
        return 126, f"{command[0]}: {exc}"
    # Callers provide secrets through stdin.  Do not let an unexpected child
    # echo turn a diagnostic into a credential leak.
    return proc.returncode, output.replace(stdin_data, "[redacted]").strip()


def reset_authentik_admin_password(password: str,
                                   log: Callable[[str], None]) -> dict:
    """Set a one-time password for the built-in ``akadmin`` account.

    Authentik documents ``ak changepassword akadmin`` as its recovery path.
    The generated password is sent twice on stdin for Django's confirmation
    prompt.  It is intentionally neither logged nor retained here.
    """
    if not password or "\n" in password or "\r" in password:
        return {"ok": False, "error": "invalid temporary password"}
    rc, output = docker_cmd_with_stdin(
        ["docker", "exec", "-i", "authentik-server-1", "ak",
         "changepassword", "akadmin"],
        password + "\n" + password + "\n", log, timeout=90)
    if rc != 0:
        # Django's output can include installation details.  The bootstrap
        # needs only an actionable, non-sensitive result.
        return {"ok": False,
                "error": "Authentik could not reset the akadmin password. "
                         "Confirm its server container is healthy, then retry."}
    log("Authentik akadmin password reset; temporary password was not logged.")
    return {"ok": True}


def freellmapi_local_setup(email: str, password: str,
                           log: Callable[[str], None]) -> tuple[int, dict]:
    """Claim a fresh FreeLLMAPI from inside its container's loopback boundary.

    Upstream intentionally requires a setup code when the socket peer is not
    loopback. Docker port publishing makes a host request appear remote, so the
    reviewed first-run call runs inside the container. Credentials travel only
    on stdin and the one-time session token is returned only to the caller.
    """
    if not email or not password or "\n" in email or "\n" in password:
        return 2, {}
    script = (
        "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',async()=>{"
        "try{const r=await fetch('http://127.0.0.1:3001/api/auth/setup',{method:'POST',"
        "headers:{'content-type':'application/json'},body:d});const b=await r.json();"
        "process.stdout.write(JSON.stringify({status:r.status,body:b}))}"
        "catch(e){process.exit(1)}})"
    )
    payload = _json.dumps({"email": email, "password": password}, separators=(",", ":")) + "\n"
    rc, output = docker_cmd_with_stdin(
        ["docker", "exec", "-i", "mu3lab-freellmapi-freellmapi-1",
         "node", "-e", script], payload, log, timeout=30)
    if rc:
        return rc, {}
    try:
        decoded = _json.loads(output)
    except (TypeError, ValueError):
        return 1, {}
    return 0, decoded if isinstance(decoded, dict) else {}


def compose_up(projdir: Path, log: Callable[[str], None],
               timeout: int = 300, env: dict[str, str] | None = None,
               extra_files: list[Path] | None = None,
               wait_timeout: int | None = None,
               recreate: bool = False,
               on_output: Callable[[str], None] | None = None) -> tuple[int, str]:
    """`docker compose up -d` for a project dir, via docker_cmd (sg-aware).

    Uses -f/--project-directory flags instead of cwd= so `sg -c` (single
    string, no shell games beyond one quoted layer) stays exact. Extra files
    are reviewed service-owned overrides, never browser-provided paths.
    """
    files = [projdir / "docker-compose.yml"]
    digest_override = projdir / "docker-compose.digest.yml"
    if digest_override.is_file():
        files.append(digest_override)
    files += list(extra_files or [])
    argv = ["docker", "compose"]
    for compose_file in files:
        argv.extend(["-f", str(compose_file)])
    argv.extend(["--project-directory", str(projdir), "up", "-d"])
    if recreate:
        argv.append("--force-recreate")
    if wait_timeout is not None:
        argv.extend(["--wait", "--wait-timeout", str(wait_timeout)])
    if on_output:
        return docker_cmd_stream(argv, log, timeout=timeout, env=env,
                                 on_output=on_output)
    return docker_cmd(argv, log, timeout=timeout, env=env)


def compose_config(projdir: Path, log: Callable[[str], None], *,
                   env: dict[str, str] | None = None) -> tuple[int, str]:
    """Validate one materialized curated project before any image pull."""
    command = ["docker", "compose", "-f", str(projdir / "docker-compose.yml"),
               "--project-directory", str(projdir), "config", "--quiet"]
    return docker_cmd(command, log, timeout=60, env=env)


def compose_pull(projdir: Path, log: Callable[[str], None], *,
                 timeout: int = 1800,
                 env: dict[str, str] | None = None) -> tuple[int, str]:
    """Pull images declared by one curated, already-validated Compose file."""
    command = ["docker", "compose", "-f", str(projdir / "docker-compose.yml"),
               "--project-directory", str(projdir), "pull"]
    return docker_cmd_stream(command, log, timeout=timeout, env=env)


def compose_images(projdir: Path, log: Callable[[str], None]) -> tuple[int, str]:
    """Return resolved image IDs for audit after a successful curated pull."""
    command = ["docker", "compose", "-f", str(projdir / "docker-compose.yml"),
               "--project-directory", str(projdir), "images", "--format", "json"]
    return docker_cmd(command, log, timeout=60)


def compose_exec(projdir: Path, service: str, argv: list[str],
                 log: Callable[[str], None], *, timeout: int = 300,
                 env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run a fixed, reviewed command inside one curated Compose service.

    Callers must supply a service name and argument list from source code; no
    browser input reaches this function.  It exists for idempotent model
    preparation without reopening a shell escape hatch in the control plane.
    """
    if not service or not argv or any(not isinstance(value, str) for value in argv):
        return 2, "invalid reviewed compose exec request"
    command = ["docker", "compose", "-f", str(projdir / "docker-compose.yml"),
               "--project-directory", str(projdir), "exec", "-T", service, *argv]
    return docker_cmd(command, log, timeout=timeout, env=env)


def compose_action(projdir: Path, action: str, log: Callable[[str], None],
                   *, timeout: int = 600,
                   env: dict[str, str] | None = None,
                   extra_files: list[Path] | None = None) -> tuple[int, str]:
    """Run one allowlisted lifecycle verb for a reviewed Compose project."""
    verbs = {
        "start": ["up", "-d"],
        "stop": ["stop"],
        "restart": ["up", "-d", "--force-recreate"],
    }
    if action not in verbs:
        return 2, "unsupported curated lifecycle action"
    files = [projdir / "docker-compose.yml"]
    digest_override = projdir / "docker-compose.digest.yml"
    if digest_override.is_file():
        files.append(digest_override)
    files += list(extra_files or [])
    command = ["docker", "compose"]
    for compose_file in files:
        command.extend(["-f", str(compose_file)])
    command.extend(["--project-directory", str(projdir), *verbs[action]])
    return docker_cmd_stream(command, log, timeout=timeout, env=env)


def compose_logs(projdir: Path, log: Callable[[str], None], *,
                 tail: int = 120, container: str = "") -> tuple[int, str]:
    """Return bounded logs without accepting paths or arbitrary arguments."""
    bounded = max(20, min(int(tail), 500))
    command = ["docker", "compose", "-f", str(projdir / "docker-compose.yml")]
    digest_override = projdir / "docker-compose.digest.yml"
    if digest_override.is_file():
        command.extend(["-f", str(digest_override)])
    command.extend(["--project-directory", str(projdir), "logs", "--no-color",
                    "--tail", str(bounded)])
    if container:
        command.append(container)
    return docker_cmd(command, log, timeout=30)


def docker_container_statuses(project: str) -> tuple[int, str]:
    """Return compact status lines for one Compose project, without logging.

    This is a read-only progress probe.  It deliberately uses Docker labels
    rather than re-evaluating a Compose file, so it works while the project is
    still starting and does not need to receive service environment values.
    """
    return docker_cmd([
        "docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
        "--format", "{{.Names}}\t{{.Status}}",
    ], lambda _line: None, timeout=15)


def docker_image_digest(image: str) -> tuple[int, str]:
    """Resolve one manifest-owned image reference to its immutable repo digest."""
    if not image or any(char.isspace() for char in image):
        return 2, "invalid curated image reference"
    return docker_cmd([
        "docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", image,
    ], lambda _line: None, timeout=30)


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

    APT's ``.gpg`` keyring format is binary, while the official repository
    endpoints serve ASCII-armored keys.  Dearmor those inputs before staging;
    merely copying an armored key into a ``.gpg`` file makes APT silently
    ignore the key and produces a misleading NO_PUBKEY error.  Staging and
    conversion happen as the user; only the final copy and chmod are elevated.
    """
    lines: list[str] = []
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="mu3lab-"))
    tmpdir.chmod(0o700)
    staged = tmpdir / "payload"
    if Path(path).suffix == ".gpg" and data.lstrip().startswith(
            b"-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        armored = tmpdir / "payload.asc"
        binary = tmpdir / "payload.gpg"
        armored.write_bytes(data)
        gnupg_home = tmpdir / "gnupg"
        gnupg_home.mkdir(mode=0o700)
        try:
            proc = _subprocess.run(
                ["gpg", "--batch", "--yes", "--dearmor",
                 "--output", str(binary), str(armored)],
                capture_output=True, text=True, timeout=30,
                env={**_os.environ, "GNUPGHOME": str(gnupg_home)})
        except (OSError, _subprocess.TimeoutExpired) as exc:
            return _fail([f"could not dearmor GPG key for {path}: {exc}"])
        if proc.returncode != 0 or not binary.is_file():
            detail = (proc.stderr or proc.stdout or "gpg dearmor failed").strip()
            return _fail([f"could not dearmor GPG key for {path}: {detail}"])
        staged.write_bytes(binary.read_bytes())
        lines.append(f"dearmored ASCII key for {path}")
    else:
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


def tailscale_serve(port: int, loopback_port: int,
                    log: Callable[[str], None]) -> dict:
    """Publish one registry-owned loopback listener through Tailscale Serve."""
    if not (1 <= int(port) <= 65535 and 1 <= int(loopback_port) <= 65535):
        return _fail(["invalid curated Tailscale route"])
    argv = ["tailscale", "serve", "--bg", f"--https={int(port)}",
            f"http://127.0.0.1:{int(loopback_port)}"]
    # Tailscale can delegate Serve configuration to the dashboard operator
    # (`tailscale set --operator=<user>`). Try that supported unprivileged path
    # first so a background worker does not invoke a polkit dialog needlessly.
    try:
        direct = _subprocess.run(argv, capture_output=True, text=True, timeout=60)
        direct_output = (direct.stdout + direct.stderr).strip()
        if direct.returncode == 0:
            log(direct_output or "Private HTTPS route published.")
            return _ok(["Private HTTPS route published."], changed=True)
        log(direct_output or f"(exit {direct.returncode}, no output)")
    except (OSError, _subprocess.TimeoutExpired) as exc:
        log(str(exc))
    result = privilege.run_privileged(argv, log, timeout=60)
    if result.get("need_terminal"):
        return _fail(["Tailscale Serve requires an administrator command."],
                     terminal_command=result["terminal_command"])
    return _ok(["Private HTTPS route published."], changed=True) if result.get("ok") else _fail(
        ["Tailscale Serve could not publish the curated route."])


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
