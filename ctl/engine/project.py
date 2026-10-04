"""Turn an app folder into its runtime Compose project under ``/srv/mu3lab/projects/<id>``.

The folder's files are copied, then the private ``.env`` is built from the
manifest, in this order: existing values are kept; missing secrets are
generated once; the OIDC client settings are assigned; configuration
defaults fill gaps; each rule adds what it owns; templated ``env`` values are
assigned from current facts. Files ending in ``.tmpl`` are rendered with the
same values. Nothing here talks to Docker or another app.
"""

from __future__ import annotations

import base64
import os
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ctl import hostinfo
from ctl.engine import template
from ctl.engine.launch import caddy_handler
from ctl.manifest.catalog import App, Catalog
from ctl.manifest.models import ConfigField, Secret
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text

# Files that describe the app to Mu3Lab rather than run it.
NOT_COPIED = frozenset({"app.yaml", "hooks.py", "connectors", "scripts", "__pycache__"})
TEMPLATE_SUFFIX = ".tmpl"
OIDC_SECRET_LENGTH = 40

EnvHook = Callable[[App, dict[str, str]], None]


def token(length: int) -> str:
    """A URL-safe secret that never starts with "-" (app CLIs would read it as an option)."""
    while True:
        value = secrets.token_urlsafe(length)
        if not value.startswith("-"):
            return value


def generate(secret: Secret) -> str:
    if secret.kind == "fixed":
        return secret.value
    if secret.kind == "base64":
        return base64.b64encode(secrets.token_bytes(secret.length)).decode("ascii")
    return token(secret.length)


def discovery_url(dns_name: str, app_id: str) -> str:
    return f"https://{dns_name}/application/o/mu3lab-{app_id}/.well-known/openid-configuration"


def _config_default(field: ConfigField) -> str | None:
    if field.default is None:
        return None
    if isinstance(field.default, bool):
        return "true" if field.default else "false"
    return str(field.default)


@dataclass(frozen=True)
class Facts:
    """What generated settings may depend on, gathered once per render."""

    dns_name: str
    paths: RuntimePaths
    catalog: Catalog

    def public_url(self, app: App) -> str:
        route = app.manifest.route
        if not self.dns_name or route is None:
            return ""
        port = "" if route.https_port == 443 else f":{route.https_port}"
        return f"https://{self.dns_name}{port}"


def lookup_for(app: App, facts: Facts, env: dict[str, str]) -> template.Lookup:
    """Resolve ``{{name}}`` for this app; None for names that cannot be known yet."""

    def lookup(name: str) -> str | None:
        if name.startswith("app:"):
            _, other, key = ([*name.split(":", 2), "", ""])[:3]
            if other not in facts.catalog:
                return None
            return read_runtime_env(facts.paths.projects / other / ".env").get(key) or None
        public = facts.public_url(app)
        known = {
            "public_url": public or None,
            "sign_in_launch": caddy_handler(app.manifest),
            "origin": public or None,
            "dns_name": facts.dns_name or None,
            "https_port": str(app.manifest.route.https_port) if app.manifest.route else None,
            "discovery_url": discovery_url(facts.dns_name, app.id) if facts.dns_name else None,
            "data_root": str(facts.paths.data),
            "tz": env.get("TZ") or None,
        }
        if name in known:
            return known[name]
        return env.get(name)

    return lookup


def build_env(app: App, facts: Facts, existing: dict[str, str], hooks: list[EnvHook]) -> dict[str, str]:
    manifest = app.manifest
    env = dict(existing)
    env["MU3LAB_DATA_ROOT"] = str(facts.paths.data)
    # Assigned, not defaulted: apps follow the computer if its timezone changes.
    env["TZ"] = hostinfo.timezone()
    for secret in manifest.secrets:
        if not env.get(secret.env):
            env[secret.env] = generate(secret)
    oidc = manifest.sign_in.oidc
    if oidc:
        env[oidc.env.client_id] = oidc.client_id
        if not env.get(oidc.env.client_secret):
            env[oidc.env.client_secret] = token(OIDC_SECRET_LENGTH)
        if oidc.env.discovery_url and facts.dns_name:
            env[oidc.env.discovery_url] = discovery_url(facts.dns_name, app.id)
    for field in manifest.configuration:
        default = _config_default(field)
        if default is not None and field.env not in env:
            env[field.env] = default
    for hook in hooks:
        hook(app, env)
    lookup = lookup_for(app, facts, env)
    for name, value in manifest.env.items():
        try:
            env[name] = template.render_value(value, lookup)
        except template.TemplateError:
            # A fact such as the private address is not known yet (Tailscale not
            # joined); keep any earlier value and fill it in on the next render.
            continue
    return env


def _copy_folder(source: Path, target: Path) -> None:
    target.mkdir(mode=0o750, parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name in NOT_COPIED or item.name.startswith(".env") or item.is_symlink():
            continue
        if item.suffix == TEMPLATE_SUFFIX:
            continue
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, destination)


def _write_private(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def render(app: App, facts: Facts, hooks: list[EnvHook] | None = None) -> Path:
    """Write the app's runtime project and return its directory."""
    target = facts.paths.projects / app.id
    _copy_folder(app.folder, target)
    env_path = target / ".env"
    env = build_env(app, facts, read_runtime_env(env_path), hooks or [])
    _write_private(env_path, runtime_env_text(env))
    lookup = lookup_for(app, facts, env)
    for source in sorted(app.folder.glob(f"*{TEMPLATE_SUFFIX}")):
        try:
            text = template.render(source.read_text(encoding="utf-8"), lookup)
        except template.TemplateError:
            continue
        _write_private(target / source.name.removesuffix(TEMPLATE_SUFFIX), text)
    return target
