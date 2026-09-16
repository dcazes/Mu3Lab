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
    """`docker network create [--internal] <name>` as the invoking user.

    Unprivileged by design (relies on docker-group membership, which the
    installer's group checkpoint guarantees before networks are attempted).
    """
    lines: list[str] = []
    argv = ["docker", "network", "create"] + (["--internal"] if internal else []) + [name]
    rc, out = privilege._exec(argv)
    lines.append("$ " + " ".join(argv))
    lines.append(out or f"(exit {rc})")
    if rc != 0:
        return _fail(lines)
    return _ok(lines)
