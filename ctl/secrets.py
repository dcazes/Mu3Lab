"""Mu3Lab :: ctl/secrets.py

WHAT: Secret generation with preserve-existing semantics. CURRENT scope is
      ONLY the repo-root .env (MU3LAB_CTL_TOKEN, MU3LAB_INGRESS_TOKEN) needed
      by card ③'s env step. Per-service .env generation (authentik,
      vaultwarden, …) arrives with the real dashboard's app installers.
WHY:  Tokens must be random, mode-0600, and NEVER overwritten once created
      (rotating a live token orphans running services). One module owns that
      invariant; callers only learn which keys were ADDED (never values).
RUN:  Imported by ctl/install.py. No server use directly.
DEBUG: `stat -c %a .env` must print 600. Values are logged as names only —
      grep any log for a token value and file a bug.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT_ENV_KEYS = ("MU3LAB_CTL_TOKEN", "MU3LAB_INGRESS_TOKEN")


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
