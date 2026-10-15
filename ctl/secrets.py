"""Mu3Lab :: ctl/secrets.py

WHAT: Secret generation with preserve-existing semantics for the bootstrap,
      identity services. App secrets are declared in manifests.
WHY:  Tokens must be random, mode-0600, and NEVER overwritten once created
      (rotating a live token orphans running services). Callers only learn which keys were ADDED (never values).
RUN:  Imported by ctl/install.py and the runtime project renderer.
DEBUG: State/database/key and generated private env files use mode 600. Values are logged as names only —
      grep any log for a token value and file a bug.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from ctl.platform_apps import by_capability
from ctl.runtime import RuntimePaths
from ctl.secret_file import locked, write_atomic
from ctl.store.secrets import SecretStore

ROOT_ENV_KEYS = ("MU3LAB_CTL_TOKEN", "MU3LAB_INGRESS_TOKEN")


def ensure_authentik_env(
    root: Path, token_factory=None, *, email: str = "", password_hash: str = ""
) -> tuple[Path, list[str]]:
    """Keep stable credentials and supply first-start values before Compose starts."""
    token_factory = token_factory or generate_hex
    target = root / "projects" / by_capability("identity_provider").id / ".env"
    with locked(target.with_suffix(".lock")):
        values = read_runtime_env(target)
        store = SecretStore(RuntimePaths(root))
        scope = "app:" + by_capability("identity_provider").id
        added: list[str] = []
        for key in ("AUTHENTIK_SECRET_KEY", "AUTHENTIK_POSTGRESQL__PASSWORD", "AUTHENTIK_BOOTSTRAP_TOKEN"):
            saved = store.get(scope, "env:" + key)
            if saved:
                values[key] = saved
            elif not values.get(key):
                values[key] = token_factory()
                added.append(key)
            if saved is None:
                store.put(scope, "env:" + key, values[key])
        if email and password_hash:
            for key, value in (
                ("AUTHENTIK_BOOTSTRAP_EMAIL", email),
                ("AUTHENTIK_BOOTSTRAP_PASSWORD_HASH", password_hash),
            ):
                if not values.get(key):
                    values[key] = value
                    added.append(key)
                    # Canonical too, so a render before first start keeps it.
                    store.put(scope, "env:" + key, value)
        values.pop("AUTHENTIK_TAG", None)
        store.delete(scope, "env:AUTHENTIK_TAG")
        write_atomic(target, runtime_env_text(values).encode())
    return target, added


def clear_authentik_bootstrap(root: Path) -> None:
    target = root / "projects" / by_capability("identity_provider").id / ".env"
    with locked(target.with_suffix(".lock")):
        values = read_runtime_env(target)
        store = SecretStore(RuntimePaths(root))
        for key in ("AUTHENTIK_BOOTSTRAP_EMAIL", "AUTHENTIK_BOOTSTRAP_PASSWORD_HASH"):
            values.pop(key, None)
            store.delete("app:" + by_capability("identity_provider").id, "env:" + key)
        write_atomic(target, runtime_env_text(values).encode())


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


def generate_hex(nbytes: int = 32) -> str:
    """Random hex token (injectable indirection over secrets for tests)."""
    return secrets.token_hex(nbytes)


def platform_values(paths: RuntimePaths = RuntimePaths()) -> dict[str, str]:
    return SecretStore(paths).get("platform", "tokens") or {}


def ensure_platform_tokens(
    paths: RuntimePaths = RuntimePaths(), token_factory=generate_hex
) -> tuple[dict[str, str], list[str]]:
    with locked(paths.state / "platform.lock"):
        values = platform_values(paths)
        added = []
        for key in ROOT_ENV_KEYS:
            if not values.get(key):
                values[key] = token_factory()
                added.append(key)
        if added:
            SecretStore(paths).put("platform", "tokens", values)
    return values, added
