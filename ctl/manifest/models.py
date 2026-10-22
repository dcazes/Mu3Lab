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
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("health path must start with /")
        return value


class ExtraPort(Model):
    """Another port the app's compose file publishes, beside ``local_port``.

    ``loopback`` ports are for this computer only (an extra route's upstream,
    MQTT, a health port). ``tailnet`` ports listen on this server's tailnet
    address only, for traffic HTTPS routes cannot carry, such as WebRTC media;
    the compose file binds them to ``${MU3LAB_TAILNET_IP}``.
    """

    port: Port
    purpose: str
    protocol: Literal["tcp", "udp"] = "tcp"
    listen: Literal["loopback", "tailnet"] = "loopback"


class ServiceSpec(Model):
    local_port: Port
    # Every other host port the compose file publishes; the catalog rejects any it does not list.
    extra_ports: tuple[ExtraPort, ...] = ()
    health: Health = Health()
    start_timeout_seconds: int = Field(default=120, ge=10, le=3600)
    stoppable: bool = True
    # Said in the Stop confirmation when stopping this app affects more than the app itself.
    stop_warning: str = ""
    uses_gpu: bool = False

    @model_validator(mode="after")
    def _distinct_ports(self) -> ServiceSpec:
        ports = [(self.local_port, "tcp"), *((extra.port, extra.protocol) for extra in self.extra_ports)]
        if len(ports) != len(set(ports)):
            raise ValueError("service ports must be distinct")
        return self


# --- Private address (Tailscale Serve → Caddy → app) ------------------------------------


class BlockedPath(Model):
    path: str
    message: str
    status: int = 403


class TokenBypass(Model):
    """Requests carrying their own API token skip the Authentik gate (phone apps).

    An empty ``header`` means the app checks credentials on every request to
    ``path`` itself (Subsonic phone apps send them in the query string).
    """

    path: str
    header_prefix: str
    header: str = "Authorization"

    @model_validator(mode="after")
    def _prefix_needs_header(self) -> TokenBypass:
        if not self.header and self.header_prefix:
            raise ValueError("a token bypass without a header cannot have a header prefix")
        return self


class Redirect(Model):
    """Send a path elsewhere on the same address, e.g. away from an app's own password form."""

    path: str
    to: str
    status: Literal[301, 302, 307, 308] = 302


class UpstreamBasicAuth(Model):
    """Caddy signs in to the app with its own login once Authentik has admitted the visitor.

    Both values are the app's generated settings. The header is built when Caddy
    starts and reaches it as an environment value, never written into the Caddyfile.
    """

    user_env: str
    password_env: str


class Route(Model):
    https_port: Port  # Tailscale Serve port on the tailnet hostname
    caddy_port: Port = Field(ge=19460, le=19499)  # loopback Caddy listener
    # Required, so no app is published ungated by leaving it out (review A-L2).
    # open: plain proxy (the app signs people in itself, e.g. native OIDC).
    # gate: Authentik's outpost must admit the visitor first.
    # trusted_header: gate, then Caddy passes the verified username to the app.
    access: Literal["open", "gate", "trusted_header"]
    copy_identity_headers: tuple[str, ...] = ()
    trusted_header: str = "Remote-User"
    # What the trusted header carries: the Authentik username, or the email for
    # apps that identify people by email (Beaver Habits).
    trusted_value: Literal["username", "email"] = "username"
    token_bypass: TokenBypass | None = None
    blocked_paths: tuple[BlockedPath, ...] = ()
    redirects: tuple[Redirect, ...] = ()
    forwarded_host: Literal["host", "hostport"] = "hostport"
    # Who Authentik admits, through a gate or to an OIDC app: everyone in the household, or administrators only.
    audience: Literal["household", "operators"] = "household"
    upstream_basic_auth: UpstreamBasicAuth | None = None
    # Foundation routes (dashboard, Authentik, Vaultwarden) are hand-reviewed in
    # apps/ingress/Caddyfile; every other route is generated from its manifest.
    generated: bool = True


