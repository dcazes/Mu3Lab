# Mu3Lab

Mu3Lab is a curated, private homelab control plane for Debian 12+ and Ubuntu
22.04+ x86-64 hosts. It separates first-run host preparation from normal app
management:

1. `./install.sh` (or `./check.sh`) opens a temporary local bootstrap dashboard at
   `127.0.0.1:8799`. It checks host compatibility, installs only missing
   dependencies, and pauses for unavoidable human actions such as the normal
   Tailscale web login. Developer unit tests are available separately and never
   block an end-user install.
2. The React control plane is the permanent dashboard. It is intended to be
   reached through private Tailscale HTTPS, manages only Mu3Lab's curated
   services, and never treats arbitrary Docker projects as trusted apps.

## Product safety rules

- Tailscale and Authentik are mandatory infrastructure for the completed
  platform; reusable Tailscale auth keys are never accepted by the dashboard.
- Persistent data lives under `/srv/mu3lab`, not in the Git checkout.
- Git contains definitions and safe defaults, never application data, backups,
  or unencrypted secrets.
- Install-time images are resolved to immutable digests. Automated update,
  backup, and restore execution are intentionally deferred until after the MVP.
- Vaultwarden and infrastructure lifecycle are always excluded from MCP exposure.
- SurfSense and Firecrawl are planned, not installable or ready. They remain
  hidden behind a blocked maturity state until their full upstream deployment,
  backup, route, and functional-test contracts exist.

## Current development state

The identity-first bootstrapper and registry-backed dashboard foundation are
active work.
`services.yaml` is the deployment source of truth and `catalog.yaml` is the
user-facing curated catalog. The permanent dashboard has Home, My Apps,
Connections, Security & Backups, and System views. It truthfully distinguishes
foundation, core, optional, and policy-blocked services, and never offers a
browser route until that route is actually published through the tailnet.

The control plane keeps leased, resumable, secret-redacted SQLite jobs and
structured events under `/srv/mu3lab/runtime`; a persistent worker reclaims
expired work after a restart. The supported AI slice is Ollama, FreeLLMAPI,
LiteLLM, and Open WebUI. It verifies generated provider configuration, streamed
chat, embedding dimensions, the private Open WebUI route, and Authentik
trusted-header identity before reporting the slice verified. The dashboard also
provides a full-screen Chat view backed by that protected Open WebUI instance.

Authenticated operators can install supported optional apps and manage them
from their detail pages. Install, start, stop, restart, retry, and MCP runtime
requests are durable jobs; the worker resolves every Compose path and command
from checked-in registries. The single host-wide compute setting selects the
reviewed CPU, NVIDIA, or AMD Ollama runtime contract; apps that do not use
acceleration ignore it. Basic application logs are bounded and redacted before
reaching the browser. Update execution is deferred from the MVP.

Connections includes reviewed application-data MCP integrations for Mealie,
Actual Budget, Immich, and Paperless-ngx. Operators provide write-only app
credentials, then Mu3Lab starts the isolated integration, performs live MCP
tool discovery, and registers the verified endpoint with Open WebUI. Firecrawl
and SurfSense candidates remain unavailable while their parent applications are
blocked; AdventureLog currently has no suitable public MCP server.

## Developer checks

```bash
./install.sh --no-open
make test
cd dashboard && npm ci && npm run build
```

The CI workflow runs the Python suite, dashboard build, and YAML validation.

## Storage and backups

```text
/srv/mu3lab/
├── data/
├── backups/
├── secrets/
├── runtime/
└── projects/
```

The intended local encrypted Restic policy is 7 daily, 4 weekly, and 12 monthly
snapshots. Backups currently report `not_configured` until a real repository is
initialized, and `verified` only after snapshot and integrity-check metadata
exist. Scheduled backup and restore execution are not shipped yet.

## License

Mu3Lab is licensed under the AGPL-3.0-or-later. Individual curated apps retain
their own licenses and operational requirements.
