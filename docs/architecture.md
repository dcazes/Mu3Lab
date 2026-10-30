# Mu3Lab technical deep dive

Mu3Lab is a single-host control plane for a curated household application stack. It provisions the host, materializes Docker Compose deployments, connects identity and private ingress, manages application lifecycles, and exposes operational state through a browser dashboard. Its other major integrations are password distribution, AI inference and application tools, personal calendars, encrypted backups and recovery.

The project aims to make independently developed applications behave like a coherent home cloud. It does not replace their databases or user interfaces, implement their business features itself, or provide a general-purpose container hosting service. The trust model is a household with trusted administrators, not mutually untrusted tenants.

This document describes the checked-in implementation. The [README](../README.md) summarizes the project; the [user guide](user-guide.md) describes the complete setup and use experience. Plans and dated reviews elsewhere in `docs/` describe work at different points in time. In particular, the earlier custom MCP approval gateway has been replaced by ContextForge: current tool calls do not pass through an exact-input approval queue.

## 1. Runtime topology and ownership

```text
Authorized device
  └─ Tailscale private HTTPS
       └─ Tailscale Serve → Caddy on host loopback
            ├─ Authentik: identity provider and selected forward-auth gates
            ├─ Mu3Lab dashboard/API: Uvicorn/FastAPI on 127.0.0.1:8787
            │    └─ SQLite state + encrypted scoped secrets
            │          └─ worker: leased jobs → Compose, app APIs and host commands
            └─ app routes: native OIDC, trusted identity headers, gate or local login

Chat → LiteLLM → FreeLLMAPI → configured external inference providers
               ├─ Ollama → local chat and embedding models
               └─ Speech → local transcription and speech synthesis (optional)

Chat → ContextForge → app connector → application API
Calendar → owner-scoped local store or Nextcloud CalDAV
Kopia server → authenticated backup hooks → worker stop/export/start → snapshots
```

Three application tiers appear in manifests:

| Tier | Current components | Responsibility |
| --- | --- | --- |
| Foundation | Caddy ingress, Authentik, Vaultwarden | Bootstrapped directly by the installer before normal app orchestration is available. Tailscale is a host service, not an app container. |
| Core | Ollama, FreeLLMAPI, LiteLLM, Open WebUI, ContextForge | Installed as the baseline suite through the common app engine. |
| Optional | Personal apps, LobeChat, Firecrawl, Speech, Kopia backups | Explicitly installed by an administrator, with declared dependencies. |

[`deploy/`](../deploy) contains two user-level systemd units: `mu3lab-ctl` serves the API and dashboard, while `mu3lab-worker` executes durable work and maintains observations. Both run from the installation checkout and its `.venv`. The installer enables lingering so the services survive logout. Containers have their own Compose lifecycle and restart settings.

These processes are separated for responsiveness and recovery, **not privilege isolation**. Both run as the installing user, can access the state database and encryption key, and inherit Docker access. Docker access is effectively host-root authority. The API also performs some direct external operations, including logs, account management and personal calendar work; it is not merely a write-only queue producer.

## 2. Source organization and app contracts

[`ctl/manifest/models.py`](../ctl/manifest/models.py) supplies strict Pydantic contracts; [`ctl/manifest/catalog.py`](../ctl/manifest/catalog.py) loads and validates the catalog. [`ctl/registry.py`](../ctl/registry.py) projects manifests into the flat `Service` interface consumed by lifecycle code. Platform services are found by capabilities through [`ctl/platform_apps.py`](../ctl/platform_apps.py), rather than embedding app IDs into generic orchestration.

Each `apps/<id>/` directory owns:

| Artifact | Meaning |
| --- | --- |
| `app.yaml` | Identity, category, tier, approved version, dependencies, health checks, ports, sign-in, account bootstrap, configuration, secrets, rules, integrations, backup exclusions, media folders, mobile instructions and optional chat assistant. |
| `docker-compose.yml` and overrides | Services, persistent mounts, networks and pinned image references. Compute and first-start variants are explicit Compose files. |
| Templates, scripts and `hooks.py` | App-specific settings and setup behavior that cannot be expressed by generic rules. |
| `connectors/<id>/` | Connector contract, Compose deployment, credential provisioning and any tool review or local implementation. |

The manifest is a deployment contract, not a sandbox for untrusted code. Hooks and scripts are trusted repository code with control-plane authority. The public API accepts known service IDs and typed settings; it does not accept arbitrary Compose files, shell commands or container images from the browser.

[`ctl/rules/`](../ctl/rules) implements reusable behavior: first-admin creation from environment, staged starts, first-owner guards, container scripts, disabling password login after bootstrap, per-person accounts and logins, provider routing, embedding prerequisites, hiding competing model providers, and backup-service integration. Each rule owns typed settings and lifecycle hook points.

[`ctl/engine/hooks.py`](../ctl/engine/hooks.py) provides `HookContext`, `StartPlan` and `AppHooks`. Context exposes the app, runtime project, controlled Compose wrapper, installing identity, logging, progress, environment persistence, rerendering and sign-in registration. Rules can alter a first-start plan, run initialization, then let the engine perform the final normal start. App hooks handle exceptional supported API interactions instead of duplicating the installation engine.

## 3. Host bootstrap and installation