class ExtraRoute(Route):
    """Another private address for the same app, proxied to one of its loopback ``extra_ports``.

    For apps with more than one browser-facing part (LiveKit: the voice page,
    its signalling server and an admin page). The dashboard shows each as a
    link labelled ``label`` beside the app's main Open button.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,20}$")
    # Plain words: it also names the route's sign-in provider in Authentik.
    label: str = Field(pattern=r"^[A-Za-z0-9 .-]{1,40}$")
    local_port: Port
    # False for an address only programs use (LiveKit's signalling server): no link on the app's page.
    link: bool = True


# --- Sign-in --------------------------------------------------------------------------


class OidcEnv(Model):
    """Where the app reads its OIDC client settings."""

    client_id: str
    client_secret: str
    discovery_url: str = ""


class JsonLaunch(Model):
    """The app's login page starts sign-in with a JSON request that returns Authentik's address."""

    path: str
    body: dict[str, Any]  # "{origin}" in a string value becomes the app's private address
    # GET sends ``body`` as query parameters (ContextForge's /auth/sso/login).
    method: Literal["POST", "GET"] = "POST"
    url_key: tuple[str, ...]
    # Serve a same-origin page at the launch path that runs this POST in the
    # browser, for apps whose own login page would otherwise ask first.
    launcher: bool = False
    session_check_path: str = ""  # already signed in? then the launcher just opens the app


class FormLaunch(Model):
    """The app's sign-in page holds a small form that starts Authentik sign-in when submitted.

    Rails apps (Dawarich) need a POST carrying the page's own anti-forgery
    token, so a plain link cannot start sign-in. Mu3Lab serves a launch page
    that loads ``page``, submits the form whose action is ``action``, and the
    app then hands over to Authentik.
    """

    page: str = Field(pattern=r"^/[\w\-./]*$")
    action: str = Field(pattern=r"^/[\w\-./]*$")


class FirstRunCheck(Model):
    """A JSON endpoint whose fields prove the app skipped its own first-run setup."""

    path: str
    expect: dict[str, Any]
    message: str


class RoleClaim(Model):
    """A claim naming the person's role in the app's own words, served as its own scope.

    The app requests the scope named ``claim`` (Audiobookshelf asks for its
    group claim by name) and receives the same-named claim.
    """

    claim: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    admin: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")  # for Mu3Lab operators
    user: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")  # for everyone else in the household
    multi: bool = True  # a list of one role, for apps that read a group list


class Oidc(Model):
    client_id: str
    redirect_paths: tuple[str, ...]
    role_claim: RoleClaim | None = None
    launch_path: str
    # How a browser starts sign-in: follow redirects, POST JSON, or submit a CSRF form.
    launch: Literal["redirect", "json_post", "csrf_form", "form_post"] = "redirect"
    json_launch: JsonLaunch | None = None
    form_launch: FormLaunch | None = None
    env: OidcEnv
    first_run_checks: tuple[FirstRunCheck, ...] = ()
    initial_owner_env: str = ""  # env var holding the only username allowed on first sign-in
    # The app passes each person's Authentik access token on to ContextForge (Open WebUI's tool calls).
    forwards_sign_in: bool = False

    @model_validator(mode="after")
    def _launch_details(self) -> Oidc:
        if (self.launch == "json_post") != (self.json_launch is not None):
            raise ValueError("json_post launch needs json_launch details, and only it may have them")
        if (self.launch == "form_post") != (self.form_launch is not None):
            raise ValueError("form_post launch needs form_launch details, and only it may have them")
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
    # Who may use it: the whole household, or administrators only (an infrastructure app's
    # read-only assistant, which shows the whole server's people, sign-ins or backups).
    audience: Literal["household", "operators"] = "household"


class Chat(Model):
    assistant: Assistant | None = None
    connectors: tuple[str, ...] = ()  # folder names under apps/<id>/connectors/
    # For a chat app (capability "chat"): how Mu3Lab adds the app assistants (ctl.chat_ops).
    workspace: Literal["", "lobe", "open_webui"] = ""


class ModelRunner(Model):
    """For a model runner (capability ``model_runner``): which API Mu3Lab drives (ctl.model_runners)."""

    engine: Literal["ollama_api"]


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
    # Pass the installing owner's username as the script's only argument, so it can
    # find the owner's account even after other household members become admins.
    owner_username: bool = False
    # Or the owner's email, for apps that identify people by email.
    owner_email: bool = False
    # Optional script, in the same folder and interpreter, that deletes the credential when the
    # connector is switched off. Only for credentials minted for Mu3Lab alone, never a phone's.
    revoke_script: str = ""


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
    # Or Mu3Lab mints the credential itself, through the app's API with a login Mu3Lab already
    # holds (``connector_credentials`` in apps/<id>/hooks.py), when no script inside the app can.
    provision_hook: bool = False
    provision_note: str = ""
    review_note: str = ""
    secrets: tuple[Secret, ...] = ()
    # Shared Mu3Lab files copied into the connector's project, e.g. the generic adapter.
    include: tuple[str, ...] = ()
    reviewed: bool = False  # a review.yaml sits next to it: the tools ContextForge starts it with
    reviewed_update: ReviewedUpdate | None = None
    # The app serves MCP itself (``endpoint`` is the app's own service): no connector container
    # to build, start or stop, and the app joins mu3lab_mcp_upstream so ContextForge can reach it.
    builtin: bool = False
    # Fixed headers sent with every call besides the credential, e.g. X-Forwarded-Proto for an app
    # that redirects plain HTTP because it expects to sit behind an HTTPS proxy.
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_provisioner(self) -> Connector:
        if self.provision is not None and self.provision_hook:
            raise ValueError("a connector is provisioned by a script or by its app's hooks, not both")
        return self

    @property
    def auto_provision(self) -> bool:
        return self.provision is not None or self.provision_hook


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


# --- Working with other apps -----------------------------------------------------------


class Integration(Model):
    """Settings that depend on whether an app providing a capability is installed.

    ``env`` applies while a provider is installed; its values may read the
    provider's own settings as ``{{provider:<ENV>}}``. ``absent_env`` applies
    otherwise. Installing or removing the provider re-renders this app.
    """

    capability: str
    env: dict[str, str] = Field(default_factory=dict)
    absent_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("env", "absent_env")
    @classmethod
    def _env_names(cls, value: dict[str, str]) -> dict[str, str]:
        bad = [name for name in value if not ENV_NAME.fullmatch(name)]
        if bad:
            raise ValueError(f"invalid environment variable names: {', '.join(bad)}")
        return value


# --- Backups ---------------------------------------------------------------------------


class BackupSpec(Model):
    """Whether the app's data folders are backed up.

    Leave an app out only when its data can be fetched again and holds nothing
    people made (downloaded AI models, caches); ``reason`` is shown to them.
    """

    include: bool = True
    reason: str = ""
    # Data folders (by name) left out because they are downloaded again (a map index); still removed on uninstall.
    skip: tuple[str, ...] = ()


# --- Media libraries -------------------------------------------------------------------


class MediaFolder(Model):
    """A library under the media root (``/srv/mu3lab/media/<name>``) that people fill themselves.

    Compose files mount it as ``${MU3LAB_MEDIA_<NAME>}``. Uninstall never deletes
    it, and backups leave it out: it is large and people already have the files.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,30}$")
    purpose: str

    @property
    def env(self) -> str:
        return "MU3LAB_MEDIA_" + self.name.upper().replace("-", "_")


