"""Authentik configuration for Mu3Lab, rendered as Blueprint documents.

Pure functions: no files, no network. ``ctl.identity`` decides what to render
from the app manifests and applies the result through Authentik's API.

Three kinds of document exist:

* the gate: the dashboard plus every installed app whose route is guarded by
  Authentik's outpost (``route.access`` gate or trusted_header), each admitting
  the household or administrators only (``route.audience``);
* one OIDC application per installed app that signs people in itself;
* a removal document that deletes an uninstalled app's objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TAILNET_NAME = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.ts\.net\Z")
_PLAIN_ID = re.compile(r"[a-z0-9-]+")
_PLAIN_NAME = re.compile(r"[A-Za-z0-9 .-]+")
# Authentik's documented launch URL for "hide from the user's library". Only the
# dashboard is listed there; apps are opened from the dashboard.
HIDDEN_FROM_LIBRARY = "blank://blank"
ADMIN_GROUPS = ("authentik Admins", "mu3lab-operators")
HOUSEHOLD_GROUP = "mu3lab-household"
DASHBOARD_BLUEPRINT = "Mu3Lab dashboard access"


@dataclass(frozen=True)
class GatedApp:
    id: str
    name: str
    port: int
    audience: str  # household | operators


@dataclass(frozen=True)
class OidcApp:
    id: str
    name: str
    port: int
    client_id: str
    client_secret: str
    redirect_paths: tuple[str, ...]
    initial_owner: str = ""  # while set, only this username may sign in
    # (claim, admin value, user value, list?) for apps that read the role in their own words.
    role_claim: tuple[str, str, str, bool] | None = None
    audience: str = "household"  # household | operators (who may sign in)
    forwards_sign_in: bool = False  # passes people's access tokens on to ContextForge


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def validate_host(host: str) -> str:
    host = host.rstrip(".")
    if not _TAILNET_NAME.fullmatch(host):
        raise ValueError("Authentik's external host must be a MagicDNS ts.net name")
    return host


def _check_names(app_id: str, name: str) -> None:
    if not _PLAIN_ID.fullmatch(app_id) or not _PLAIN_NAME.fullmatch(name):
        raise ValueError("app identifiers in Authentik must be plain names")


def oidc_blueprint_name(app_id: str) -> str:
    return f"Mu3Lab {app_id} sign-in"


def removal_blueprint_name(app_id: str) -> str:
    return f"Mu3Lab {app_id} removal"


# How long one sign-in lasts on a device. Every app and the dashboard renew their own sign-in
# from it without asking; signing out, or being removed from the household, ends it at once.
SESSION = "days=30"
_FLOWS = """      authentication_flow: !Find [authentik_flows.flow, [slug, default-authentication-flow]]
      # Implicit consent: no "Continue to app?" page. A consent page left open
      # loses its place when another app starts a sign-in in the same browser.
      authorization_flow: !Find [authentik_flows.flow, [slug, default-provider-authorization-implicit-consent]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, default-provider-invalidation-flow]]
"""


def _bindings(target: str, groups: tuple[str, ...]) -> str:
    return "".join(
        f"""  - model: authentik_policies.policybinding
    state: present
    identifiers:
      target: !KeyOf {target}
      order: {order}
    attrs:
      group: !Find [authentik_core.group, [name, {group}]]
"""
        for order, group in enumerate(groups)
    )


def _gated_entries(host: str, app: GatedApp) -> str:
    """Forward-auth provider and hidden application; the outpost answers 404 for unknown hosts."""
    _check_names(app.id, app.name)
    if not 1024 <= app.port <= 65535:
        raise ValueError("gated app port must be a private HTTPS port")
    key = f"mu3lab-{app.id}-application"
    entries = f"""  - model: authentik_providers_proxy.proxyprovider
    state: present
    identifiers:
      name: Mu3Lab {app.name} provider
    attrs:
      name: Mu3Lab {app.name} provider
      mode: forward_single
      # The app's own origin, port included, so sign-in returns to it.
      external_host: {quote(f"https://{host}:{app.port}")}
      access_token_validity: hours=24
{_FLOWS}      intercept_header_auth: true
  - model: authentik_core.application
    id: {key}
    state: present
    identifiers:
      slug: mu3lab-{app.id}
    attrs:
      name: Mu3Lab {app.name}
      slug: mu3lab-{app.id}
      meta_launch_url: {quote(HIDDEN_FROM_LIBRARY)}
      policy_engine_mode: any
      provider: !Find [authentik_providers_proxy.proxyprovider, [name, Mu3Lab {app.name} provider]]