[`install.sh`](../install.sh) is the shell entry point. It rejects root invocation, requires an apt-based environment and initial download utilities, establishes a sudo session, and prepares the pinned toolchain through [`tools/toolchain.sh`](../tools/toolchain.sh). `uv`, a managed Python 3.12 build, locked Python packages and the standalone Bitwarden CLI are kept in the checkout's tool directories; the installer does not replace system Python.

[`ctl/bootstrap/terminal.py`](../ctl/bootstrap/terminal.py) handles interactive input and progress. Fresh installs collect a name, email and a password of at least 12 characters, with confirmation and an acknowledgment to save it. The supplied owner password is used to bootstrap Authentik and Vaultwarden, not persisted as a reusable master password. Existing installs can request the existing vault login. Restore mode asks for the copied repository and backup password instead.

[`ctl/install.py`](../ctl/install.py) defines an ordered table of check/fix steps. Each step checks actual readiness before applying a fix, then verifies the result. Source fingerprints in [`ctl/bootstrap/stamps.py`](../ctl/bootstrap/stamps.py) distinguish an already-built component from changed source. Durable provisioning state and human acknowledgments are tracked separately from app installation jobs.

The major phases are:

1. Validate OS, CPU architecture and required ports; install base packages. The current architecture check accepts **x86-64 only**, despite some release tooling supporting multi-architecture builds. Debian 12+, Ubuntu 22.04+ and qualifying derivatives are recognized. WSL receives additional systemd caveats rather than equivalent native-Linux assurance.
2. Install/start Tailscale, grant the installing user operator access, and wait for browser-based device authorization and the private DNS name. The normal flow does not ask for a reusable Tailscale auth key.
3. Install or repair Docker Engine/Compose, group access and networks. Docker access can use the updated group without requiring the installer session to log out.
4. Prepare Docker address pools and optional NVIDIA container tooling. [`ctl/docker_config.py`](../ctl/docker_config.py) supplies a pool of `/24` networks from `10.210.0.0/16` if the configured capacity is insufficient. Applying daemon settings can restart Docker and affect unrelated workloads. GPU drivers are not installed.
5. Prepare dashboard assets, create the runtime directories, and—in restore mode—restore old state before generating new secrets or accounts.
6. Generate missing platform keys, install user services, start Caddy and Vaultwarden, create the initial vault account, publish its private route and configure supported browser policies.
7. Start Authentik and queue the core-app job in the worker. [`ctl/core_setup.py`](../ctl/core_setup.py) downloads every core image straight away but installs nothing until the dashboard is protected and the owner is known. Then prepare Authentik's owner and integration settings, provide container-to-tailnet access, enforce dashboard authentication, and publish dashboard ingress.
8. Hand the owner's identity to the core job, save the Authentik login to the owner's vault and verify private routes. Other generated logins reach the vault through the automatic vault sync once their apps exist.
9. Open the dashboard and wait for a **fresh authenticated operator dashboard load**. The installer does not wait for the core apps: they keep downloading and installing in the worker, and Home shows each app's progress.

Logs are written beneath the checkout's `.state/`. Re-running the installer resumes by checking current state; it does not fetch newer Git source. `start.sh` only restarts the existing control-plane service after checking that the environment and dashboard build exist.

## 4. Persistent state, credentials and rendering

[`ctl/runtime.py`](../ctl/runtime.py) defines the production root, `/srv/mu3lab`. `MU3LAB_RUNTIME_ROOT` provides an override for isolated environments; it is not a substitute for isolating Docker projects and external dependencies when exercising lifecycle operations.

```text
/srv/mu3lab/
├── data/       application databases, uploads and included content
├── media/      user-supplied large libraries: audiobooks, podcasts, ROMs
├── backups/    Kopia repository and restore destinations
├── state/      mu3lab.db, secrets.key, locks, observations and recovery state
└── projects/   materialized Compose deployments and private environment files
```

`RuntimePaths.runtime` is an alias for `state`, not a separate `runtime/` directory. Credentials are not maintained in a general top-level `secrets/` tree.

[`ctl/store/db.py`](../ctl/store/db.py) opens SQLite with foreign-key enforcement and WAL journaling. Numbered SQL migrations are serialized by process/thread locks and applied transactionally. A database with a schema newer than the code is rejected. Reentrant connection handling lets related operations participate in the same transaction, including job creation and encrypted workflow input persistence.

The store contains installations, canonical app owners, initialization progress, jobs/events/audit, configuration records, provider metadata, observations, personal checklist state, calendar state and access-revocation work. Application databases remain owned by the apps, not folded into Mu3Lab's SQLite database.

[`ctl/store/secrets.py`](../ctl/store/secrets.py) stores JSON values encrypted with Fernet under `(scope, name)`, with optional expiry. The single key is `state/secrets.key`. Key creation is locked, and the code refuses to invent a new key over existing ciphertext if the original is missing. Private state files use restrictive permissions; atomic writers in [`ctl/secret_file.py`](../ctl/secret_file.py) avoid partially published files. **The database and key must be preserved together.** This encryption does not protect against the installing user or a compromised API/worker that can read both.

[`ctl/app_settings.py`](../ctl/app_settings.py) separates operator configuration (`config:<KEY>`) from managed values (`env:<NAME>`) in the app's encrypted scope. [`ctl/service_config.py`](../ctl/service_config.py) validates declared fields, treats secrets as write-only, checks form revisions to prevent lost concurrent edits, and holds the app lock while changing settings.