# --- Display ---------------------------------------------------------------------------


class Ui(Model):
    available: bool = True
    label: str = "Open"  # the dashboard's button text for opening the app
    path: str = ""
    unavailable_reason: str = ""
    # Compose services worth naming on the app's page, by service name (Dawarich's Photon).
    parts: dict[str, str] = Field(default_factory=dict)


# --- The manifest -----------------------------------------------------------------------


class AppManifest(Model):
    id: str
    name: str
    # foundation: installed by ./install.sh. core: installed by core setup.
    # optional: the owner installs and removes it from the dashboard.
    tier: Literal["foundation", "core", "optional"]
    group: Literal["apps", "ai", "infrastructure"]
    # Where Apps > Discover lists the app; the dashboard names and orders these (lib/services.ts).
    category: Literal["finance", "home", "media", "documents", "places", "knowledge", "ai", "system"]
    tagline: str
    summary: str
    version: str  # the maintainer-approved release; image digests live in docker-compose.yml
    upstream: str  # GitHub owner/repo for release notes
    capabilities: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    data_dir: str = ""  # folder under /srv/mu3lab/data; defaults to the app id
    media: tuple[MediaFolder, ...] = ()
    backup: BackupSpec = BackupSpec()
    service: ServiceSpec
    route: Route | None = None
    # More private addresses, each with its own tailnet port, Caddy listener and access rule.
    extra_routes: tuple[ExtraRoute, ...] = ()
    sign_in: SignIn
    account: Account = Account()
    secrets: tuple[Secret, ...] = ()
    env: dict[str, str | dict[str, Any] | list[Any]] = Field(default_factory=dict)
    configuration: tuple[ConfigField, ...] = ()
    integrates_with: tuple[Integration, ...] = ()
    rules: tuple[RuleRef, ...] = ()
    chat: Chat = Chat()
    model_runner: ModelRunner | None = None
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
        for route in (self.route, *self.extra_routes):
            if route and route.upstream_basic_auth and route.access == "open":
                # The login it adds would be handed to anyone who reached the address.
                raise ValueError("upstream_basic_auth needs a route Authentik guards")
        if self.extra_routes:
            if self.route is None:
                raise ValueError("extra_routes need a main route")
            names = [route.name for route in self.extra_routes]
            if len(names) != len(set(names)):
                raise ValueError("extra route names must be unique")
            routes = (self.route, *self.extra_routes)
            for field in ("https_port", "caddy_port"):
                values = [getattr(route, field) for route in routes]
                if len(values) != len(set(values)):
                    raise ValueError(f"each route needs its own {field}")
            upstreams = [self.service.local_port, *(route.local_port for route in self.extra_routes)]
            if len(upstreams) != len(set(upstreams)):
                raise ValueError("each route needs its own local_port")
            loopback = {extra.port for extra in self.service.extra_ports if extra.listen == "loopback"}
            for route in self.extra_routes:
                if route.local_port not in loopback:
                    raise ValueError(
                        f"extra route {route.name!r} needs its local_port in service.extra_ports (loopback)"
                    )
                if route.access == "trusted_header":
                    # One app, one trusted identity header: only its main route may carry it.
                    raise ValueError("an extra route cannot use trusted_header access")
        secret_names = [secret.env for secret in self.secrets]
        if len(secret_names) != len(set(secret_names)):
            raise ValueError("secret env names must be unique")
        clash = set(secret_names) & set(self.env)
        if clash:
            raise ValueError(f"env values may not overwrite generated secrets: {', '.join(sorted(clash))}")
        if ("model_runner" in self.capabilities) != (self.model_runner is not None):
            raise ValueError("a model runner (capability model_runner) declares model_runner.engine, and only it")
        return self

    @property
    def data_folder(self) -> str:
        return self.data_dir or self.id

    @property
    def removable(self) -> bool:
        return self.tier == "optional"