"""
    # Bound like the OIDC apps: someone removed from the household is refused at once,
    # even while their account's own revocation is still pending.
    groups = ADMIN_GROUPS if app.audience == "operators" else (*ADMIN_GROUPS, HOUSEHOLD_GROUP)
    return entries + _bindings(key, groups)


# The one-time sign-in link a new household member receives (Settings → People):
# it signs them in once and asks them to choose their own password. Authentik
# issues such links only through the brand's recovery flow.
WELCOME_FLOW = """  - model: authentik_stages_prompt.prompt
    id: mu3lab-welcome-password
    state: present
    identifiers:
      name: mu3lab-welcome-password
    attrs:
      field_key: password
      label: Password
      type: password
      required: true
      placeholder: Choose a password
      order: 0
  - model: authentik_stages_prompt.prompt
    id: mu3lab-welcome-password-repeat
    state: present
    identifiers:
      name: mu3lab-welcome-password-repeat
    attrs:
      field_key: password_repeat
      label: Password (again)
      type: password
      required: true
      placeholder: Type it again
      order: 1
  - model: authentik_stages_prompt.promptstage
    id: mu3lab-welcome-prompt
    state: present
    identifiers:
      name: Mu3Lab choose your password
    attrs:
      fields:
        - !KeyOf mu3lab-welcome-password
        - !KeyOf mu3lab-welcome-password-repeat
  - model: authentik_stages_user_write.userwritestage
    id: mu3lab-welcome-write
    state: present
    identifiers:
      name: mu3lab-welcome-user-write
    attrs:
      user_creation_mode: never_create
  - model: authentik_stages_user_login.userloginstage
    id: mu3lab-welcome-login
    state: present
    identifiers:
      name: mu3lab-welcome-user-login
    attrs:
      session_duration: {SESSION}
  # Authentik's own password sign-in. Left at 0 it lasts a day whatever the browser does, after
  # which every app and the dashboard ask for the password again.
  - model: authentik_stages_user_login.userloginstage
    state: present
    identifiers:
      name: default-authentication-login
    attrs:
      session_duration: {SESSION}
  - model: authentik_flows.flow
    id: mu3lab-welcome-flow
    state: present
    identifiers:
      slug: mu3lab-welcome
    attrs:
      name: Welcome to Mu3Lab
      title: Choose your Mu3Lab password
      designation: recovery
      authentication: require_unauthenticated
  - model: authentik_flows.flowstagebinding
    state: present
    identifiers:
      target: !KeyOf mu3lab-welcome-flow
      stage: !KeyOf mu3lab-welcome-prompt
      order: 10
  - model: authentik_flows.flowstagebinding
    state: present
    identifiers:
      target: !KeyOf mu3lab-welcome-flow
      stage: !KeyOf mu3lab-welcome-write
      order: 20
  - model: authentik_flows.flowstagebinding
    state: present
    identifiers:
      target: !KeyOf mu3lab-welcome-flow
      stage: !KeyOf mu3lab-welcome-login
      order: 30
  - model: authentik_brands.brand
    state: present
    # Authentik's own default brand: matching on domain alone created a second
    # one when this ran before Authentik's built-in blueprint had made it.
    identifiers:
      domain: authentik-default
      default: true
    attrs:
      flow_recovery: !KeyOf mu3lab-welcome-flow
""".replace("{SESSION}", SESSION)


def render_gate_blueprint(host: str, dashboard_port: int, gated: tuple[GatedApp, ...] = ()) -> str:
    """The dashboard's gate, Mu3Lab's groups, every gated app and the embedded outpost."""
    host = validate_host(host)
    dashboard = f"https://{host}:{dashboard_port}"
    authentik = f"https://{host}"
    providers = "".join(
        f"        - !Find [authentik_providers_proxy.proxyprovider, [name, Mu3Lab {app.name} provider]]\n"
        for app in gated
    )
    return f"""# yaml-language-server: $schema=https://goauthentik.io/blueprints/schema.json
