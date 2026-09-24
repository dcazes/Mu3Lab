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
- SurfSense is installable through a reviewed v0.0.40 stack with its privileged
  sandbox disabled. Its tailnet route is Authentik-gated and SurfSense then uses
  a separate local account. Firecrawl is available as a private, login-free API
  with durable queue, browser, cache, and database services.

## Current development state

The identity-first bootstrapper and registry-backed dashboard foundation are
active work.
`services.yaml` is the deployment source of truth and `catalog.yaml` is the
user-facing curated catalog. The permanent dashboard has Home, My Apps,
Connections, Security & Backups, and System views. It truthfully distinguishes
foundation, core, optional, and policy-blocked services, and never offers a
browser route until that route is actually published through the tailnet.

An optional Homarr v2 preview dashboard is available beside the custom Home
view at `/homarr`. It is embedded after its private Tailnet route is verified
and also offers a full-page link. Its AI & research and Work & life groups use
single native tiles that combine each app icon, live running/stopped status,
and the permitted audited start/stop action. The separate container inspector
remains behind a socket proxy that keeps general POST access disabled and
allowlists only container start/stop; restart, removal, configuration, and
other Docker mutations remain disabled. The prior v1 app-data directory is
retained separately for rollback.

The control plane keeps leased, resumable, secret-redacted SQLite jobs and
structured events under `/srv/mu3lab/runtime`; a persistent worker reclaims
expired work after a restart. The supported AI slice is Ollama, FreeLLMAPI,
LiteLLM, Open WebUI, and optional LobeChat. It verifies generated provider
configuration, streamed chat, embedding dimensions, private routes, and
Authentik-protected identity before reporting the slice verified. The dashboard
provides a full-screen Chat view with separate LobeChat and Open WebUI tabs;
LobeChat is preferred when installed while Open WebUI remains available.

Authenticated operators can install supported optional apps and manage them
from their detail pages. Install, start, stop, restart, retry, and MCP runtime
requests are durable jobs; the worker resolves every Compose path and command
from checked-in registries. The single host-wide compute setting selects the
reviewed CPU, NVIDIA, or AMD Ollama runtime contract; apps that do not use
acceleration ignore it. Basic application logs are bounded and redacted before
reaching the browser. Update execution is deferred from the MVP.

Provider Accounts accepts a curated eight-provider allowlist, stores one
write-only encrypted credential per provider, and reports the provider's
FreeLLMAPI route plus a streamed LiteLLM `mu3lab-chat` check. The screen shows
API authorization failures and verification job progress separately from an
empty list. LobeChat offers only `mu3lab-chat`; Mu3Lab disables other persisted
provider/model rows and blocks re-enabling them while preserving chat history.
Eight saved LobeChat agents cover Actual Budget, Mealie, Immich, Paperless-ngx,
SurfSense, Firecrawl, Nextcloud, and AdventureLog.

Advanced Integrations separates automatically managed connections from apps
that still require a user-scoped credential or approval. Mu3Lab handles MCP
preparation, verification, chat registration, and lifecycle reconciliation
after the required application credential is available. Enabled MCPs start
after their application is healthy and stop before it stops; the worker
reconciles those states after a host restart. Verified Streamable HTTP tools
are bound and pinned only to their matching LobeChat agent, with approval
required for write tools. Open WebUI receives the same reviewed connections
when installed. Connections → Advanced integrations also offers prepared
runtime status, per-tool permissions, an operator tool console, metadata-only
action history from the dashboard and LobeChat, job diagnostics, and reviewed
update status. Write calls in the console require a one-use confirmation.
Mealie uses a pinned stdio-to-HTTP bridge; Firecrawl uses its official HTTP
MCP server against the self-hosted API. Mu3Lab's small Nextcloud and
AdventureLog adapters expose scoped files and travel data operations. Baby Buddy
is listed as a planned productivity app; its official MCP remains manual because
it acts as the user represented by an API token copied from BabyBuddy settings.
All accepted MCP runtimes can be prepared while their apps are stopped. A
connection is not reported live until credentials, health, and tool discovery
pass; Nextcloud and AdventureLog also perform an app-data read check.

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
