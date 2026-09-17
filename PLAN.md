# Mu3Lab product plan

Mu3Lab is a curated, private homelab platform for supported x86-64 Debian 12+
and Ubuntu 22+ hosts. It deliberately has two interfaces:

1. `./install.sh` opens a temporary, loopback-only HTML bootstrap dashboard.
2. The permanent React dashboard manages the curated platform after bootstrap.

The bootstrap path is check-first, resumable, and idempotent. It never accepts
an Authentik password, a Vaultwarden administrator password, or a Tailscale
auth key. It guides normal Tailscale web login and stops until the machine has
joined the tailnet. Docker group membership is a real session boundary: after
the group is added, the operator logs out and back in before Docker-backed
steps proceed.

## Non-negotiable boundaries

- Tailscale is required. Normal user entrypoints are private HTTPS URLs on one
  MagicDNS machine hostname (`https://mu3lab.<tailnet>.ts.net`) with documented
  stable ports where an application cannot safely live below a path prefix.
- Tailscale Serve accepts tailnet HTTPS and forwards only to loopback Caddy.
  Containers and the control plane do not publish ordinary LAN-facing ports.
- Authentik is mandatory infrastructure. The first administrator and household
  users are created through Authentik's official UI; public registration stays
  off.
- Vaultwarden is mandatory but is excluded from MCP and generic SSO automation.
- Persistent state belongs outside Git at `/srv/mu3lab/{data,backups,secrets,
  runtime,projects}`. Git contains only source, manifests, templates, defaults,
  and migration history. It never contains personal data or unencrypted secrets.
- Every deployable service uses a reviewed, pinned Compose image version or
  digest. Updates are manual and snapshot first; failure preserves generated
  configuration and diagnostics, then offers a guided restore. No silent rollback.

## Identity policy

Every catalog service declares one of `oidc`, `proxy`, `local`, or `excluded`.
Native Authentik OIDC is preferred. Authentik proxy/forward-auth protects access
in front of a service but does not create or authenticate the service's own user
session. Therefore Authentik and a local service password never need to match.

SurfSense is included in the core suite as a **local-account** application. Its
current upstream release has local email/password and Google OAuth, but no
supported Authentik OIDC/SAML, trusted-header, or external provisioning
integration. Mu3Lab will not fork it, automate browsers, write its database, or
replay passwords. The operator creates the account in SurfSense and may use
Vaultwarden to store and fill the credentials. The dashboard must never claim
this is true SSO.

## Data protection

Local encrypted Restic repositories live under `/srv/mu3lab/backups`, with
retention of 7 daily, 4 weekly, and 12 monthly snapshots. Each manifest must
declare database dumps, volumes/binds, and a service-scoped restore procedure.
Restores require explicit destructive confirmation and backups are integrity
checked before being marked verified.

## Delivery sequence

1. Stabilize source, tests, lint/type/YAML/secret checks, and documentation.
2. Finish the identity-first bootstrap sequence: local Vaultwarden account,
   Tailscale web login, private Vaultwarden route, Authentik administrator and
   users, forward-auth dashboard protection, health verification, and canonical
   tailnet URL.
3. Add authenticated, audited lifecycle jobs and the fixed core-suite executor
   to the React control plane.
4. Deliver the complete mandatory slice: Ollama, FreeLLMAPI, LiteLLM, Open WebUI,
   Firecrawl, and SurfSense. Routing is free-first; paid routes require an
   explicit user choice, and SurfSense remains local-account only.
5. Add optional curated applications one at a time only after their manifest,
   auth classification, health checks, storage/backup plan, resource profile,
   integration verification, and MCP policy are complete.

MCP tools are curated and policy-bound: reads are allowed by default, drafts
are visibly labeled, writes need confirmation, destructive operations require
typed confirmation, and privileged host operations are never autonomous.