# Generated by Mu3Lab from the app manifests; contains no credentials.
version: 1
metadata:
  name: {DASHBOARD_BLUEPRINT}
entries:
  - model: authentik_core.group
    state: present
    identifiers:
      name: mu3lab-operators
    attrs:
      name: mu3lab-operators
  # Household members: the dashboard and every app, but no administration.
  - model: authentik_core.group
    state: present
    identifiers:
      name: {HOUSEHOLD_GROUP}
    attrs:
      name: {HOUSEHOLD_GROUP}
  - model: authentik_providers_proxy.proxyprovider
    state: present
    identifiers:
      name: Mu3Lab dashboard provider
    attrs:
      name: Mu3Lab dashboard provider
      mode: forward_single
      # The dashboard's origin, port included: without it the outpost would
      # send people to Authentik's own library instead of back to Mu3Lab.
      external_host: {quote(dashboard)}
      access_token_validity: hours=24
{_FLOWS}      intercept_header_auth: true
  - model: authentik_core.application
    id: mu3lab-dashboard-application
    state: present
    identifiers:
      slug: mu3lab
    attrs:
      name: Mu3Lab
      slug: mu3lab
      meta_launch_url: {quote(dashboard)}
      meta_description: Private Mu3Lab control plane
      policy_engine_mode: any
      provider: !Find [authentik_providers_proxy.proxyprovider, [name, Mu3Lab dashboard provider]]
{_bindings("mu3lab-dashboard-application", (*ADMIN_GROUPS, HOUSEHOLD_GROUP))}{"".join(_gated_entries(host, app) for app in gated)}{WELCOME_FLOW}  - model: authentik_outposts.outpost
    state: present
    identifiers:
      name: authentik Embedded Outpost
    attrs:
      name: authentik Embedded Outpost
      type: proxy
      providers:
        - !Find [authentik_providers_proxy.proxyprovider, [name, Mu3Lab dashboard provider]]
{providers}      config:
        authentik_host: {quote(authentik)}
        authentik_host_browser: {quote(authentik)}
"""


def _role_scope(app: OidcApp) -> str:
    """A scope named after the app's role claim, carrying the person's role in the app's own words."""
    if not app.role_claim:
        return ""
    claim, admin_value, user_value, multi = app.role_claim
    role = f"{admin_value!r} if admin else {user_value!r}"
    value = f"[{role}]" if multi else role
    return f"""
  - id: mu3lab-{app.id}-roles
    model: authentik_providers_oauth2.scopemapping
    state: present
    identifiers:
      name: Mu3Lab {app.name} role claim
    attrs:
      name: Mu3Lab {app.name} role claim
      scope_name: {claim}
      description: Your role in {app.name}
      expression: |
        names = {{group.name for group in request.user.ak_groups.all()}}
        admin = bool(names & {{"mu3lab-operators", "authentik Admins"}})
        return {{{quote(claim)}: {value}}}"""


def _forwarded_tokens(app: OidcApp) -> str:
    """Token lifetimes for an app that passes people's access tokens on to ContextForge.

    Authentik copies the sign-in's nonce into the first access token, and ContextForge
    refuses any token with a nonce as an ID token. Refreshed access tokens carry none, and
    Open WebUI refreshes any token within 5 minutes of expiry, so a 4-minute token is always
    refreshed before it is passed on. Refresh tokens are kept (not replaced on every use)
    until their last week, so tool calls refreshing at the same moment do not revoke each other.
    """
    if not app.forwards_sign_in:
        return ""
    return """
      access_token_validity: minutes=4
      refresh_token_validity: days=30
      refresh_token_threshold: days=7"""


