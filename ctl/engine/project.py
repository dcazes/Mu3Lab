"""Turn an app folder into its runtime Compose project under ``/srv/mu3lab/projects/<id>``.

The folder's files are copied, then the private ``.env`` is built from the
app's canonical settings (``ctl.app_settings``), in this order: stored managed
values; missing secrets are generated once; the OIDC client settings are
assigned; configuration defaults, then the operator's configuration; each
rule adds what it owns; integrations; templated ``env`` values are assigned
from current facts. Files ending in ``.tmpl`` are rendered with the same
values. ``.env`` is an output only: it is never read back as input, and
deleting it and rendering again reproduces it (R16).

Every output is computed before any is written, and each is published
privately (0600 from its first byte) and atomically. Rendering holds the
app's resource lock. Nothing here changes Docker or another app; the one
thing it asks Docker (the connector networks ContextForge may reach) is
read-only.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from ctl import hostinfo, process, resource_locks
from ctl.app_settings import AppSettings, fixed_names
from ctl.compute import build, gpu_build
from ctl.engine import template
from ctl.engine.launch import caddy_handler
from ctl.manifest.catalog import App, Catalog
from ctl.manifest.models import ConfigField, Secret
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl.store.secrets import SecretStore

# Files that describe the app to Mu3Lab rather than run it.
NOT_COPIED = frozenset({"app.yaml", "hooks.py", "connectors", "scripts", "__pycache__"})
# A developer's environments and caches, at any depth (a voice agent's tests leave a .venv).
DEV_FOLDERS = ("__pycache__", ".venv", "node_modules")
TEMPLATE_SUFFIX = ".tmpl"
# In each runtime project: the files the last copy took from the app's folder, so a file the
# app later drops (an old setup's Compose override) is removed instead of staying in use.
SHIPPED_RECORD = ".mu3lab-shipped"
OIDC_SECRET_LENGTH = 40
# Rendering waits this long for a job that holds the app.
RENDER_LOCK_SECONDS = 600
TAILNET_HOST = "MU3LAB_TAILNET_HOST"

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


def _config_default(field: ConfigField, gpu: bool = False) -> str | None:
    default = field.gpu_default if gpu and field.gpu_default is not None else field.default
    if default is None:
        return None
    if isinstance(default, bool):
        return "true" if default else "false"
    return str(default)


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

    def route_url(self, app: App, name: str) -> str:
        """The private address of one of the app's ``extra_routes``."""
        route = next((extra for extra in app.manifest.extra_routes if extra.name == name), None)
        if not self.dns_name or route is None:
            return ""
        return f"https://{self.dns_name}:{route.https_port}"

    @cached_property
    def tailnet_ip(self) -> str:
        """Asked of Tailscale once per render, and only by an app whose settings name it."""
        from ctl.service_state import tailnet_ip

        return tailnet_ip()

    @cached_property
    def home_address(self):
        """This server's home-network address, asked once per render and only by an app on the home network."""
        from ctl.service_state import home_address

        return home_address()

    @cached_property
    def media_folders(self) -> str:
        """The libraries under the media root, comma-separated; "none" while there are none."""
        media = self.paths.media
        names = sorted(item.name for item in media.iterdir() if item.is_dir()) if media.is_dir() else []
        return ",".join(names) or "none"

    @cached_property
    def connector_networks(self) -> str:
        """Asked of Docker once per render, and only by an app whose settings name it (ContextForge)."""
        from ctl.service_state import connector_networks

        return connector_networks()


def lookup_for(app: App, facts: Facts, env: dict[str, str]) -> template.Lookup:
    """Resolve ``{{name}}`` for this app; None for names that cannot be known yet."""

    def lookup(name: str) -> str | None:
        if name == "tailnet_ip":
            return facts.tailnet_ip or None
        if name in {"home_ip", "home_network"}:
            home = facts.home_address
            return (home.ip if name == "home_ip" else home.network) if home else None
        if name == "connector_networks":
            return facts.connector_networks or None
        if name.startswith("route_url:"):
            return facts.route_url(app, name.split(":", 1)[1]) or None
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
            "media_root": str(facts.paths.media),
            "media_folders": facts.media_folders,
            "tz": env.get("TZ") or None,
            "country_code": hostinfo.country_code(env.get("TZ")) or None,
            "currency_code": hostinfo.currency_code(hostinfo.country_code(env.get("TZ"))),
        }
        if name in known:
            return known[name]
        return env.get(name)

    return lookup


def build_env(
    app: App,
    facts: Facts,
    existing: dict[str, str],
    hooks: list[EnvHook],
    config: dict[str, str] | None = None,
) -> dict[str, str]:
    """``existing`` holds the stored managed values; ``config`` the operator's, by field key."""
    manifest = app.manifest
    env = dict(existing)
    env["MU3LAB_DATA_ROOT"] = str(facts.paths.data)
    if facts.dns_name:
        # Containers resolve this server's private name to the tailnet relay (see platform/tailnet-relay).
        env[TAILNET_HOST] = facts.dns_name
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
    # Asked only when a field has a GPU default: finding the build may ask the graphics driver.
    gpu = any(field.gpu_default is not None for field in manifest.configuration) and gpu_build(
        app.folder, facts.paths.projects / app.id, manifest.service.uses_gpu
    )
    for field in manifest.configuration:
        chosen = (config or {}).get(field.key)
        if chosen:
            env[field.env] = chosen
            continue
        default = _config_default(field, gpu)
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
    if manifest.build_env:
        mode = build(app.folder, facts.paths.projects / app.id, manifest.service.uses_gpu)
        for name, value in manifest.build_env.get(mode, {}).items():
            env[name] = template.render(value, lookup)
    return env


