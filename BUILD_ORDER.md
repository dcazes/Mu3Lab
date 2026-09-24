# Mu3Lab delivery checklist

This checklist is intentionally shorter than the historical build notes. The
service registry and tests, rather than an aspirational file list, are the
source of truth for current implementation status.

## Current foundation

- [x] Temporary stdlib bootstrap UI bound to `127.0.0.1`.
- [x] Unit-test and preflight gates, secret-redacted installation logs, and
  normal browser-based Tailscale login (no auth-key field).
- [x] x86-64 CPU/NVIDIA/AMD preflight classification; ARM is rejected for v1.
- [x] `/srv/mu3lab` runtime layout and a registry-backed read-only dashboard.
- [x] Pinned Compose templates for Caddy, Authentik, and Vaultwarden.
- [x] SurfSense classified as an Authentik-gated, local-account application;
  its non-privileged stack and official MCP are installable from the dashboard.

## Remaining implementation gates

1. Run the provider, SurfSense, and SurfSense MCP live acceptance path on a
   clean supported host and retain the resulting release/digest evidence.
2. Promote each remaining application MCP only after its installed-app,
   credential, tool-discovery, and LobeChat registration checks pass.
3. Implement encrypted Restic snapshots and a typed-confirmation restore flow
   for Authentik, Vaultwarden, and LiteLLM configuration.
4. Add update execution only after the backup/restore contract is operational.

No optional service is considered supported merely because it appears in the
catalog. It becomes supported only after every gate above that applies to it is
implemented and tested.