def render_oidc_blueprint(host: str, app: OidcApp) -> str:
    """One app's confidential OIDC client, its claims and who may sign in."""
    host = validate_host(host)
    _check_names(app.id, app.name)
    secrets_ok = app.client_id and app.client_secret and not any(c in app.client_id + app.client_secret for c in "\r\n")
    if not secrets_ok or not app.redirect_paths or any(not path.startswith("/") for path in app.redirect_paths):
        raise ValueError(f"{app.id}: OIDC client settings are incomplete")
    if any(c in app.initial_owner for c in "\r\n'\""):
        raise ValueError(f"{app.id}: invalid initial owner")
    origin = f"https://{host}:{app.port}"
    redirects = "\n".join(
        f"        - matching_mode: strict\n          url: {quote(origin + path)}" for path in app.redirect_paths
    )
    key = f"mu3lab-{app.id}-application"
    content = f"""# yaml-language-server: $schema=https://goauthentik.io/blueprints/schema.json
# Generated by Mu3Lab from apps/{app.id}/app.yaml.
version: 1
metadata:
  name: {oidc_blueprint_name(app.id)}
entries:
  - id: mu3lab-{app.id}-claims
    model: authentik_providers_oauth2.scopemapping
    state: present
    identifiers:
      name: Mu3Lab {app.name} verified identity claims
    attrs:
      name: Mu3Lab {app.name} verified identity claims
      scope_name: profile
      description: Verified email, groups, and Mu3Lab operator role
      expression: |
        groups = [group.name for group in request.user.ak_groups.all()]
        admin = "mu3lab-operators" in groups or "authentik Admins" in groups
        # Apps gate on these two names: every admitted person is a user, and
        # Authentik's own administrators count as Mu3Lab operators.
        if admin or "mu3lab-household" in groups:
          groups.append("mu3lab-users")
        if admin and "mu3lab-operators" not in groups:
          groups.append("mu3lab-operators")
        return {{
          "email_verified": bool(request.user.email),
          "preferred_username": request.user.username,
          "name": request.user.name or request.user.username,
          "groups": groups,
          "mu3lab_role": "admin" if admin else "user",
        }}{_role_scope(app)}
  - id: mu3lab-{app.id}-provider
    model: authentik_providers_oauth2.oauth2provider
    state: present
    identifiers:
      name: Mu3Lab {app.name} provider
    attrs:
      name: Mu3Lab {app.name} provider
      client_type: confidential
      grant_types:
        - authorization_code
        # Without it Authentik refuses the refresh tokens offline_access hands out.
        - refresh_token{_forwarded_tokens(app)}
      signing_key: !Find [authentik_crypto.certificatekeypair, [name, authentik Self-signed Certificate]]
      client_id: {quote(app.client_id)}
      client_secret: {quote(app.client_secret)}
      authorization_flow: !Find [authentik_flows.flow, [slug, default-provider-authorization-implicit-consent]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, default-provider-invalidation-flow]]
      issuer_mode: per_provider
      include_claims_in_id_token: true
      property_mappings:
        - !Find [authentik_providers_oauth2.scopemapping, [scope_name, openid]]
        - !Find [authentik_providers_oauth2.scopemapping, [scope_name, email]]
        - !Find [authentik_providers_oauth2.scopemapping, [scope_name, profile]]
        # Refresh tokens, for clients that keep acting for a signed-in person (Open WebUI's tool calls).
        - !Find [authentik_providers_oauth2.scopemapping, [scope_name, offline_access]]
        - !KeyOf mu3lab-{app.id}-claims{"" if not app.role_claim else chr(10) + f"        - !KeyOf mu3lab-{app.id}-roles"}
      redirect_uris:
{redirects}
  - model: authentik_core.application
    id: {key}
    state: present
    identifiers:
      slug: mu3lab-{app.id}
    attrs:
      name: {quote(app.name)}
      slug: mu3lab-{app.id}
      provider: !KeyOf mu3lab-{app.id}-provider
      meta_launch_url: {quote(HIDDEN_FROM_LIBRARY)}
      policy_engine_mode: any
"""
    if not app.initial_owner:
        if app.audience == "operators":
            # The household binding (order 2) goes if the app was once open to everyone.
            absent = f"""  - model: authentik_policies.policybinding
    state: absent
    identifiers:
      target: !KeyOf {key}
      order: 2
"""
            return content + _bindings(key, ADMIN_GROUPS) + absent + _absent_owner_policy(app)
        return content + _bindings(key, (*ADMIN_GROUPS, HOUSEHOLD_GROUP)) + _absent_owner_policy(app)
    # The first person to sign in becomes the app's owner (Actual Budget), so
    # until the installer has, only they are admitted.
    expression = (
        f"return request.user.username == {app.initial_owner!r} and "
        "request.user.ak_groups.filter(name__in=['authentik Admins', 'mu3lab-operators']).exists()"
    )
    return (
        content
        + "".join(
            f"""  - model: authentik_policies.policybinding
    state: absent
    identifiers:
      target: !KeyOf {key}
      order: {order}
"""
            for order in range(3)
        )
        + f"""  - model: authentik_policies_expression.expressionpolicy
    id: mu3lab-{app.id}-owner-policy
    state: present
    identifiers:
      name: Mu3Lab {app.name} owner admission
    attrs:
      expression: |
        {expression}
  - model: authentik_policies.policybinding
    state: present
    identifiers:
      target: !KeyOf {key}
      order: 10
    attrs:
      policy: !KeyOf mu3lab-{app.id}-owner-policy
"""
    )


