"""Generate an application's runtime Compose project under /srv/mu3lab/projects.

The checked-in definition under apps/<id> is copied, then a private `.env` is
filled with generated secrets and tailnet URLs. Existing values are never
overwritten, so reconnecting to existing data keeps its credentials.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
from collections.abc import Callable
from pathlib import Path

import yaml

from ctl import actions
from ctl.registry import Service
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text

Values = dict[str, str]


def _token(length: int) -> str:
    # Secrets reach app CLIs as separate arguments (Nextcloud's entrypoint runs
    # `occ maintenance:install --database-pass $POSTGRES_PASSWORD`), where a
    # leading "-" is parsed as an option. token_urlsafe starts with one 1 in 64.
    while True:
        token = secrets.token_urlsafe(length)
        if not token.startswith("-"):
            return token


def _database_and_oidc(
    password_key: str, secret_key: str, prefix: str, client_id: str
) -> Callable[[Values, Path], None]:
    def generate(values: Values, _root: Path) -> None:
        values.setdefault(password_key, _token(36))
        values.setdefault(secret_key, _token(48))
        values.setdefault(f"{prefix}_CLIENT_ID", client_id)
        values.setdefault(f"{prefix}_CLIENT_SECRET", _token(40))

    return generate


def _immich_secrets(values: Values, _root: Path) -> None:
    values.setdefault("DB_PASSWORD", _token(36))
    values.setdefault("DB_USERNAME", "postgres")
    values.setdefault("DB_DATABASE_NAME", "immich")
    values.setdefault("IMMICH_OIDC_CLIENT_ID", "mu3lab-immich")
    values.setdefault("IMMICH_OIDC_CLIENT_SECRET", _token(40))


def _nextcloud_secrets(values: Values, _root: Path) -> None:
    values.setdefault("NEXTCLOUD_DB_PASSWORD", _token(36))
    values.setdefault("NEXTCLOUD_REDIS_PASSWORD", _token(36))
    values.setdefault("NEXTCLOUD_OIDC_CLIENT_ID", "mu3lab-nextcloud")
    values.setdefault("NEXTCLOUD_OIDC_CLIENT_SECRET", _token(40))


def _surfsense_secrets(values: Values, _root: Path) -> None:
    values.setdefault("DB_USER", "surfsense")
    values.setdefault("DB_NAME", "surfsense")
    values.setdefault("DB_PASSWORD", _token(36))
    values.setdefault("SECRET_KEY", _token(48))
    values.setdefault("ZERO_ADMIN_PASSWORD", _token(36))
    values.setdefault("SEARXNG_SECRET", _token(36))
    values.setdefault("AUTH_TYPE", "LOCAL")
    values.setdefault("REGISTRATION_ENABLED", "TRUE")
    values.setdefault("SANDBOX_ENABLED", "FALSE")
    values.setdefault("EMBEDDING_MODEL", "litellm://ollama/nomic-embed-text")
    values.setdefault("EMBEDDING_BASE_URL", "http://ollama:11434")
    # v0.0.40 also reads this legacy spelling in selected code paths.
    values.setdefault("EMBEDDING_API_BASE_URL", "http://ollama:11434")


def _firecrawl_secrets(values: Values, _root: Path) -> None:
    values.setdefault("POSTGRES_PASSWORD", _token(36))
    values.setdefault("RABBITMQ_DEFAULT_PASS", _token(36))
    values.setdefault("BULL_AUTH_KEY", _token(36))


def _lobehub_secrets(values: Values, root: Path) -> None:
    from ctl.lobehub_ops import apply_model_policy

    values.setdefault("POSTGRES_PASSWORD", _token(36))
    values.setdefault("AUTH_SECRET", _token(48))
    # LobeHub requires base64 AES key material of 16, 24, or 32 bytes.
    values.setdefault("KEY_VAULTS_SECRET", base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
    values.setdefault("RUSTFS_ACCESS_KEY", "mu3lab-lobehub")
    values.setdefault("RUSTFS_SECRET_KEY", _token(48))
    values.setdefault("AUTH_AUTHENTIK_ID", "mu3lab-lobehub")
    values.setdefault("AUTH_AUTHENTIK_SECRET", _token(40))
    litellm = read_runtime_env(RuntimePaths().projects / "litellm" / ".env")
    if litellm.get("LITELLM_MASTER_KEY"):
        values.setdefault("LITELLM_MASTER_KEY", litellm["LITELLM_MASTER_KEY"])
    apply_model_policy(values, root)


def _baby_buddy_secrets(values: Values, _root: Path) -> None:
    values.setdefault("BABY_BUDDY_SECRET_KEY", _token(48))


GENERATED_SECRETS: dict[str, Callable[[Values, Path], None]] = {
    "immich": _immich_secrets,
    "adventurelog": _database_and_oidc("POSTGRES_PASSWORD", "SECRET_KEY", "ADVENTURELOG_OIDC", "mu3lab-adventurelog"),
    "paperless-ngx": _database_and_oidc(
        "PAPERLESS_DBPASS", "PAPERLESS_SECRET_KEY", "PAPERLESS_OIDC", "mu3lab-paperless-ngx"
    ),
    "nextcloud": _nextcloud_secrets,
    "surfsense": _surfsense_secrets,
    "firecrawl": _firecrawl_secrets,
    "lobehub": _lobehub_secrets,
    "baby-buddy": _baby_buddy_secrets,
}


def _discovery_url(dns_name: str, service_id: str) -> str:
    return f"https://{dns_name}/application/o/mu3lab-{service_id}/.well-known/openid-configuration"


def _register_oidc_client(
    service: Service,
    dns_name: str,
    name: str,
    client_id: str,
    client_secret: str,
    redirect_paths: tuple[str, ...],
    initial_owner: str = "",
) -> None:
    from ctl.authentik_blueprints import write_oidc_application_blueprint

    write_oidc_application_blueprint(
        RuntimePaths().root,
        dns_name,
        service_id=service.id,
        name=name,
        private_port=service.private_https_port,
        client_id=client_id,
        client_secret=client_secret,
        redirect_paths=redirect_paths,
        initial_owner=initial_owner,
    )


def _mealie_urls(service: Service, values: Values, dns_name: str, public_url: str, _target: Path) -> None:
    values.setdefault("MEALIE_BASE_URL", public_url)
    values.setdefault("MEALIE_OIDC_ENABLED", "true")
    values.setdefault("MEALIE_OIDC_SIGNUP_ENABLED", "true")
    values.setdefault("MEALIE_OIDC_CLIENT_ID", "mu3lab-mealie")
    values.setdefault("MEALIE_OIDC_CLIENT_SECRET", _token(40))
    values.setdefault("MEALIE_OIDC_CONFIGURATION_URL", _discovery_url(dns_name, "mealie"))
    values.setdefault("MEALIE_OIDC_AUTO_REDIRECT", "true")
    values.setdefault("MEALIE_ALLOW_SIGNUP", "false")
    values.setdefault("MEALIE_OIDC_REMEMBER_ME", "true")
    values.setdefault("MEALIE_ALLOW_PASSWORD_LOGIN", "true")
    _register_oidc_client(
        service,
        dns_name,
        "Mealie",
        values["MEALIE_OIDC_CLIENT_ID"],
        values["MEALIE_OIDC_CLIENT_SECRET"],
        ("/login", "/login?direct=1"),
    )


def _actual_budget_urls(service: Service, values: Values, dns_name: str, public_url: str, _target: Path) -> None:
    values.setdefault("ACTUAL_OPENID_CLIENT_ID", "mu3lab-actual-budget")
    values.setdefault("ACTUAL_OPENID_CLIENT_SECRET", _token(40))
    values.setdefault("ACTUAL_OPENID_DISCOVERY_URL", _discovery_url(dns_name, "actual-budget"))
    values.setdefault("ACTUAL_OPENID_SERVER_HOSTNAME", public_url)
    values.setdefault("ACTUAL_OPENID_ENFORCE", "true")
    values.setdefault("ACTUAL_USER_CREATION_MODE", "login")
    _register_oidc_client(
        service,
        dns_name,
        "Actual Budget",
        values["ACTUAL_OPENID_CLIENT_ID"],
        values["ACTUAL_OPENID_CLIENT_SECRET"],
        ("/openid/callback",),
        initial_owner=values.get("MU3LAB_INITIAL_OWNER_USERNAME", ""),
    )


def _adventurelog_urls(service: Service, values: Values, dns_name: str, public_url: str, _target: Path) -> None:
    values.setdefault("SITE_URL", public_url)
    values.setdefault("ADVENTURELOG_OIDC_DISCOVERY_URL", _discovery_url(dns_name, "adventurelog"))
    values.setdefault("ADVENTURELOG_FORCE_SOCIAL_LOGIN", "false")
    _register_oidc_client(
        service,
        dns_name,
        "AdventureLog",
        values["ADVENTURELOG_OIDC_CLIENT_ID"],
        values["ADVENTURELOG_OIDC_CLIENT_SECRET"],
        ("/accounts/oidc/mu3lab-adventurelog/login/callback/",),
    )


def _paperless_urls(service: Service, values: Values, dns_name: str, public_url: str, _target: Path) -> None:
    values.setdefault("PAPERLESS_URL", public_url)
    values.setdefault("PAPERLESS_APPS", "allauth.socialaccount.providers.openid_connect")
    values.setdefault(
        "PAPERLESS_SOCIALACCOUNT_PROVIDERS",
        json.dumps(
            {
                "openid_connect": {
                    "APPS": [
                        {
                            "provider_id": "authentik",
                            "name": "Authentik",
                            "client_id": values["PAPERLESS_OIDC_CLIENT_ID"],
                            "secret": values["PAPERLESS_OIDC_CLIENT_SECRET"],
                            "settings": {"server_url": _discovery_url(dns_name, "paperless-ngx")},
                        }
                    ]
                },
            },
            separators=(",", ":"),
        ),
    )
    values.setdefault("PAPERLESS_DISABLE_REGULAR_LOGIN", "false")
    values.setdefault("PAPERLESS_REDIRECT_LOGIN_TO_SSO", "false")
    providers = json.loads(values["PAPERLESS_SOCIALACCOUNT_PROVIDERS"])
    for app in providers.get("openid_connect", {}).get("APPS", []):
        if app.get("provider_id") == "authentik":
            app.setdefault("settings", {}).update(email_authentication=True)
    values["PAPERLESS_SOCIALACCOUNT_PROVIDERS"] = json.dumps(providers, separators=(",", ":"))
    values.setdefault("PAPERLESS_SOCIAL_AUTO_SIGNUP", "true")
    _register_oidc_client(
        service,
        dns_name,
        "Paperless-ngx",
        values["PAPERLESS_OIDC_CLIENT_ID"],
        values["PAPERLESS_OIDC_CLIENT_SECRET"],
        ("/accounts/oidc/authentik/login/callback/",),
    )


def _immich_urls(service: Service, values: Values, dns_name: str, _public_url: str, target: Path) -> None:
    config = {
        "oauth": {
            "enabled": True,
            "issuerUrl": _discovery_url(dns_name, "immich"),
            "clientId": values["IMMICH_OIDC_CLIENT_ID"],
            "clientSecret": values["IMMICH_OIDC_CLIENT_SECRET"],
            "scope": "openid email profile",
            "signingAlgorithm": "RS256",
            "autoRegister": True,
            "autoLaunch": True,
            "buttonText": "Login with Authentik",
            "roleClaim": "mu3lab_role",
            "mobileOverrideEnabled": False,
            "mobileRedirectUri": "",
        }
    }
    config["passwordLogin"] = {"enabled": values.get("IMMICH_PASSWORD_LOGIN_ENABLED", "true") == "true"}
    (target / "immich-config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(target / "immich-config.json", 0o600)
    _register_oidc_client(
        service,
        dns_name,
        "Immich",
        values["IMMICH_OIDC_CLIENT_ID"],
        values["IMMICH_OIDC_CLIENT_SECRET"],
        ("/auth/login", "/user-settings", "/api/oauth/mobile-redirect"),
    )


def _nextcloud_urls(service: Service, values: Values, dns_name: str, public_url: str, _target: Path) -> None:
    values.setdefault("NEXTCLOUD_TRUSTED_DOMAINS", f"{dns_name} localhost 127.0.0.1")
    values.setdefault("NEXTCLOUD_TRUSTED_PROXIES", "127.0.0.1")
    values.setdefault("NEXTCLOUD_OVERWRITEHOST", f"{dns_name}:{service.private_https_port}")
    values.setdefault("NEXTCLOUD_OVERWRITECLIURL", public_url)
    _register_oidc_client(
        service,
        dns_name,
        "Nextcloud",
        values["NEXTCLOUD_OIDC_CLIENT_ID"],
        values["NEXTCLOUD_OIDC_CLIENT_SECRET"],
        ("/apps/user_oidc/code",),
    )


def _surfsense_urls(_service: Service, values: Values, _dns_name: str, public_url: str, _target: Path) -> None:
    values.setdefault("SURFSENSE_PUBLIC_URL", public_url)


def _lobehub_urls(service: Service, values: Values, dns_name: str, public_url: str, _target: Path) -> None:
    values.setdefault("APP_URL", public_url)
    values.setdefault("S3_ENDPOINT", public_url + "/lobe-assets")
    values.setdefault("AUTH_SSO_PROVIDERS", "authentik")
    values.setdefault("AUTH_AUTHENTIK_ISSUER", f"https://{dns_name}/application/o/mu3lab-lobehub/")
    values.setdefault("AUTH_DISABLE_EMAIL_PASSWORD", "1")
    values.setdefault("OPENAI_PROXY_URL", "http://litellm:4000/v1")
    values["OPENAI_MODEL_LIST"] = "-all,+mu3lab-chat"
    _register_oidc_client(
        service,
        dns_name,
        "LobeChat",
        values["AUTH_AUTHENTIK_ID"],
        values["AUTH_AUTHENTIK_SECRET"],
        ("/api/auth/callback/authentik",),
    )


def _baby_buddy_urls(_service: Service, values: Values, _dns_name: str, public_url: str, _target: Path) -> None:
    values.setdefault("BABY_BUDDY_PUBLIC_URL", public_url)


TAILNET_SETTINGS: dict[str, Callable[[Service, Values, str, str, Path], None]] = {
    "mealie": _mealie_urls,
    "actual-budget": _actual_budget_urls,
    "adventurelog": _adventurelog_urls,
    "paperless-ngx": _paperless_urls,
    "immich": _immich_urls,
    "nextcloud": _nextcloud_urls,
    "surfsense": _surfsense_urls,
    "lobehub": _lobehub_urls,
    "baby-buddy": _baby_buddy_urls,
}


def _copy_definition(source: Path, target: Path) -> None:
    target.mkdir(mode=0o750, parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name.startswith(".env") or item.name == "__pycache__" or item.is_symlink():
            continue
        destination = target / item.name
        if item.is_file():
            shutil.copy2(item, destination)
        elif item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".git"))


def materialize(service: Service, root: Path) -> Path:
    target = RuntimePaths().projects / service.id
    _copy_definition(service.compose_path(root), target)
    if service.id == "lobehub":
        from ctl.login_launch import caddy_handler

        caddy = target / "Caddyfile"
        caddy.write_text(caddy.read_text().replace("\t# MU3LAB_LOGIN", caddy_handler(service.id)), encoding="utf-8")
    env_path = target / ".env"
    values = read_runtime_env(env_path)
    values.setdefault("MU3LAB_DATA_ROOT", str(RuntimePaths().data))
    if generate := GENERATED_SECRETS.get(service.id):
        generate(values, root)
    try:
        from ctl.service_state import tailnet_dns_name

        dns_name = tailnet_dns_name()
    except (OSError, ValueError):
        dns_name = ""
    configure = TAILNET_SETTINGS.get(service.id)
    if configure and dns_name and service.private_https_port:
        configure(service, values, dns_name, f"https://{dns_name}:{service.private_https_port}", target)
    env_path.write_text(runtime_env_text(values), encoding="utf-8")
    os.chmod(env_path, 0o600)
    return target


def pin_images(project: Path) -> dict[str, str]:
    """Resolve every declared image and write a Compose digest override."""
    definition = yaml.safe_load((project / "docker-compose.yml").read_text(encoding="utf-8")) or {}
    snapshot: dict[str, str] = {}
    override: dict[str, dict[str, dict[str, str]]] = {"services": {}}
    for service_name, contract in (definition.get("services") or {}).items():
        if not isinstance(contract, dict) or not contract.get("image"):
            continue
        image = str(contract["image"])
        if "@sha256:" in image:
            pinned = image
        else:
            rc, output = actions.docker_image_digest(image)
            pinned = output.strip()
            if rc or "@sha256:" not in pinned:
                raise RuntimeError(f"image {service_name} did not resolve to an immutable digest")
        snapshot[str(service_name)] = pinned
        override["services"][str(service_name)] = {"image": pinned}
    if not snapshot:
        raise RuntimeError("the curated deployment did not declare any images")
    target = project / "docker-compose.digest.yml"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(override, sort_keys=True), encoding="utf-8")
    temporary.replace(target)
    return snapshot