[`ctl/engine/project.py`](../ctl/engine/project.py) copies deployment files into `projects/<id>` and computes environment values from stored settings, preserved/generated secrets, OIDC configuration, defaults, user configuration, rules, capability-based integrations and templates. It computes outputs before publishing them privately and atomically. `.env` files are generated outputs, not the canonical configuration database. Runtime consumers may read them, but an operator should change settings through the configuration layer rather than editing generated files.

## 5. Networking, routing and identity

[`apps/ingress/`](../apps/ingress), [`ctl/routes.py`](../ctl/routes.py) and [`ctl/authentik_blueprints.py`](../ctl/authentik_blueprints.py) implement private ingress. Tailscale Serve terminates private HTTPS on the server's tailnet name and forwards to Caddy listeners on loopback. Applications generally share that hostname with distinct HTTPS ports; the dashboard uses `8446`, Authentik `443`, and Vaultwarden `8443`. Copying only the hostname loses the destination app.

Application origins publish loopback ports rather than public/LAN listeners. Foundation routes have hand-reviewed configuration; optional routes are generated from manifests. A route's `access: open` means no additional proxy sign-in gate, **not public internet exposure or necessarily anonymous app access**. Many such applications enforce native OIDC themselves.

Sign-in strategies differ:

| Strategy | Implementation and examples |
| --- | --- |
| Native OIDC | Authentik issues app-specific identity; the app validates it and owns its session. Examples include Immich, Nextcloud, Mealie, Actual Budget, Outline, RomM and the chat apps. |
| Trusted headers | Caddy performs forward authentication and replaces caller-supplied identity headers. Baby Buddy, Grocy, Beaver Habits and Navidrome use app-specific identity handling. |
| Forward-auth gate | Authentik protects the route, while the app may still need its own account. SurfSense retains a vault-filled local login. |
| Independent local sign-in | Vaultwarden remains directly usable with Bitwarden clients and is not Authentik-gated. |
| Internal service | Ollama, Firecrawl and Speech provide APIs rather than normal user-facing app screens. |

Routes can include narrow path/token exceptions for native clients. A mobile/API bypass skips the browser gate only as declared; the downstream application must authenticate its token. Browser SSO alone cannot configure every native client.

The tailnet relay in [`ctl/tailnet_relay.py`](../ctl/tailnet_relay.py) and [`platform/tailnet-relay/`](../platform/tailnet-relay) solves an internal reachability problem: containers need to fetch Authentik metadata through the private HTTPS origin, but Tailscale rejects some container-originated connections. The relay listens on the Docker host address on port 443 and forwards encrypted traffic to Tailscale. It is an internal bridge, not a public application route.

Shared Docker networks include `mu3lab_frontend`, `mu3lab_backend`, `mu3lab_mcp` and `mu3lab_mcp_upstream`, alongside app-private networks. ContextForge bridges the chat-facing MCP network and connector-facing network; connector deployments also need their declared upstream-app connectivity. Network placement reduces unintended reachability but is not a comprehensive egress firewall or hostile-container isolation guarantee.

## 6. API authorization and household accounts

[`ctl/api/security.py`](../ctl/api/security.py) trusts Authentik identity headers only with a constant-time-checked shared ingress token from Caddy. It derives the caller's subject, username, validated email and groups from that authenticated hop. Operator groups include `mu3lab-operators` and `authentik Admins`; `mu3lab-household` provides member access.

Browser mutations require an HTTPS `Origin` matching the request host and an `x-mu3lab-csrf` HMAC bound to the forwarded Authentik session JWT. Dependencies distinguish operator, member, owner-scoped and verified-account mutations. The API rechecks authorization; hiding an administrator button in React is not its security boundary. Backup hooks are a deliberately separate machine-authenticated route with a narrowly scoped bearer token.

[`ctl/people.py`](../ctl/people.py) uses Authentik's API to create people and obtain one-use expiring recovery/invitation links. The administrator distributes the link; Mu3Lab is not an email delivery service. Recipients choose their own passwords. A household identity is separate from a vault registration and from Tailscale network membership.

[`ctl/access.py`](../ctl/access.py) records subject-keyed deactivation or demotion holds before relying on external revocations. These holds override old role claims immediately in Mu3Lab's API. External revocation targets are retried durably, so an unavailable dependency does not erase the request. This cannot retract downloaded content or universally revoke tokens created directly in every app. App-specific reconciliation, such as Navidrome password replacement, handles additional supported cases.

App onboarding stores the installing owner canonically and provisions first-admin accounts before exposing initial-user races where supported. Account modes include environment bootstrap, API bootstrap, OIDC first login and trusted-header access. Some apps need an actual first sign-in to finish ownership. Existing application data is not blindly reassigned; hooks can reject data with an unknown owner.

## 7. Vault provisioning and browser integration

[`ctl/integrations/vaultwarden/`](../ctl/integrations/vaultwarden), [`ctl/vault_setup.py`](../ctl/vault_setup.py) and [`ctl/vault_sync.py`](../ctl/vault_sync.py) use Vaultwarden bootstrap/organization interfaces and a pinned Bitwarden CLI. CLI sessions use disposable private cache directories.

The initial owner's master password is used during setup. Later automated distribution uses Mu3Lab's service account and a shared organization with a private collection for each matched person. Active Authentik people are matched to existing vault accounts by email. Generated app logins are matched by stable `mu3lab_id` fields; administrator entries include relevant service administration credentials and the backup password. Provider sign-up entries are conservative, create-only records rather than remotely created provider accounts.