def _absent_owner_policy(app: OidcApp) -> str:
    """Once the owner guard is lifted, its policy and binding go away."""
    return f"""  - model: authentik_policies_expression.expressionpolicy
    state: absent
    identifiers:
      name: Mu3Lab {app.name} owner admission
"""


def render_removal_blueprint(app_id: str, name: str, *, oidc: bool) -> str:
    """Delete one uninstalled app's Authentik objects (the names match what the renderers create)."""
    _check_names(app_id, name)
    provider_model = "authentik_providers_oauth2.oauth2provider" if oidc else "authentik_providers_proxy.proxyprovider"
    extra = (
        f"""  - model: authentik_providers_oauth2.scopemapping
    state: absent
    identifiers:
      name: Mu3Lab {name} verified identity claims
  - model: authentik_providers_oauth2.scopemapping
    state: absent
    identifiers:
      name: Mu3Lab {name} role claim
  - model: authentik_policies_expression.expressionpolicy
    state: absent
    identifiers:
      name: Mu3Lab {name} owner admission
"""
        if oidc
        else ""
    )
    return f"""# yaml-language-server: $schema=https://goauthentik.io/blueprints/schema.json
version: 1
metadata:
  name: {removal_blueprint_name(app_id)}
entries:
  - model: authentik_core.application
    state: absent
    identifiers:
      slug: mu3lab-{app_id}
  - model: {provider_model}
    state: absent
    identifiers:
      name: Mu3Lab {name} provider
{extra}"""


CHAT_READER = "mu3lab-mcp"
CHAT_READER_BLUEPRINT = "Mu3Lab chat read-only access"
CHAT_READER_PERMISSIONS = ("authentik_core.view_user", "authentik_core.view_group", "authentik_events.view_event")
_TOKEN_KEY = re.compile(r"[A-Za-z0-9_-]{40,128}")


def render_chat_reader_blueprint(key: str) -> str:
    """A service account that can only view people, groups and sign-in events, with an API token ``key``.

    The administrators-only Authentik assistant uses it; it cannot sign in, and its role grants
    nothing beyond viewing. Applying it again replaces the token's key.
    """
    if not _TOKEN_KEY.fullmatch(key):
        raise ValueError("The chat token for Authentik is not a plain random key.")
    permissions = "".join(f"\n        - {permission}" for permission in CHAT_READER_PERMISSIONS)
    return f"""# yaml-language-server: $schema=https://goauthentik.io/blueprints/schema.json
version: 1
metadata:
  name: {CHAT_READER_BLUEPRINT}
entries:
  - model: authentik_rbac.role
    identifiers:
      name: Mu3Lab chat read-only
    attrs:
      permissions:{permissions}
  - model: authentik_core.user
    identifiers:
      username: {CHAT_READER}
    attrs:
      name: Mu3Lab chat (read-only)
      type: service_account
      is_active: true
      roles:
        - !Find [authentik_rbac.role, [name, Mu3Lab chat read-only]]
  - model: authentik_core.token
    identifiers:
      identifier: {CHAT_READER}-read-only
    attrs:
      user: !Find [authentik_core.user, [username, {CHAT_READER}]]
      intent: api
      expiring: false
      description: Mu3Lab's administrators-only chat assistant reads people, groups and sign-in events.
      key: {quote(key)}
"""
