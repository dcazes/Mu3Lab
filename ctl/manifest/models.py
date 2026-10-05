"""Typed contents of an app manifest (``apps/<id>/app.yaml``).

One manifest fully describes one app: what it is, how Mu3Lab reaches it, how
people sign in to it, which generated settings it needs, and which shared
rules (``ctl.rules``) apply to it. Every model forbids unknown keys, so a typo
in a manifest fails loudly instead of being ignored.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

APP_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
Port = Annotated[int, Field(ge=1, le=65535)]


def _https(url: HttpUrl) -> HttpUrl:
    if url.scheme != "https":
        raise ValueError("link must use HTTPS")
    return url


# Links people are sent to (app stores, project pages) must never be plain HTTP.
HttpsUrl = Annotated[HttpUrl, AfterValidator(_https)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- How Mu3Lab reaches the app on this computer --------------------------------------


class Health(Model):
    """A loopback probe that says the app is up."""

    kind: Literal["http", "tcp"] = "http"
    path: str = "/"
    port: Port | None = None  # defaults to service.local_port

    @field_validator("path")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("health path must start with /")
        return value


class ServiceSpec(Model):
    local_port: Port
    health: Health = Health()
    start_timeout_seconds: int = Field(default=120, ge=10, le=3600)
    stoppable: bool = True
    uses_gpu: bool = False


# --- Private address (Tailscale Serve → Caddy → app) ------------------------------------


class BlockedPath(Model):
    path: str
    message: str
    status: int = 403


class TokenBypass(Model):
    """Requests carrying their own API token skip the Authentik gate (phone apps)."""

    path: str
    header_prefix: str
    header: str = "Authorization"


class Redirect(Model):
    """Send a path elsewhere on the same address, e.g. away from an app's own password form."""

    path: str
    to: str
    status: Literal[301, 302, 307, 308] = 302


class Route(Model):
    https_port: Port  # Tailscale Serve port on the tailnet hostname
    caddy_port: Port = Field(ge=19460, le=19499)  # loopback Caddy listener
    # open: plain proxy (the app signs people in itself, e.g. native OIDC).
    # gate: Authentik's outpost must admit the visitor first.
    # trusted_header: gate, then Caddy passes the verified username to the app.
    access: Literal["open", "gate", "trusted_header"] = "open"
    copy_identity_headers: tuple[str, ...] = ()
    trusted_header: str = "Remote-User"
    token_bypass: TokenBypass | None = None
    blocked_paths: tuple[BlockedPath, ...] = ()
    redirects: tuple[Redirect, ...] = ()
    forwarded_host: Literal["host", "hostport"] = "hostport"
    # Who Authentik admits through a gate: everyone in the household, or administrators only.
    audience: Literal["household", "operators"] = "household"
    # Foundation routes (dashboard, Authentik, Vaultwarden) are hand-reviewed in
    # apps/ingress/Caddyfile; every other route is generated from its manifest.
    generated: bool = True


# --- Sign-in --------------------------------------------------------------------------


class OidcEnv(Model):
    """Where the app reads its OIDC client settings."""

    client_id: str
    client_secret: str
    discovery_url: str = ""


class JsonLaunch(Model):
    """The app's login page starts sign-in with a JSON POST that returns Authentik's address."""

    path: str
    body: dict[str, Any]  # "{origin}" in a string value becomes the app's private address
    url_key: tuple[str, ...]
    # Serve a same-origin page at the launch path that runs this POST in the
    # browser, for apps whose own login page would otherwise ask first.
    launcher: bool = False
    session_check_path: str = ""  # already signed in? then the launcher just opens the app


class FirstRunCheck(Model):
    """A JSON endpoint whose fields prove the app skipped its own first-run setup."""

    path: str
    expect: dict[str, Any]
    message: str