def _copied(item: Path) -> bool:
    """Whether a top-level item of the app's folder goes into its runtime project as it is."""
    if item.name in NOT_COPIED or item.name.startswith(".env") or item.is_symlink():
        return False
    return item.suffix != TEMPLATE_SUFFIX


def shipped_files(source: Path) -> list[Path]:
    """Every file ``_copy_folder`` copies from the app's folder, relative to it.

    Updates compare these with the installed project, so a change Mu3Lab makes to an app's
    own files (LiveKit's voice agent, say) is offered as an update like a new release.
    """
    files: list[Path] = []
    for item in sorted(source.iterdir()):
        if not _copied(item):
            continue
        if item.is_dir():
            for path in sorted(item.rglob("*")):
                relative = path.relative_to(source)
                if path.is_file() and not any(part in DEV_FOLDERS for part in relative.parts):
                    files.append(relative)
        elif item.is_file():
            files.append(Path(item.name))
    return files


def _outputs(source: Path) -> set[str]:
    """Everything the app's folder puts in its project: copied files and rendered templates."""
    rendered = {path.name.removesuffix(TEMPLATE_SUFFIX) for path in source.glob(f"*{TEMPLATE_SUFFIX}")}
    return {str(path) for path in shipped_files(source)} | rendered


@cache
def _dropped_in_history(source: Path) -> frozenset[str]:
    """Files the checkout's history says the app's folder once had and deleted.

    Only for projects copied before ``SHIPPED_RECORD`` existed; without git it finds nothing.
    Kept for the process's life: the dashboard asks on every refresh, and a project gets its
    record on its next copy.
    """
    try:
        proc = process.completed(
            ["git", "log", "--diff-filter=D", "--name-only", "--format=", "--relative", "--", "."],
            cwd=source,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if proc.returncode:
        return frozenset()
    found = set()
    for line in proc.stdout.splitlines():
        relative = Path(line.strip())
        if not line.strip() or any(part in DEV_FOLDERS for part in relative.parts):
            continue
        top = relative.parts[0]
        if top in NOT_COPIED or top.startswith(".env"):
            continue
        found.add(str(relative).removesuffix(TEMPLATE_SUFFIX) if len(relative.parts) == 1 else str(relative))
    return frozenset(found)


def retired_files(source: Path, target: Path) -> list[str]:
    """Files an earlier copy put in the project that the app's folder no longer has."""
    record = target / SHIPPED_RECORD
    try:
        before: set[str] | frozenset[str] = set(record.read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        before = _dropped_in_history(source)
    return sorted(name for name in before - _outputs(source) if name and (target / name).is_file())


def _copy_folder(source: Path, target: Path) -> None:
    target.mkdir(mode=0o750, parents=True, exist_ok=True)
    for name in retired_files(source, target):
        (target / name).unlink()
        # A folder the app no longer ships at all goes with its last file.
        for folder in (target / name).parents:
            if folder == target or any(folder.iterdir()):
                break
            folder.rmdir()
    for item in source.iterdir():
        if not _copied(item):
            continue
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*DEV_FOLDERS))
        else:
            shutil.copy2(item, destination)
    write_atomic(target / SHIPPED_RECORD, "".join(f"{name}\n" for name in sorted(_outputs(source))).encode("utf-8"))


def _write_private(path: Path, text: str) -> None:
    write_atomic(path, text.encode("utf-8"))


def _media_folders(app: App, facts: Facts) -> None:
    """Create the app's libraries as this (unprivileged) user, so people can add files to them.

    Docker would otherwise create a missing bind-mount source owned by root.
    Existing folders are left exactly as they are.
    """
    for folder in app.manifest.media:
        (facts.paths.media / folder.name).mkdir(mode=0o775, parents=True, exist_ok=True)


def render(app: App, facts: Facts, hooks: list[EnvHook] | None = None, *, copy_files: bool = True) -> Path:
    """Write the app's runtime project and return its directory.

    ``copy_files=False`` refreshes only settings and templates: re-wiring an
    app for another app's arrival must not slip in newer deployment files
    from the checkout outside its own backed-up update (R12).
    """
    target = facts.paths.projects / app.id
    with resource_locks.hold(f"app:{app.id}", timeout=RENDER_LOCK_SECONDS, paths=facts.paths):
        settings = AppSettings(app.id, facts.paths)
        env_path = target / ".env"
        settings.ensure_imported(app.manifest, env_path)
        env = build_env(app, facts, settings.generated(), hooks or [], settings.config())
        # Keep the existing line order, so an unchanged render is byte-for-byte unchanged;
        # new names follow in sorted order, so a fresh render is deterministic too.
        order = [name for name in read_runtime_env(env_path) if name in env]
        env = {name: env[name] for name in order} | {name: env[name] for name in sorted(env) if name not in order}
        # Stage every output first: a failure here publishes nothing.
        outputs = {env_path: runtime_env_text(env)}
        lookup = lookup_for(app, facts, env)
        for source in sorted(app.folder.glob(f"*{TEMPLATE_SUFFIX}")):
            try:
                text = template.render(source.read_text(encoding="utf-8"), lookup)
            except template.TemplateError:
                continue  # a fact is not known yet; the previous output stays until it is
            outputs[target / source.name.removesuffix(TEMPLATE_SUFFIX)] = text
        configured = {field.env for field in app.manifest.configuration}
        settings.replace_generated(
            {name: value for name, value in env.items() if name not in fixed_names(app.manifest) | configured}
        )
        _media_folders(app, facts)
        if copy_files or not (target / "docker-compose.yml").is_file():
            _copy_folder(app.folder, target)
        for path, text in outputs.items():
            _write_private(path, text)
    return target
