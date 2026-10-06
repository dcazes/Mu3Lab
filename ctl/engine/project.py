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
import json
import os
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from ctl import hostinfo
from ctl.engine import template
from ctl.engine.launch import caddy_handler
from ctl.manifest.catalog import App, Catalog
from ctl.manifest.models import ConfigField, Secret
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl.store.secrets import SecretStore

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


def generate(secret: Secret, env: dict[str, str] | None = None) -> str:
    if secret.kind == "argon2":
        password = (env or {}).get(secret.source_env, "")
        if not password:
            raise ValueError("A source secret is required for an Argon2 admin token.")
        return Argon2id(salt=os.urandom(16), length=32, iterations=3, lanes=4, memory_cost=65536).derive_phc_encoded(
            password.encode()
        )
    if secret.kind == "rsa_jwk":
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_numbers()
        values = {
            "n": private.public_numbers.n,
            "e": private.public_numbers.e,
            "d": private.d,
            "p": private.p,
            "q": private.q,
            "dp": private.dmp1,
            "dq": private.dmq1,
            "qi": private.iqmp,
        }
        encoded = {
            name: base64.urlsafe_b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).decode().rstrip("=")
            for name, value in values.items()
        }
        return json.dumps(
            {"keys": [{"kty": "RSA", "alg": "RS256", "use": "sig", "kid": secrets.token_hex(8), **encoded}]},
            separators=(",", ":"),
        )
    if secret.kind == "fixed":
        return secret.value
    if secret.kind == "hex":
        return secrets.token_hex(secret.length)
    if secret.kind == "base64":
        return base64.b64encode(secrets.token_bytes(secret.length)).decode("ascii")
    return token(secret.length)


def discovery_url(dns_name: str, app_id: str) -> str:
    return f"https://{dns_name}/application/o/mu3lab-{app_id}/.well-known/openid-configuration"


def installed(app: App, paths: RuntimePaths) -> bool:
    """Core apps are always present; an optional app is installed while its project exists."""
    return app.manifest.tier != "optional" or (paths.projects / app.id / "docker-compose.yml").is_file()


def provider(capability: str, facts: Facts, exclude: str = "") -> App | None:
    """The installed app that provides ``capability`` (one at most is honoured)."""
    return next(
        (
            other
            for other in facts.catalog.apps
            if capability in other.manifest.capabilities and other.id != exclude and installed(other, facts.paths)
        ),
        None,
    )


def _integrations(app: App, facts: Facts, env: dict[str, str]) -> None:
    for integration in app.manifest.integrates_with:
        source = provider(integration.capability, facts, exclude=app.id)
        values = integration.env if source else integration.absent_env
        if not source:
            # Settings that only made sense with the provider must not outlive it.
            for name in set(integration.env) - set(integration.absent_env):
                env.pop(name, None)
        provided = read_runtime_env(facts.paths.projects / source.id / ".env") if source else {}
        base = lookup_for(app, facts, env)

        def lookup(name: str, base: template.Lookup = base, provided: dict[str, str] = provided) -> str | None:
            if name.startswith("provider:"):
                return provided.get(name.split(":", 1)[1]) or None
            return base(name)

        for name, value in values.items():
            try:
                env[name] = template.render(value, lookup)
            except template.TemplateError:
                env.pop(name, None)  # the provider has not published that setting yet


def _config_default(field: ConfigField) -> str | None:
    if field.default is None:
        return None
    if isinstance(field.default, bool):
        return "true" if field.default else "false"
    return str(field.default)


def _rendered_default(field: ConfigField, default: str, lookup: template.Lookup) -> str | None:
    """A default may name a fact (``{{country_code}}``); one that is unknown or not an allowed choice is left unset."""
    try:
        value = template.render(default, lookup)
    except template.TemplateError:
        return None
    if field.type == "enum" and value not in field.options:
        return None
    return value


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
            "country_code": hostinfo.country_code(env.get("TZ")) or None,
        }
        if name in known:
            return known[name]
        return env.get(name)

    return lookup


def build_env(app: App, facts: Facts, existing: dict[str, str], hooks: list[EnvHook]) -> dict[str, str]:
    manifest = app.manifest
    env = dict(existing)
    env["MU3LAB_DATA_ROOT"] = str(facts.paths.data)
    for folder in manifest.media:
        env[folder.env] = str(facts.paths.media / folder.name)
    # Assigned, not defaulted: apps follow the computer if its timezone changes.
    env["TZ"] = hostinfo.timezone()
    store = SecretStore(facts.paths)
    scope = "app:" + app.id
    for secret in manifest.secrets:
        saved = store.get(scope, "env:" + secret.env)
        env[secret.env] = saved or env.get(secret.env) or generate(secret, env)
        if saved is None:
            store.put(scope, "env:" + secret.env, env[secret.env])
    oidc = manifest.sign_in.oidc
    if oidc:
        env[oidc.env.client_id] = oidc.client_id
        saved = store.get(scope, "env:" + oidc.env.client_secret)
        env[oidc.env.client_secret] = saved or env.get(oidc.env.client_secret) or token(OIDC_SECRET_LENGTH)
        if saved is None:
            store.put(scope, "env:" + oidc.env.client_secret, env[oidc.env.client_secret])
        if oidc.env.discovery_url and facts.dns_name:
            env[oidc.env.discovery_url] = discovery_url(facts.dns_name, app.id)
    for field in manifest.configuration:
        default = _config_default(field)
        if default is not None and field.env not in env:
            default = _rendered_default(field, default, lookup_for(app, facts, env))
            if default is not None:
                env[field.env] = default
    for hook in hooks:
        hook(app, env)
    _integrations(app, facts, env)
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


def _media_folders(app: App, facts: Facts) -> None:
    """Create the app's libraries as this (unprivileged) user, so people can add files to them.

    Docker would otherwise create a missing bind-mount source owned by root.
    Existing folders are left exactly as they are.
    """
    for folder in app.manifest.media:
        (facts.paths.media / folder.name).mkdir(mode=0o775, parents=True, exist_ok=True)


def render(app: App, facts: Facts, hooks: list[EnvHook] | None = None) -> Path:
    """Write the app's runtime project and return its directory."""
    target = facts.paths.projects / app.id
    _media_folders(app, facts)
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
