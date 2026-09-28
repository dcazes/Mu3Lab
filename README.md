# Mu3Lab

Mu3Lab is a curated, private homelab control plane for Linux desktops running
Ubuntu 22.04+, Debian 12+, or a derivative such as Linux Mint (x86-64 or arm64).

## Install

```bash
git clone https://github.com/dcazes/Mu3Lab.git
cd Mu3Lab
./install.sh
```

`./install.sh` asks for your password once, prepares its own Python
environment, and opens a setup page in your browser. The page checks this
computer, installs only what is missing or out of date, and tells you when it
needs you: creating your Vaultwarden and Authentik accounts and approving the
computer in Tailscale. Keep the terminal open until the page says Mu3Lab is
installed, then continue in your private dashboard.

Run `./install.sh` again at any time, for example after `git pull`: finished
steps are skipped, changed code is rebuilt, and the dashboard restarts on the
new version. `make check` prints a read-only readiness report.

The permanent dashboard is reached through private Tailscale HTTPS, manages
only Mu3Lab's curated services, and never treats arbitrary Docker projects as
trusted apps.

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

Baby Buddy is available as an optional family tracker for feeding, sleep,
diapers, pumping, growth, and other care activity. It appears in the React
Home workspace and My Apps catalog, stores its data under the normal Mu3Lab
data root, and uses Authentik trusted-header SSO on its private Tailnet route.

The control plane keeps leased, resumable, secret-redacted SQLite jobs and
structured events under `/srv/mu3lab/runtime`; a persistent worker reclaims
expired work after a restart. The supported AI slice is Ollama, FreeLLMAPI,
LiteLLM, and LobeChat. It verifies generated provider
configuration, streamed chat, embedding dimensions, private routes, and
Authentik-protected identity before reporting the slice verified. The dashboard
provides a full-screen LobeChat Chat view.

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
SurfSense, Firecrawl, Nextcloud, and AdventureLog. Mu3Lab seeds them for existing
LobeChat accounts and automatically for accounts created later.

Advanced Integrations separates automatically managed connections from apps
that still require a user-scoped credential or approval. Mu3Lab handles MCP
preparation, verification, chat registration, and lifecycle reconciliation
after the required application credential is available. Enabled MCPs start
after their application is healthy and stop before it stops; the worker
reconciles those states after a host restart. Verified Streamable HTTP tools
are bound and pinned only to their matching LobeChat agent, with approval
required for write tools. Connections → Advanced integrations also offers prepared
runtime status, per-tool permissions, an operator tool console, metadata-only
action history from the dashboard and LobeChat, job diagnostics, and reviewed
update status. Write calls in the console require a one-use confirmation.
Mealie uses a pinned stdio-to-HTTP bridge; Firecrawl uses its official HTTP
MCP server against the self-hosted API. Mu3Lab's small Nextcloud and
AdventureLog adapters expose scoped files and travel data operations. Baby
Buddy's official MCP remains manual because it acts as the user represented by
an API token copied from Baby Buddy settings.
All accepted MCP runtimes can be prepared while their apps are stopped. A
connection is not reported live until credentials, health, and tool discovery
pass; Nextcloud and AdventureLog also perform an app-data read check.

## Development

```bash
./install.sh --no-open   # first run: creates .venv and installs the control plane
make dev-setup           # linters, type checkers, and dashboard dependencies
make verify              # everything CI runs: lint, type check, tests, build
make format              # apply ruff and prettier formatting
```

Code layout:

- `ctl/api/` — the FastAPI control plane. `security.py` resolves the caller's
  Authentik identity and provides the `Operator`/`OperatorMutation`
  dependencies every route uses; `routes/` has one router per dashboard area,
  all under `/api/v1` (plus the unversioned `/api/health` probe).
- `ctl/service_ops.py` runs lifecycle jobs in the worker; `ctl/lifecycle/`
  holds the steps it sequences (runtime project generation, health checks,
  account linking, per-app setup).
- `dashboard/src/` — `api/` (typed client), `components/` (shared UI),
  `features/<area>/` (one folder per dashboard page), `lib/` (helpers).

Tests never touch the host's `/srv/mu3lab`: `tests/__init__.py` points
`MU3LAB_RUNTIME_ROOT` at an empty temporary directory.

CI runs ruff, mypy, the Python and dashboard test suites, ESLint, Prettier,
the dashboard build, YAML and Compose validation, and a secret scan.

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