The manual vault-seeding endpoint can ask for a master password and two-factor code, uses them for that request, and does not retain the master password. The system necessarily retains the credentials it generates and manages for apps; “master password not stored” does not mean Mu3Lab holds no secrets or cannot read managed shared credentials.

[`ctl/browser_extension.py`](../ctl/browser_extension.py) writes machine policies for detected Chrome/Chromium/Brave installations. They install Bitwarden and set its self-hosted base URL. Browser “managed” UI is a visible consequence. Other browsers and devices require manual installation and server selection. The extension still requires the user's vault sign-in.

## 8. Jobs, process safety and installation lifecycle

[`ctl/jobs.py`](../ctl/jobs.py) owns persistent jobs, transitions, events, idempotency and audit. Public job records exclude secret payloads; sensitive workflow inputs live in encrypted scoped storage. Mutation namespaces and request identity are used to distinguish retries from different operations rather than treating any repeated key as equivalent.

[`ctl/worker.py`](../ctl/worker.py) polls for work every two seconds. A claimed job has a 45-second lease renewed by a heartbeat thread every ten seconds. Dispatch is restricted to known core, service, MCP, provider, model, cleanup and platform-update handlers.

Two complementary mechanisms prevent concurrent destructive work:

- [`ctl/resource_locks.py`](../ctl/resource_locks.py) holds a kernel `flock` for the executing worker and locks affected resources. A process paused beyond its lease still holds its kernel lock, preventing a replacement worker from racing it.
- [`ctl/job_processes.py`](../ctl/job_processes.py) records spawned processes with PID, kernel start time and deadline. A new worker settles surviving commands before claiming more work or performing repairs. Earlier backup containers also receive bounded settling before new work proceeds.

[`ctl/job_guard.py`](../ctl/job_guard.py) checks cancellation and lease ownership at side-effect checkpoints. Cancellation is cooperative: an already-running command is allowed to finish, and certain data-swapping phases are explicitly uncancellable. Cancelling is not transactional undo. [`ctl/process.py`](../ctl/process.py) enforces whole-command deadlines, concurrent output draining, bounded output retention and bounded lines so subprocess behavior cannot indefinitely block a worker or exhaust its memory through logs.

The common installer in [`ctl/engine/install.py`](../ctl/engine/install.py) performs:

1. Validate the catalog service, required configuration and installing identity; remember the owner and initialization state.
2. Render the private runtime project and align the recorded app release; execute pre-start rules and validate Compose.
3. Download images and record immutable image digests.
4. Register/wait for OIDC configuration when needed; compute staged-start overrides and start containers.
5. Bootstrap accounts, complete staged starts and run post-health configuration.
6. Verify application health, publish the route and verify sign-in expectations.
7. Synchronize connector lifecycle, downstream integrations and chat assistants; publish completion or a structured failure.

[`ctl/install_batches.py`](../ctl/install_batches.py) stores dependency-ordered multi-app installation requests. Image downloading can run concurrently while application-changing jobs remain serialized. Batch state is reconciled after jobs and across restarts.

“Durable” means intent and progress survive the browser or worker restarting. It does **not** mean every operation can be replayed automatically. Updates and restores preserve explicit in-progress records; an interrupted data operation blocks further work until a supported recovery action is selected.

## 9. Image downloads, compute and capacity

[`ctl/download_manager.py`](../ctl/download_manager.py), [`ctl/image_downloads.py`](../ctl/image_downloads.py) and [`ctl/image_fetch.py`](../ctl/image_fetch.py) coordinate persistent image download state. The fetcher can retrieve image layers using parallel byte ranges, verify blobs against manifest digests, assemble OCI archives and load them into Docker. Cached/downloaded layers can be reused; image preparation and app startup are separate progress states.

[`ctl/app_sizes.py`](../ctl/app_sizes.py) obtains compressed layer sizes from registries and estimates unpacked sizes without downloading complete images where supported. Shared layers are deduplicated for plans. Estimates are not a capacity guarantee: user content, model weights, database growth, map data, backup retention and restore staging add storage needs.

[`ctl/compute.py`](../ctl/compute.py) resolves automatic or selected CPU/NVIDIA/AMD mode. An app uses a checked-in override only if it has one. Installed releases retain their recorded compute variant rather than silently changing with a setting. [`ctl/lifecycle/compute_switch.py`](../ctl/lifecycle/compute_switch.py) explicitly downloads and switches the same app release to another available variant, restarts it and attempts to restore the prior variant if startup fails. AMD has an Ollama override but no universal automatic driver/runtime setup; NVIDIA support likewise depends on working host drivers.

[`ctl/resource_usage.py`](../ctl/resource_usage.py) reads container cgroups for memory use, excluding reclaimable page cache in the displayed estimate. Disk accounting includes app data/media, volumes, images and containers, with shared-layer attribution. Expensive disk measurements are cached and refreshed periodically or after relevant jobs.

[`ctl/disk_cleanup.py`](../ctl/disk_cleanup.py) identifies recorded obsolete images and projects. It keeps current and previous app releases and helper images, avoids cleanup during active work/downloads, and requires confirmation for removed-app data. Its targeted cleanup is distinct from the broader top-level uninstall script.