class Oidc(Model):
    client_id: str
    redirect_paths: tuple[str, ...]
    launch_path: str
    # How a browser starts sign-in: follow redirects, POST JSON, or submit a CSRF form.
    launch: Literal["redirect", "json_post", "csrf_form"] = "redirect"
    json_launch: JsonLaunch | None = None
    env: OidcEnv
    first_run_checks: tuple[FirstRunCheck, ...] = ()
    initial_owner_env: str = ""  # env var holding the only username allowed on first sign-in

    @model_validator(mode="after")
    def _launch_details(self) -> Oidc:
        if (self.launch == "json_post") != (self.json_launch is not None):
            raise ValueError("json_post launch needs json_launch details, and only it may have them")
        return self


class SignIn(Model):
    # oidc: the app's own Authentik single sign-on.
    # gate: Authentik guards the address; the app keeps its own login (saved to the vault).
    # trusted_header: Authentik guards the address and tells the app who you are.
    # local: the app's own login, never Authentik-gated (Authentik, Vaultwarden).
    # none: nobody signs in (an API or an internal service).
    method: Literal["oidc", "gate", "trusted_header", "local", "none"]
    # The identity provider itself opens using the current shared sign-in session.
    session_provider: bool = False
    oidc: Oidc | None = None
    note: str = ""

    @model_validator(mode="after")
    def _oidc_details(self) -> SignIn:
        if self.session_provider and self.method != "local":
            raise ValueError("a session provider must use local sign-in")
        if (self.method == "oidc") != (self.oidc is not None):
            raise ValueError("sign_in.oidc is required for method oidc and forbidden otherwise")
        return self


class Account(Model):
    """How the app's first owner account comes to exist."""

    mode: Literal["none", "oidc_first_login", "trusted_header", "environment_bootstrap", "api_bootstrap"] = "none"
    needs_owner: bool = True  # False for an internal service account, rather than a person.
    user_action: str = ""
    # A generated login the person never sees, saved to their vault for Bitwarden to fill.
    save_login_to_vault: bool = False


# --- Generated settings -----------------------------------------------------------------


class Secret(Model):
    """A value generated once and then kept for the life of the app's data."""

    env: str
    kind: Literal["token", "base64", "hex", "fixed", "rsa_jwk", "argon2"] = "token"
    length: int = Field(default=36, ge=8, le=128)
    source_env: str = ""  # source secret for argon2, generated earlier
    value: str = ""  # for kind fixed: a non-secret default kept once chosen

    @field_validator("env")
    @classmethod
    def _env_name(cls, value: str) -> str:
        if not ENV_NAME.fullmatch(value):
            raise ValueError(f"invalid environment variable name {value!r}")
        return value


class ConfigField(Model):
    """A setting the owner may change from the dashboard."""

    key: str
    env: str
    type: Literal["string", "boolean", "integer", "enum", "secret"]
    label: str = ""
    default: str | bool | int | None = None
    required: bool = False
    options: tuple[str, ...] = ()


# --- Rules (shared behaviors; see ctl/rules) ---------------------------------------------


class RuleRef(Model):
    """One shared rule the app follows, with app-specific settings."""

    rule: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --- Chat -------------------------------------------------------------------------------


class Assistant(Model):
    title: str
    description: str
    instructions: str


class Chat(Model):
    assistant: Assistant | None = None
    connectors: tuple[str, ...] = ()  # folder names under apps/<id>/connectors/


class CredentialField(Model):
    key: str
    env: str
    type: Literal["string", "secret"]
    label: str
    required: bool = True
    prefix: str = ""


class CredentialScript(Model):
    """A script, kept in the connector's folder, run inside the app to mint the connector's credential.

    The script prints ``MU3LAB_OUTPUT <key>=<value>`` lines; nothing else it
    prints is kept, so account details never reach a log.
    """

    service: str  # the app's compose service to run it in
    script: str  # file name inside the connector folder
    interpreter: tuple[str, ...]  # e.g. ["python", "manage.py", "shell"]; the script arrives on stdin


class ReviewedUpdate(Model):
    version: str
    folder: str
    release_url: HttpsUrl


