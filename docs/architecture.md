# Mu3Lab architecture

An app's folder is its source of truth. `apps/<id>/app.yaml` describes its
purpose, ports, sign-in method, account setup, rules, routes, assistant and
connectors. `docker-compose.yml` owns image versions and digests. Templates,
setup scripts, connector code and any exceptional `hooks.py` stay beside it.
`ctl/manifest/` validates these definitions; the registry adapts them for the
installer and dashboard. Platform roles are discovered by manifest capability.
Shared control-plane code must not name individual app IDs.

The dashboard sends authenticated, same-origin requests to `ctl/api/`. Python
Pydantic models define requests and responses. `make api-schema` dumps OpenAPI
and generates the dashboard's TypeScript models; CI checks both generated
files. The API queues durable jobs rather than performing installations in a
browser request. Jobs carry redacted progress and leased ownership so the
worker can resume interrupted work safely.

`ctl/engine/install.py` runs core and optional app installations through the
same sequence: validate the plan and installing identity, render a private
project, run pre-start checks, start containers, verify health, configure
accounts and sign-in, apply routes, and synchronize connectors and assistants.
`ctl/rules/` supplies reusable behavior declared under `rules:` in the manifest.
Rule settings are strict Pydantic models. Their hook points cover environment
preparation, pre-start checks, staged starts, post-start setup, post-health
setup and periodic checks. An app-specific hook receives a `HookContext` and
must use its supported Compose, environment, owner, logging and progress APIs.
Hooks handle exceptions to shared rules; they do not duplicate the engine.

`ctl/integrations/` wraps supported external interfaces. Authentik uses its
REST API with a machine credential. LobeHub uses its device approval flow and
per-person API keys. Vaultwarden uses its registration/organization interfaces
for bootstrap and invitations, and the pinned Bitwarden CLI for routine vault
operations. Each CLI session has a disposable private cache. Chat talks to
app connectors through a tool gateway with person/provider/app/version credentials.
Current shared application connectors are operator-only; household members have
ordinary chat without connector access. Direct/helper writes and console writes
create an immutable operation in the isolated gateway authority store. A human
approves the exact stored inputs through the authenticated dashboard, and the
gateway atomically claims one dispatch attempt after rechecking current policy.
Lost replies and interrupted dispatches remain unknown and are never resent.

`gateway_authority.py` owns the dedicated gateway SQLite database and encryption
key under `projects/mcp-gateway/authority/`. Only this directory, read-only policy
and metadata logs are mounted into the gateway. Policy revisions and committed
hashes invalidate old grants before permission changes; health confirms gateway
acknowledgement. Back up this database with its matching key as well as the
control-plane store. See [the authority implementation and recovery record](reviews/2026-10-08-gateway-authority-implementation.md).

`ctl/store/` owns one SQLite database, `/srv/mu3lab/state/mu3lab.db`, and numbered
SQL migrations. One private `secrets.key` encrypts scoped credential rows.
Job creation and encrypted job inputs commit together. App ownership has one
canonical record. Container-specific `.env` files are generated private
outputs, not another credential source. Application data remains in `data/`;
restic snapshots remain in `backups/`. Database and encryption key must be
preserved together.

The worker's `ctl/status/` reconciler observes host and app health every five
seconds and after job changes, independently of long-running installations.
It commits observations together. Page loads read these saved observations;
`/api/v1/snapshot` combines them with jobs, caller identity and chat readiness.
Every viewer gets the same health state, with actions filtered by their role.
The UI uses five display states and warns after 30 seconds without observations.
Personal checklist changes merge by item on the server, so devices agree.

## Adding an app

1. Copy a similar folder under `apps/` and give its manifest a unique ID and
   private ports. Pin its Compose images; declare dependencies and account mode.
2. List reusable rules with app-owned settings. Keep setup scripts and templates
   in the folder. Add `hooks.py` only when a supported app interface needs it.
3. Declare and lock any connector, its credential provisioning, health check,
   tool categories and assistant. Describe the supported mobile clients.
4. Run `make lint test`, then `make verify`. Validate installation, sign-in,
   chat tools and removal in a disposable VM using [acceptance.md](acceptance.md).
   Never use a working household stack as the installation test target.

UI development and builds run inside a pinned Node container. A normal tagged
release publishes verified dashboard assets and immutable connector images;
the installed updater offers normal releases. See [development.md](development.md)
for the commands and the release checks.