## 10. Status observations and dashboard behavior

[`ctl/status/reconciler.py`](../ctl/status/reconciler.py) observes apps on a worker thread independently of long-running jobs. It batches Compose/Tailscale state, probes services with bounded concurrency, and commits app, core, network and host observations together. The fast observation interval is five seconds when recently viewed; idle observations slow to five minutes. Separate upkeep routines also use active/idle pacing.

Status combines container state, health checks, route readiness, sign-in verification, initialization and pending work. It is not just the result of `docker ps`. [`ctl/status/display.py`](../ctl/status/display.py) projects six states: `running`, `stopped`, `working`, `checking`, `not_installed`, and `needs_attention`. Observations are evidence of particular checks at a time, not proof that every app feature or human login has been exercised.

[`ctl/api/routes/snapshot.py`](../ctl/api/routes/snapshot.py) combines stored observations with jobs, identity and chat readiness. Ordinary dashboard snapshot loading does not synchronously probe Docker to invent fresh health. Action availability and personal fields depend on the caller, while underlying app-health observations are shared.

The React/TypeScript app in [`dashboard/src/`](../dashboard/src) provides Home, Apps/Discover, app details, Chat, Calendar and Settings. The main shell has responsive navigation, a command palette, theme selection, confirmations, job dialogs and distinct loading/offline/signed-out/error states. Heavier settings/calendar pages are lazy-loaded. API types derive from Python contracts through OpenAPI, with common client handling for errors and mutations. Settings include AI, chat integrations, voice keys, people, security/backups, system, sign-in and release history.

Home's checklist is owner-scoped server state; edits merge by item. Resource views show shared host measurements. The shell marks observations stale after seven minutes, accommodating idle polling. Failed sections can retain prior data while visibly reporting that refresh failed.

[`dashboard/public/sw.js`](../dashboard/public/sw.js) caches the application shell and static assets per release. It never caches `/api/` responses. A cached shell enables an offline notice and home-screen installation, not offline server operations or offline access to personal dashboard data.

## 11. AI inference, local models and speech

[`ctl/provider_catalog.py`](../ctl/provider_catalog.py) declares accepted provider types and setup metadata. The current catalog includes Cerebras, Google AI Studio, Groq, Hugging Face, NVIDIA, OpenRouter, Mistral and Zhipu. Mu3Lab stores submitted keys encrypted and exposes only safe metadata through the API.

[`ctl/provider_ops.py`](../ctl/provider_ops.py) delegates provider validation and model discovery to FreeLLMAPI, then verifies routing. FreeLLMAPI owns its provider model choices/ranking and failover; Mu3Lab does not duplicate a permanent list of external models. A provider account, valid key and provider-side allowance remain external prerequisites. A syntactically accepted key is not a verified inference connection.

LiteLLM exposes stable model aliases to consuming apps. Ollama supplies local chat and embedding models; FreeLLMAPI supplies external chat routes. [`ctl/local_models.py`](../ctl/local_models.py) restricts local selection to a reviewed list with model tags, size estimates and embedding dimensions. The selected local chat model is offered as `mu3lab-local`; document embeddings use `mu3lab-embed`.

Model changes are durable jobs: download and check the new models first, update routing, verify through LiteLLM, rewire consumers and clean up unused models as appropriate. Index-bound consumers such as SurfSense prevent incompatible embedding changes; other consumers may require reindexing.

The chat setting is an ordered list of sources, `local` and `outside` (default `[outside]`): the first serves `mu3lab-chat` and `mu3lab-fast`, and the second is exposed as a chat choice (`mu3lab-local` or `mu3lab-outside`), never as an automatic fallback. Its three-word form (`outside_allowed`, `local_first`, `local_only`) is derived from the order. Without `outside`, generated LiteLLM routing points managed chat aliases to the local model, removes the outside-provider route and disables the provider keys in the provider gateway. Without a configured local model, chat fails rather than falling back externally. This is an inference-routing policy, not an outbound network firewall for all applications.

[`apps/speaches/`](../apps/speaches) provides optional Whisper transcription and Kokoro synthesis. Its hook prepares models; manifest integrations point LiteLLM and chat at `mu3lab-stt` and `mu3lab-tts`. Open WebUI uses browser voice settings when the speech capability is absent. Server speech processing can be local while the resulting chat text still goes to an outside model if the household allows it.

[`apps/livekit/`](../apps/livekit) is the household voice assistant: a main agent hands people to app assistants through ContextForge with their own keys, and changes run only after a spoken yes. [`ctl/voice_settings.py`](../ctl/voice_settings.py) and [`ctl/voice_tools.py`](../ctl/voice_tools.py) write the agent's `settings.json` and `people.json`, read at each call, so Settings, Voice assistant needs no restart. Its web search goes through [`apps/searxng/`](../apps/searxng), the household's private metasearch. An experimental full-duplex mode ([`ctl/duplex_voice.py`](../ctl/duplex_voice.py)) replaces the separate steps with MiniCPM-o 4.5, stopping every other app that uses the graphics card while it is on.

[`ctl/voice_keys.py`](../ctl/voice_keys.py) issues owner-scoped LiteLLM keys restricted to speech models and rate-limited. The value is shown once with no-store response headers; Mu3Lab retains no plaintext copy of that personal key, and LiteLLM stores its hash. Compatible external clients use the private gateway address and bearer key. This is an API integration facility, not automatic installation of another voice product.

