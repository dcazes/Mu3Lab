"""What Docker Compose needs to start each app, for the installer and every later start.

Most apps start from their generated project with nothing extra. Two
foundation apps need values the installer used to pass only itself, so any
other start (a backup, a restore) brought them up wrong:

- the sign-in service (Authentik) reads its generated settings file and its
  database password from the environment, and runs from the checked-in
  ``apps/authentik`` folder; without them it does not start at all;
- the password vault (Vaultwarden) needs its private HTTPS address and the
  ``docker-compose.tailnet.yml`` override; without them it starts but forgets
  that address.

Values are passed in the process environment, never on a command line or in
a log.
"""

from __future__ import annotations

import re
from pathlib import Path

from ctl.compute import compose_overrides
from ctl.registry import Service
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

VAULTWARDEN_SERVE_PORT = "8443"


def vault_domain(dns_name: str) -> str:
    """Vaultwarden's private URL on the tailnet, or "" for a local or bare name."""
    name = dns_name.rstrip(".")
    if not re.fullmatch(r"[A-Za-z0-9.-]+\.ts\.net", name):
        return ""
    return f"https://{name}:{VAULTWARDEN_SERVE_PORT}"


def _tailnet_name() -> str:
    from ctl.service_state import tailnet_dns_name

    try:
        return tailnet_dns_name()
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def identity_env(paths: RuntimePaths | None = None, env_file: Path | None = None) -> dict[str, str]:
    """Authentik's Compose values, read from the settings file the installer generated."""
    paths = paths or RuntimePaths()
    env_file = env_file or paths.projects / "authentik" / ".env"
    generated = read_runtime_env(env_file)
    if not generated.get("AUTHENTIK_POSTGRESQL__PASSWORD"):
        raise RuntimeError("Authentik's generated settings are missing; run ./install.sh to repair them.")
    return {
        "AUTHENTIK_ENV_FILE": str(env_file),
        "AUTHENTIK_POSTGRESQL__PASSWORD": generated["AUTHENTIK_POSTGRESQL__PASSWORD"],
        "MU3LAB_DATA_ROOT": str(paths.data),
    }


def vault_env(project: Path, dns_name: str, paths: RuntimePaths | None = None) -> tuple[dict[str, str], list[Path]]:
    """Vaultwarden's Compose values and, once the tailnet name is known, its private-URL override."""
    paths = paths or RuntimePaths()
    env = {"MU3LAB_DATA_ROOT": str(paths.data), "MU3LAB_VAULT_ENV_FILE": str(project / ".env")}
    extra: list[Path] = []
    domain = vault_domain(dns_name)
    if domain and (project / "docker-compose.tailnet.yml").is_file():
        env["VAULTWARDEN_DOMAIN"] = domain
        extra.append(project / "docker-compose.tailnet.yml")
    return env, extra


def settings(service: Service, project: Path, paths: RuntimePaths | None = None) -> tuple[dict[str, str], list[Path]]:
    """The environment and extra Compose files for any start or stop of ``service`` in ``project``."""
    capabilities = set(service.manifest.capabilities)
    if "identity_provider" in capabilities:
        return identity_env(paths), compose_overrides(service.id, project)
    if "password_store" in capabilities:
        env, extra = vault_env(project, _tailnet_name(), paths)
        return env, [*extra, *compose_overrides(service.id, project)]
    return {}, compose_overrides(service.id, project)
