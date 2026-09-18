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
    "freellmapi": ("ENCRYPTION_KEY", "FREELLMAPI_SERVICE_KEY", "FREELLMAPI_ADMIN_PASSWORD"),
    "litellm": ("LITELLM_MASTER_KEY",),
    "open-webui": ("WEBUI_SECRET_KEY", "LITELLM_MASTER_KEY"),
    "firecrawl": ("POSTGRES_PASSWORD", "TEST_API_KEY"),
    "surfsense": ("DB_PASSWORD", "SECRET_KEY", "DATABASE_URL", "REDIS_URL"),
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
                values[key.strip()] = value.strip()
    return values


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
            elif key == "DATABASE_URL":
                password = values.get("DB_PASSWORD") or shared.get("DB_PASSWORD") or token_factory()
                shared["DB_PASSWORD"] = password
                values[key] = f"postgresql+asyncpg://surfsense:{password}@db:5432/surfsense"
            elif key == "REDIS_URL":
                values[key] = "redis://redis:6379/0"
            else:
                values[key] = token_factory()
            if key == "LITELLM_MASTER_KEY":
                shared[key] = values[key]
        # Keep common routing inputs local to SurfSense and not in Git.
        if service_id == "surfsense":
            values.setdefault("LLM_API_BASE_URL", "http://litellm:4000/v1")
            values.setdefault("LLM_MODEL", "mu3lab-chat")
            values.setdefault("EMBEDDING_API_BASE_URL", "http://ollama:11434")
            values.setdefault("EMBEDDING_MODEL", "nomic-embed-text")
            values.setdefault("FIRECRAWL_API_URL", "http://api:3002")
        target.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
        os.chmod(target, 0o600)
        paths[service_id] = target
        if values.get("LITELLM_MASTER_KEY"):
            shared["LITELLM_MASTER_KEY"] = values["LITELLM_MASTER_KEY"]
    # Reuse the same LiteLLM key in Open WebUI while keeping both files private.
    litellm = read_runtime_env(paths["litellm"])
    open_webui = read_runtime_env(paths["open-webui"])
    if open_webui.get("LITELLM_MASTER_KEY") != litellm.get("LITELLM_MASTER_KEY"):
        open_webui["LITELLM_MASTER_KEY"] = litellm["LITELLM_MASTER_KEY"]
        paths["open-webui"].write_text("\n".join(f"{key}={value}" for key, value in open_webui.items()) + "\n", encoding="utf-8")
        os.chmod(paths["open-webui"], 0o600)
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