## 12. Chat assistants and ContextForge

[`ctl/chat_ops.py`](../ctl/chat_ops.py) selects the household chat app and synchronizes every installed supported chat workspace. Open WebUI is core/default; LobeChat is optional. Changing the selected app changes the dashboard destination, not conversation storage or a history migration.

Open WebUI is managed through its admin API and shares app assistants with the household. Every new chat starts with a tool-less main assistant (`mu3lab-assistant`) that lists the live app assistants and tells people to switch with `@name`; Open WebUI's sub-agents reuse the parent model and tools, so they cannot route to app assistants. LobeChat requires a per-person device-approval connection and uses its supported APIs to maintain assistants. An assistant is derived from an installed app's manifest and a live connector registered with ContextForge; merely declaring an assistant does not imply live data access.

[`ctl/mcp_catalog.py`](../ctl/mcp_catalog.py), [`ctl/mcp_ops.py`](../ctl/mcp_ops.py) and [`ctl/mcp_credentials.py`](../ctl/mcp_credentials.py) load connector contracts, provision credentials through app-owned scripts, start connectors after their app is healthy, check health/tool discovery and stop them before their app stops. Connectors may be upstream packages, local adapters, or MCP servers built into the app itself (`builtin: true`: no container; the app joins `mu3lab_mcp_upstream`). Connectors for apps that keep per-person data private (Dawarich, Beaver Habits) are not switched on at install, because assistants and their credentials are shared. [`platform/mcp-adapter/server.py`](../platform/mcp-adapter/server.py) implements bounded Streamable HTTP MCP surfaces for Nextcloud file operations, AdventureLog collections/locations, Beaver Habits, the Audiobookshelf, Navidrome and RomM libraries, and read-only Authentik and Kopia views for administrators; it rejects unsafe paths and caps request/response sizes. Other connector code lives beside its app.

[`ctl/contextforge_ops.py`](../ctl/contextforge_ops.py) registers a live connector as a ContextForge gateway and creates its virtual server the first time. Initial tool selection comes from default-on categories in `review.yaml` where present. **A connector without a review starts with its offered tools.** After creation, operators own the virtual server's tool selection; reconciliation does not overwrite their choices. Newly discovered tools are not automatically added to an existing virtual server. Stopping a connector disables its gateway while preserving configuration.

Caller identity and app authority are separate:

| Hop | Authentication and authority |
| --- | --- |
| Open WebUI → ContextForge | The person's Authentik token is forwarded using `system_oauth`; ContextForge is configured to trust the chat provider with audience checking. |
| LobeChat → ContextForge | Mu3Lab provisions a key per person and virtual server and stores it encrypted for reuse. |
| ContextForge → connector | Connector bearer credentials managed by Mu3Lab. |
| Connector → app | The app credential provisioned/configured for that connector, which can represent a shared or privileged app account rather than the chatting person's account. |

ContextForge supplies tool configuration and call auditing. Operators sign into its administration UI through Authentik. **Household members receive assistants too, and enabled write tools run without a separate Mu3Lab approval step.** Instructions such as “ask before changing” in an assistant prompt are guidance to the model, not enforced authorization. Per-person gateway logging must not be mistaken for per-person downstream data isolation. The catalog does not expose host/Docker/vault administration as ordinary app-assistant tools, but that does not limit enabled app-data tools to read-only actions.

The [connector reference](chat-connectors.md) covers operator control and future ideas, including a parent assistant and possible future approval hooks. Those are not current guarantees.

## 13. Personal calendar storage and synchronization

[`ctl/api/routes/calendar.py`](../ctl/api/routes/calendar.py) requires an authenticated subject for reads and owner-scoped, CSRF-protected mutations. It dispatches between [`ctl/local_calendar.py`](../ctl/local_calendar.py) and [`ctl/nextcloud_calendar.py`](../ctl/nextcloud_calendar.py).

Without a Nextcloud connection, events live in the encrypted local store. Each person has separate records and locks; stored full iCalendar text preserves fields such as recurrence, notes and reminders when copying events. Recurrence expansion uses `icalendar` and `recurring-ical-events`. This storage is not a CalDAV server for phone clients.

A Nextcloud connection uses the user's authorized device/app credential, stored encrypted. The browser connection flow uses Nextcloud Login Flow v2: start authorization, open its login page, poll until approved, and select one calendar. Supported server-side provisioning paths also exist. Credentials are scoped to the known service rather than arbitrary user-entered server URLs.

Events are read through CalDAV with range limits and revision/ETag checks. A temporarily unavailable connection can return marked stale cached results. Event writes do not silently overwrite a concurrent revision; conflicts surface as errors or explicit choices.

Before disconnecting or replacing a connected calendar, the code exports its events into local storage where possible. Disconnect is best-effort: export or remote credential-revocation failure returns a warning, but the local connection is still removed. Do not infer successful preservation or remote revocation from an `ok` response alone. Local changes track original ETags and deletions. Reconnection does not silently upload during a read: explicit authenticated sync pushes local events and removes confirmed local copies. If Nextcloud changed or deleted an event meanwhile, the local copy remains conflicted until the user chooses a version. Interrupted uploads are handled as unconfirmed work, not assumed success.

## 14. Application-specific integrations and limits

The common engine does not erase upstream differences. The current optional catalog has the following implementation responsibilities:

| App | Mu3Lab integration and important boundary |
| --- | --- |
| Actual Budget | Native OIDC, initial-owner guard and a connector requiring an Actual server password and selected Budget Sync ID. Mu3Lab cannot infer these; OIDC setup alone does not establish connector readiness. The app still owns budget creation/import and budgeting behavior. |
| AdventureLog | Environment/bootstrap owner setup, registered OIDC social application and a small API-backed connector. |
| Audiobookshelf | API bootstrap of the initial owner, OIDC and role claims, default libraries and mobile callback configuration. Large media is outside normal backups. |
| Baby Buddy | Trusted-header user creation via app-side authentication integration; household users get caregiver access. Native clients use device tokens. |
| Beaver Habits | Trusted email identity with a script reconciling per-person accounts and separate habit lists. |
| Dawarich | OIDC owner adoption, configurable local Photon region and phone-client setup metadata. Location data is backed up; the downloadable Photon index is excluded. |
| Firecrawl | Internal web extraction service and connector, with multiple backing services and conservative crawl concurrency. No normal user-facing launch page. |
| Grocy | Trusted-header integration, currency configuration and community connector provisioning. Stock records are supplied by the household. |
| Immich | API owner/configuration hooks, native OIDC, optional GPU Compose variant and a choice of declared connectors. Users still configure mobile uploads. |
| Mealie | OIDC ownership and community connector. Recipes and meal plans remain Mealie data. |
| Navidrome | Trusted-header browser login and API-created per-person native-client passwords saved to vault collections. A service account reconciles roles; deactivation replaces the person's password. Nextcloud can expose the shared Music folder. Music is included in data backups. |
| Nextcloud | Staged/environment owner setup, configuration scripts, OIDC, shared music integration, CalDAV calendar integration and a file-oriented MCP adapter. Dashboard calendar credentials are distinct from the shared connector credential. |
| Outline | OIDC and an app-owned script that establishes the owner's workspace before ordinary users can become first administrator. |
| Paperless-ngx | First-admin bootstrap and native OIDC, with a separate connector. Mobile clients can require app-issued tokens. |
| RomM | OIDC, media mounts for supplied ROMs and optional metadata-service secrets. App database and save data are included in backups; ROM media is excluded. |
| SurfSense | Authentik gate plus an API-provisioned local account saved to the vault, managed AI/embedding configuration and connector. Its privileged sandbox is disabled; the search index constrains embedding-model changes. |
| LobeChat | Optional chat workspace with OIDC, private object storage and per-person connection approval. |
| Speech | Internal local audio APIs integrated through LiteLLM and chat; model configuration and CPU/NVIDIA variants. |
| Kopia backups | Schedule, retention and browser supplied by Kopia; app-consistency hooks and recovery orchestration supplied by Mu3Lab. |

Mobile links, setup methods and caveats are manifest data rendered by the dashboard, not evidence that every client supports identical authentication. Additional app proposals in `docs/expansion/` are not part of this installable catalog.

## 15. Backups, consistency and recovery

[`ctl/backup_service.py`](../ctl/backup_service.py) integrates the optional Kopia server. Kopia owns scheduling, retention and repository maintenance. Mu3Lab reconciles sources/policy and coordinates consistency. Installing Mu3Lab alone does not enable scheduled household backups.

Each included app has a `/data/<app>` source mounted read-only into Kopia; `/data/mu3lab` holds an exported control-plane snapshot. Before/after actions call the restricted `/api/v1/backup-hooks/*` endpoints. The before action validates the source, records the release, holds the app and queues worker work to stop it. A refused essential hook skips the snapshot. The after action restarts it according to recorded intent. Reconciliation can recover apps left stopped by missing after-actions. After a restore swaps directories, the backup container's mounts must be refreshed before that source is accepted again.

Schedules are off, six-hourly, twelve-hourly, daily or weekly; the default is Sunday 03:30 in the host's local time. Kopia can catch up missed runs. Retention preserves the newest ten plus daily, weekly and monthly history. Weekly sampled verification reads 5% of stored data. These checks do not establish that every possible recovery path is successful.

[`ctl/platform_backup.py`](../ctl/platform_backup.py) uses SQLite's backup API to export a consistent control database, preserves its encryption key, and includes deployment state needed to reconstruct applications. Core apps are backed up as separate sources. Source snapshots are not one atomic timestamp across the whole household stack.

[`ctl/backups.py`](../ctl/backups.py) also runs one-off Kopia commands in a pinned container with networking disabled and narrowly scoped mounts. App updates use this path for mandatory pre-update snapshots even when the scheduled server app is absent.

Backup scope follows deployed data mounts and manifest exclusions. `media/` libraries are excluded; Ollama weights and Dawarich's Photon index are treated as downloadable data. The repository at `backups/kopia` is encrypted with a generated stable password held in the secret store, recovery sheet and administrator vault entries. It remains local. Off-device replication and preservation of excluded libraries are operator responsibilities.

App restore in [`ctl/lifecycle/restore.py`](../ctl/lifecycle/restore.py) validates snapshot membership/layout and available staging space, unpacks beside live data, captures the current deployment, stops the app, renames current data aside and promotes the restored data. It restores the matching release/deployment when recorded, starts and checks the app, then removes superseded data after success. Errors trigger an attempt to put original data/settings back. Recovery failure is surfaced rather than reported as success.