class Connector(Model):
    """A reviewed chat connector (MCP server) for one app, in apps/<id>/connectors/<id>/."""

    id: str
    name: str
    preferred: bool = False
    provenance: Literal["official", "community", "mu3lab-adapter"]
    repository: HttpsUrl
    revision: str
    package: str = ""
    transport: Literal["streamable-http"] = "streamable-http"
    endpoint: HttpUrl
    local_health: HttpUrl
    credentials: tuple[CredentialField, ...] = ()
    provision: CredentialScript | None = None
    provision_note: str = ""
    review_note: str = ""
    secrets: tuple[Secret, ...] = ()
    # Shared Mu3Lab files copied into the connector's project, e.g. the generic adapter.
    include: tuple[str, ...] = ()
    reviewed: bool = False  # a review.yaml sits next to it; served through the tool gateway
    reviewed_update: ReviewedUpdate | None = None

    @property
    def auto_provision(self) -> bool:
        return self.provision is not None


# --- Phones -----------------------------------------------------------------------------


class MobileClient(Model):
    id: str
    name: str
    kind: Literal["native", "pwa", "web"]
    support: Literal["official", "community", "experimental"]
    platforms: tuple[Literal["ios", "android", "web"], ...]
    install: dict[Literal["ios", "android", "web"], HttpsUrl] = Field(default_factory=dict)
    homepage: HttpsUrl | None = None
    source: HttpsUrl | None = None
    setup: Literal["server_url", "pwa", "web", "api_token", "device_qr", "developer_mode"]
    summary: str
    caveat: str = ""
    fallback: bool = False
    steps: tuple[str, ...] = Field(min_length=1)


class Mobile(Model):
    primary: str
    clients: tuple[MobileClient, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _primary_declared(self) -> Mobile:
        ids = [client.id for client in self.clients]
        if len(ids) != len(set(ids)) or self.primary not in ids:
            raise ValueError("mobile client ids must be unique and include the primary")
        return self


# --- Display ---------------------------------------------------------------------------


class Ui(Model):
    available: bool = True
    label: str = "Open"  # the dashboard's button text for opening the app
    path: str = ""
    unavailable_reason: str = ""


# --- The manifest -----------------------------------------------------------------------


class AppManifest(Model):
    id: str
    name: str
    # foundation: installed by ./install.sh. core: installed by core setup.
    # optional: the owner installs and removes it from the dashboard.
    tier: Literal["foundation", "core", "optional"]
    group: Literal["apps", "ai", "infrastructure"]
    category: str
    tagline: str
    summary: str
    version: str  # the maintainer-approved release; image digests live in docker-compose.yml
    upstream: str  # GitHub owner/repo for release notes
    capabilities: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    data_dir: str = ""  # folder under /srv/mu3lab/data; defaults to the app id
    service: ServiceSpec
    route: Route | None = None
    sign_in: SignIn
    account: Account = Account()
    secrets: tuple[Secret, ...] = ()
    env: dict[str, str | dict[str, Any] | list[Any]] = Field(default_factory=dict)
    configuration: tuple[ConfigField, ...] = ()
    rules: tuple[RuleRef, ...] = ()
    chat: Chat = Chat()
    mobile: Mobile | None = None
    ui: Ui = Ui()
    resource_guidance: str = ""
    setup_action: str = ""

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not APP_ID.fullmatch(value):
            raise ValueError("app id must be lowercase letters, digits and hyphens")
        return value

    @field_validator("env")
    @classmethod
    def _env_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        bad = [name for name in value if not ENV_NAME.fullmatch(name)]
        if bad:
            raise ValueError(f"invalid environment variable names: {', '.join(bad)}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> AppManifest:
        if self.sign_in.method in {"gate", "trusted_header"} and (self.route is None or self.route.access == "open"):
            raise ValueError("gated sign-in needs a route whose access is gate or trusted_header")
        if self.route and self.route.access == "trusted_header" and self.sign_in.method != "trusted_header":
            raise ValueError("a trusted_header route needs trusted_header sign-in")
        secret_names = [secret.env for secret in self.secrets]
        if len(secret_names) != len(set(secret_names)):
            raise ValueError("secret env names must be unique")
        clash = set(secret_names) & set(self.env)
        if clash:
            raise ValueError(f"env values may not overwrite generated secrets: {', '.join(sorted(clash))}")
        return self

    @property
    def data_folder(self) -> str:
        return self.data_dir or self.id

    @property
    def removable(self) -> bool:
        return self.tier == "optional"
