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
- [x] SurfSense explicitly classified as blocked, not as SSO-capable.

## Next implementation gates

1. Make the Docker logout/login checkpoint enforce the supported operator
   session transition, then finish core Compose environment rendering and
   health-gated startup.
2. Configure the single MagicDNS hostname through Tailscale Serve and Caddy;
   expose only declared tailnet HTTPS routes and verify them from the tailnet.
3. Add Authentik protection to the permanent dashboard before any mutating
   lifecycle endpoint exists.
4. Persist resumable, append-only, secret-free setup/lifecycle/backup jobs
   under `/srv/mu3lab/runtime`.
5. Implement encrypted Restic snapshots and a typed-confirmation restore flow
   for Authentik, Vaultwarden, and LiteLLM configuration.
6. Add the Ollama → LiteLLM → Open WebUI vertical slice, including resource
   profiles, connection tests, free-first routing, and MCP authorization tests.

No optional service is considered supported merely because it appears in the
catalog. It becomes supported only after every gate above that applies to it is
implemented and tested.