In-progress records deliberately survive crashes. An interrupted restore requires “put back”; an interrupted update offers undo or, where valid, keep-new-version. Unrelated lifecycle changes are refused while that app needs recovery. Staging plus retained originals means restore needs substantially more free space than the compressed backup size.

[`ctl/rebuild.py`](../ctl/rebuild.py) implements `./install.sh --restore` on a replacement host. It refuses to overwrite an existing installation, copies/opens the supplied repository, restores control state before new secrets are generated, restores available core and app snapshots, and resumes normal bootstrap. Old queued work is not replayed as new work; recovery resets runtime-specific state and requires appropriate connections to be re-established. The code tracks repository identity and owned-file inventories for safe retries. If it cannot prove retry cleanup is safe, it preserves remaining files and requires manual recovery. Excluded media and models require separate restoration or downloads.

The detailed manual procedure is in [backup recovery](backup-recovery.md). Older text about the retired tool-approval queue is historical; the current ContextForge tool model is described above.

## 16. App versions and platform updates

[`ctl/lifecycle/app_releases.py`](../ctl/lifecycle/app_releases.py) distinguishes the catalog's approved release from the deployed release. Runtime `docker-compose.digest.yml` pins exact images and records the compute variant. Release history and deployment bundles allow operations to recover settings, generated files and storage layout as well as images. Re-rendering or updating Mu3Lab must not silently upgrade an optional app's data to new image versions.

[`ctl/lifecycle/update.py`](../ctl/lifecycle/update.py) checks that the requested approved version is still current, downloads target images while the app runs, captures a deployment bundle and records an in-progress operation. It stages/validates definitions, stops the app, creates a pre-update backup, writes the target release and starts it. If startup fails, it attempts to restore both the old deployment and its pre-migration data. Downgrading only the image would be unsafe after a database migration. Supporting-image changes can be offered even when the main application's version label is unchanged.

[`ctl/self_update.py`](../ctl/self_update.py) updates the control plane from normal `vMAJOR.MINOR.PATCH` releases on eligible clean checkouts. Development/edited checkouts remain manual. It asks the new code for its installer plan; steps requiring privileged setup cause the dashboard update path to stop and direct the administrator to the terminal. It synchronizes dependencies/assets and applies permitted setup before restarting services.

[`ctl/platform_releases.py`](../ctl/platform_releases.py) downloads dashboard assets and `release-images.json`, verifies consumed files against the release's `SHA256SUMS`, checks the dashboard's source fingerprint, and applies immutable images to tagged runtime projects instead of local builds. Development checkouts build locally in the pinned Node container. Release workflows publish attestations, but the runtime asset code's checksum verification should not be described as independent cryptographic verification of those attestations.

A separate guard in [`ctl/update_guard.py`](../ctl/update_guard.py) is copied outside the changing checkout and launched independently. It verifies dashboard response and service survival after restart and attempts rollback when needed. Database schema/checkpoint safety checks prevent blindly putting an old database back over intervening state. This is a bounded recovery mechanism, not a guarantee that any arbitrary core-app migration can always be reversed.

## 17. Removal and retained state

[`ctl/lifecycle/uninstall.py`](../ctl/lifecycle/uninstall.py) handles individual catalog apps. It disables connectors and routes/sign-in integration, removes deployment resources, and applies the selected data policy. Data discovery uses installed Compose mounts and avoids folders other apps share. Kept data retains required settings/release information for reinstall. Large `media/` folders are outside this app-data deletion path, and backup history is separate from live app removal.

[`uninstall.sh`](../uninstall.sh) is materially broader. It stops user services, removes private routes, app containers/volumes/networks/images and browser policies, cleans generated checkout files, and recursively deletes **all of `/srv/mu3lab`**, including backups and media. Its image cleanup also includes a dangling-image prune. `--everything` additionally purges Docker and its storage, Tailscale and its local state, and NVIDIA container tooling; unrelated Docker workloads are affected. System Python, base tools, GPU drivers and Git-managed source are retained. `--dry-run` previews commands without applying them.

## 18. Diagnostics, development and validation boundaries

Systemd/journald captures API and worker logs; [`ctl/logs.py`](../ctl/logs.py) preserves severity and reduces routine polling noise. Job events provide operation progress and redacted failures. [`ctl/history.py`](../ctl/history.py) prunes older event/call history after 90 days and audit history after 365 days, while retaining job records and the newest relevant job logs. Current ContextForge call logging is a separate system from legacy call tables in Mu3Lab's store.

The typed API uses Pydantic requests/responses, common contract routes and structured errors with a code, message and recommended action. [`tools/api_schema.py`](../tools/api_schema.py) generates OpenAPI and TypeScript contracts; committed generated files let CI detect drift. App/catalog/Compose validation and the no-app-ID architectural check live under [`tools/`](../tools).

The [Makefile](../Makefile) defines developer checks; [CI](../.github/workflows/ci.yml) invokes `make verify` and `make verify-security`, then publishes an SBOM. The release workflow builds/publishes connector images and dashboard artifacts. A workflow's multi-architecture output does not expand the installer's x86-64 support matrix. Local verification also does not establish registry permissions or real-device first-install/rebuild acceptance.

Use [development](development.md) for commands and isolation guidance and [acceptance](acceptance.md) for real-device scenarios. A UI preview connected to a live API can change the live installation. An alternate state path alone does not make privileged installation work safe to run against a production host.
