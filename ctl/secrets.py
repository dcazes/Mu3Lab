"""Mu3Lab :: ctl/secrets.py

WHAT: Secret generation with preserve-existing semantics for the bootstrap,
      identity services, and generated core-suite service env files.
WHY:  Tokens must be random, mode-0600, and NEVER overwritten once created
      (rotating a live token orphans running services). One module owns that
      invariant; callers only learn which keys were ADDED (never values).
RUN:  Imported by ctl/install.py and the authenticated core-suite executor.
DEBUG: `stat -c %a .env` must print 600. Values are logged as names only —
      grep any log for a token value and file a bug.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT_ENV_KEYS = ("MU3LAB_CTL_TOKEN", "MU3LAB_INGRESS_TOKEN")
CORE_ENV_KEYS = {
    # Ollama has no private values today, but it still participates in the
    # common runtime-environment contract used by the core executor.
    "ollama": (),
    "freellmapi": ("ENCRYPTION_KEY", "FREELLMAPI_SERVICE_KEY", "FREELLMAPI_ADMIN_PASSWORD"),
    "litellm": ("LITELLM_MASTER_KEY",),
}


def ensure_authentik_env(root: Path, token_factory=None) -> tuple[Path, list[str]]:
    """Create the Authentik runtime env without returning secret values.

    The control plane needs a stable database password and secret key across
    restarts, but neither belongs in Git, browser responses, or job logs.
    Runtime project files are operator-readable (0600) so Compose can consume
    them through the normal unprivileged Docker group path.
    """
    token_factory = token_factory or generate_hex
    target = root / "projects" / "authentik" / ".env"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if target.is_file():
        for line in target.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                existing[key.strip()] = value.strip()
    values = {
        "AUTHENTIK_TAG": existing.get("AUTHENTIK_TAG", "2026.5.0"),
        "AUTHENTIK_SECRET_KEY": existing.get("AUTHENTIK_SECRET_KEY") or token_factory(),
        "AUTHENTIK_POSTGRESQL__PASSWORD": existing.get("AUTHENTIK_POSTGRESQL__PASSWORD") or token_factory(),
    }
    added = [key for key in values if key not in existing]
    if added or not target.is_file():
        target.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    return target, added


def read_runtime_env(path: Path) -> dict[str, str]:
    """Read generated KEY=VALUE lines for a private Compose boundary."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            if key.strip():
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] == "'":
                    value = value[1:-1].replace("\\'", "'").replace("\\\\", "\\")
                elif len(value) >= 2 and value[0] == value[-1] == '"':
                    value = value[1:-1]
                values[key.strip()] = value
    return values


def runtime_env_text(values: dict[str, str]) -> str:
    """Serialize literal Compose dotenv values without accidental interpolation."""
    lines = []
    for key, value in values.items():
        if not key or any(char in key for char in "=\r\n") or "\r" in value or "\n" in value:
            raise ValueError("runtime environment entries must be single-line key/value pairs")
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"{key}='{escaped}'")
    return "\n".join(lines) + "\n"


def ensure_core_envs(root: Path, token_factory=None) -> dict[str, Path]:
    """Create stable internal service env files without provider credentials."""
    token_factory = token_factory or generate_hex
    project_root = root / "projects"
    project_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    shared: dict[str, str] = {}
    paths: dict[str, Path] = {}
    for service_id, keys in CORE_ENV_KEYS.items():
        target = project_root / service_id / ".env"
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        values = read_runtime_env(target)
        for key in keys:
            if values.get(key):
                continue
            if key == "LITELLM_MASTER_KEY" and shared.get(key):
                values[key] = shared[key]
            else:
                values[key] = token_factory()
            if key == "LITELLM_MASTER_KEY":
                shared[key] = values[key]
        target.write_text(runtime_env_text(values), encoding="utf-8")
        os.chmod(target, 0o600)
        paths[service_id] = target
        if values.get("LITELLM_MASTER_KEY"):
            shared["LITELLM_MASTER_KEY"] = values["LITELLM_MASTER_KEY"]
    return paths


def generate_hex(nbytes: int = 32) -> str:
    """Random hex token (injectable indirection over secrets for tests)."""
    return secrets.token_hex(nbytes)


def ensure_root_env(root: Path,
                    token_factory=generate_hex) -> tuple[dict[str, str], list[str]]:
    """Ensure root .env exists (0600) with both MU3LAB_* tokens present.

    Creates the file if missing, ADDS absent keys, preserves everything else
    byte-for-byte (existing tokens, comments, unrelated keys). Returns the
    full mapping and the list of ADDED key names. Callers must log names only.
    """
    env_path = root / ".env"
    values: dict[str, str] = {}
    existing_lines: list[str] = []
    if env_path.is_file():
        # Preserve the file EXACTLY (comments, order, unrelated keys): only
        # append missing keys at the end, never rewrite.
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()
        for line in existing_lines:
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    added: list[str] = []
    new_lines = [line for line in existing_lines]
    for key in ROOT_ENV_KEYS:
        if not values.get(key):
            values[key] = token_factory()
            new_lines.append(f"{key}={values[key]}")
            added.append(key)
    if added or not env_path.is_file():
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, ("\n".join(new_lines) + "\n").encode())
        finally:
            os.close(fd)
        os.chmod(env_path, 0o600)  # belt-and-braces (umask-proof)
    return values, added
