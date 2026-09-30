# Mu3Lab application review — September 30, 2026

Reviewed commit: `80bfe599b64cd346630aeff300e614b0f04eef16`.

## Assessment and scope

The app has a sound starting structure: a curated registry, a React/TypeScript dashboard, FastAPI routers, persistent SQLite jobs, a separate worker process, loopback ingress through Tailscale/Caddy, and Authentik identity. The existing automated checks are substantial. Nevertheless, the current implementation has important data preservation, concurrency, recovery, and credential revocation defects. Resolve the five P1 findings below before relying on it for important personal data.

This is a repository-wide architectural and implementation review, with deeper tracing of authentication, job creation/retry/cancellation, service lifecycle, credential storage, calendar operations, provider routing, MCP connections, deployment definitions, dashboard request handling, and CI. It is not a claim that every line or every upstream container was exhaustively audited. Requirements were inferred from README.md, PLAN.md, manifests, and existing tests; those sources disagree in several places. No complete external set of acceptance criteria or compliance requirements was supplied.

Production code was not changed. The review used temporary dependencies, test fixtures, and mocked host/network actions. No application was installed, stopped, reset, or uninstalled on the host.

## Validation results

| Check | Result |
|---|---|
| Python unit/integration-style suite | 426 tests passed |
| Dashboard suite | 66 tests passed across 6 files |
| Ruff lint and format | Passed; 117 files formatted correctly |
| mypy | Passed; 75 source files |
| ESLint / Prettier | Passed |
| TypeScript / Vite production build | Passed |
| Shell syntax | Passed for install, start, uninstall and VM/login scripts |
| YAML parsing | 34 files parsed |
| CI Compose interpolation validation | Passed |
| npm audit | 4 affected packages: 1 critical, 1 high, 2 moderate; development tooling |
| Tracked private .env/runtime/data paths | None found |
| Targeted credential-pattern search | No matches for selected private-key, AWS, GitHub and OpenAI token patterns |

The stock Python run initially failed because dependencies were absent; the complete run above used a temporary virtual environment with requirements-dev.txt. The targeted secret search is narrower than Gitleaks and does not establish that the entire Git history is secret-free. Gitleaks was unavailable locally. CI defines a Gitleaks check, but its remote result was not verified here.

## Findings

Priority: **P1** = high-impact correctness/security/data preservation issue; **P2** = material reliability, security boundary, or requirements defect. Evidence labels distinguish isolated reproductions from source tracing.

### F01 — P1: Concurrent credential writes can lose records or corrupt the encrypted store

References: [workflow storage](../../ctl/workflow_secrets.py#L66), [identity save](../../ctl/workflow_secrets.py#L100), [provider storage](../../ctl/provider_secrets.py#L60), [calendar storage](../../ctl/calendar_secrets.py#L43).

All three secret stores perform unlocked whole-file read/modify/write updates. API requests and the worker can read the same snapshot and each replace it, losing the other update. A fixed `.enc.tmp` path also allows two writers to overwrite the same temporary file or encounter a missing file during rename. First-use key creation is similarly uncoordinated.

**Evidence:** An isolated two-thread workflow save, synchronized so both read the original snapshot and with the physical writes serialized, retained **one of two** identities. This isolates lost updates from the additional temporary-file race. No real credentials were used.

**Fix:** Use transactional storage with individually encrypted rows, or an interprocess lock covering key creation and the entire read/modify/write operation. Unique temporary paths alone do not prevent lost updates. Create private files with restrictive permissions at open time, rather than applying chmod after writing. Add simultaneous save/delete/cleanup and first-use tests.

### F02 — P1: Editing a calendar event erases fields outside the dashboard form

References: [event update](../../ctl/nextcloud_calendar.py#L742), [calendar serializer](../../ctl/nextcloud_calendar.py#L523), [dashboard edit form](../../dashboard/src/features/calendar/CalendarPage.tsx#L80).

The dashboard submits title, start, end, all_day, and revision. The backend constructs a new VCALENDAR and replaces the entire existing DAV resource. Existing DESCRIPTION, LOCATION, VALARM reminders, ATTENDEE/ORGANIZER information, attachments, and other properties are not preserved. Editing only a title can therefore destroy meaningful event data. The ETag prevents overwriting a competing revision, but does not protect fields discarded by serialization.

**Evidence:** Source tracing plus the actual serializer output: a normal dashboard edit payload produces none of DESCRIPTION, LOCATION, VALARM, or ATTENDEE.

**Fix:** Fetch the original resource, verify the revision, mutate only supported properties, preserve other components and properties, and PUT with If-Match. Alternatively reject editing events whose semantics the editor cannot safely preserve. Test a title-only edit of an event containing notes, location, attendees, reminders, and timezone definitions.

### F03 — P1: Cancellation and lease loss do not reliably stop executor side effects

References: [service executor](../../ctl/service_ops.py#L975), [job transition](../../ctl/jobs.py#L180), [worker heartbeat](../../ctl/worker.py#L29).

Cancellation immediately marks a job cancelled and releases its lease, but service/provider/MCP executors do not consistently check cancellation or lease ownership before later operations. A heartbeat failure only ends the heartbeat thread. Job transitions do not require the current lease owner. A cancelled runner can continue changing state, and a runner whose lease expired can overlap with reclaimed execution.

**Evidence:** A mocked restart cancelled during Compose still performed subsequent MCP synchronization and updated installation state, then raised `invalid job transition: cancelled -> succeeded`. The job remained cancelled despite the side effects.

**Fix:** Pass an execution context containing the lease owner and cancellation status into every dispatcher. Check it before each external side effect and durable state update; fence updates by lease owner/generation. Distinguish cancellation requested from cancellation acknowledged. Define what happens to an in-flight external command. Add cancellation-mid-step and lease-reclaim tests, including destructive uninstall.

### F04 — P1: Provider removal can report success while the key remains usable in the gateway

References: [remove dispatcher](../../ctl/provider_ops.py#L285), [gateway cleanup](../../ctl/provider_ops.py#L163).

Removal deletes the local record first, ignores `_reconcile()`'s failure result, and marks the job successful. Gateway cleanup also catches deletion/login errors and only logs them. The code documents that FreeLLMAPI import is additive: omission from the config does not remove an existing key. A gateway failure can leave the provider key routing after the dashboard reports it removed, and the ordinary connection row needed to retry is gone.

**Evidence:** An isolated removal with `_reconcile()` returning failure still finished in `succeeded`. A gateway cleanup login failure returned normally after logging one warning.

**Fix:** Track a durable removal-pending state, revoke/disable the gateway credential, verify absence, and only then complete local removal. Surface gateway errors as retryable job failures. Test restart/import failures and gateway DELETE failures.

### F05 — P1: The documented backup copy misses Authentik's persistent data

References: [Authentik storage](../../core/authentik/docker-compose.yml#L4), [README data and backup guidance](../../README.md#L276).

Authentik PostgreSQL, media, and templates use Docker named volumes. They are not bound under `/srv/mu3lab/data`. The README says application data resides under `/srv/mu3lab` and suggests copying `/srv/mu3lab/data` until backups ship. Following that instruction omits identity accounts and provider configuration, leaving a restore without essential sign-in state.

**Evidence:** Compose source review; no production volume inspection or restore was performed.

**Fix:** Move these stores to the declared data root with an explicit migration, or document and implement a backup of the named volumes and a consistent PostgreSQL dump. Do not treat a running database directory copy as a verified backup. Validate restoration of Authentik accounts, signing/configuration state, and app access in a throwaway environment.

### F06 — P2: Retrying a job drops the account identity handoff

References: [retry endpoint](../../ctl/api/routes/jobs.py#L65), [job retry](../../ctl/jobs.py#L305), [install identity requirement](../../ctl/service_ops.py#L204).

`retry()` creates a new job ID, but the endpoint does not supply a new verified identity or copy a valid identity handoff. Environment-bootstrap installations require a job-specific identity. Retrying a failed Nextcloud, Paperless, or AdventureLog install through the generic job UI can immediately fail its account preflight again.

**Evidence:** The original failed job had an identity; its newly queued retry had none.

**Fix:** Reauthorize and attach the appropriate current caller identity before the retry becomes claimable. Do not silently transfer another user's ownership. Test generic retries of account-provisioning and identity-configuration jobs, including expired identities.

### F07 — P2: Core and foundation installs do not enforce the advertised immutable image policy

References: [core setup](../../ctl/core_setup.py#L362), [optional-app pinning](../../ctl/lifecycle/materialize.py#L343), [ingress image](../../core/ingress/docker-compose.yml#L13), [Authentik images](../../core/authentik/docker-compose.yml#L4).

Optional app installs resolve images and create digest overrides. The core loop starts Ollama, FreeLLMAPI, and LiteLLM from source Compose files without calling that pinning step. Foundation Caddy/Authentik also include mutable tags, including `postgres:16-alpine`. Checked-in version tags constrain versions but are not immutable digests. Fresh installations or image refreshes can consume changed images without a reviewed digest change.

**Evidence:** Installer/core control flow and Compose definitions; images were not pulled.

**Fix:** Apply one reviewed digest/materialization contract to every deployable stack, including dependencies and locally built adapter base images. Preserve installed digests on ordinary reconciliation; change them only through an explicit reviewed update.

### F08 — P2: SQLite connection cleanup depends on garbage collection

References: [JobStore connection](../../ctl/jobs.py#L92), [ControlState connection](../../ctl/control_state.py#L89), [batch connection](../../ctl/install_batches.py#L49), [download store](../../ctl/image_downloads.py#L30), [provisioning store](../../ctl/provisioning.py#L57).

`with sqlite3.Connection` commits or rolls back; it does not close the connection. These stores repeatedly return raw connections without explicit close. Their internal references can defer release until cyclic garbage collection. Dashboard polling and parallel download updates make this frequent; every connection also repeats schema setup.

**Evidence:** With garbage collection temporarily disabled in an isolated process, 100 `jobs()` reads increased open file descriptors by **201**. Garbage collection reclaimed them afterward. This is nondeterministic resource cleanup, not evidence that descriptors can never be reclaimed.

**Fix:** Provide a contextmanager that initializes, commits/rolls back, and closes in finally. Separate schema migration from request-time connections. Test cleanup after success and exceptions.

### F09 — P2: Native SSO apps exclude ordinary household users

References: [OIDC application bindings](../../ctl/authentik_blueprints.py#L454), [OIDC contracts](../../ctl/identity.py#L24), [product household policy](../../PLAN.md#L23).

The generic OIDC blueprint binds every native app only to `authentik Admins` or `mu3lab-operators`. A normal household user cannot complete SSO for Nextcloud, Immich, Mealie, or the other native OIDC apps unless granted platform operator/admin membership. Proxy-gated apps use a different policy, explicitly allowing admitted household users. This contradicts the documented household sign-in goal and pressures operators to grant excessive privileges.

**Evidence:** Blueprint generation and identity contract tracing. A live multi-user Authentik flow was not run.

**Fix:** Separate application-user admission from platform administration; retain operator restrictions for the control plane and administrative gateway UIs. Add owner/operator/household/rejected-user acceptance tests across each auth mode.

### F10 — P2: MCP connections reuse one app owner's credential across all matching chat users

References: [connector SQL](../../ctl/lobehub_ops.py#L304), [automatic owner credentials](../../ctl/mcp_credentials.py#L48), [default agents](../../ctl/lobehub_ops.py#L127).

One encrypted application credential is inserted into every matching agent-scoped connector, without filtering by the owning app user or Authentik subject. Agents themselves are seeded for all LobeChat users. For Nextcloud this credential belongs to the chosen administrator's files; for Paperless it is a superuser token. Consequently different admitted chat users operate with the same app identity instead of their own app permissions. Under current OIDC bindings, those users are operators/admins; this is not a demonstrated anonymous or ordinary-household-user exploit. It becomes especially important when fixing F09.

**Evidence:** Captured generated connector SQL assigns the same token to all matching agents and has no owner predicate. No live cross-user access was attempted.

**Fix:** Explicitly define shared-household versus personal data access. For personal apps, bind connectors to the caller's app credentials and identity. For shared integrations, document the shared account and enforce an explicit permission policy. Test two users with disjoint files/documents.

### F11 — P2: Calendar caches grow without eviction and lose resource mappings between views

References: [cache insertion](../../ctl/nextcloud_calendar.py#L707), [resource lookup](../../ctl/nextcloud_calendar.py#L713), [cache key](../../ctl/nextcloud_calendar.py#L376).

Range cache entries expire for reuse but are never removed merely because they expired. The default Home range uses the current timestamp, creating new keys over time. Each range read also replaces the single `_EVENT_CACHE[owner]` mapping. A month view can fetch events, a Home fetch can replace its mappings with the next 30 days, and a later month cache hit returns the visible events without restoring their resource mapping. Editing those visible events can then fail as not found because the fallback fetch is also only the default range.

**Evidence:** Source tracing; cache mutation and fallback behavior are explicit.

**Fix:** Bound entries with TTL/LRU eviction, normalize preview range keys, and cache event resource metadata per range/calendar or by stable event identity. Ensure returning a cached range also supplies its matching metadata. Test alternating Home and month requests followed by editing an event beyond 30 days, plus long-running preview polling.

### F12 — P2: Async HTTP handlers perform blocking application/network work

References: [calendar create/update/delete](../../ctl/api/routes/calendar.py#L227), [calendar request](../../ctl/nextcloud_calendar.py#L563), [service action](../../ctl/api/routes/services.py#L53).

Calendar async handlers directly call synchronous HTTP requests with a 25-second timeout. Service action's async handler directly computes effective state using synchronous probes and runtime stores. While these run, the single Uvicorn event loop cannot serve unrelated requests promptly. Slow Nextcloud or Docker can freeze otherwise healthy dashboard interactions.

**Evidence:** Source tracing; no production load benchmark was run.

**Fix:** Use async HTTP/database boundaries or offload synchronous operations after body parsing via run_in_threadpool. Apply bounded deadlines and cancellation behavior. Test health endpoint responsiveness during a deliberately slow calendar/host request.

### F13 — P2: The browser caches a CSRF token longer than its bound session evidence

References: [CSRF cache](../../dashboard/src/api/client.ts#L62), [session token binding](../../ctl/api/security.py#L53).

The browser caches the first successful token promise for the lifetime of the loaded module. The backend derives that token from the forwarded Authentik JWT. If that JWT changes during session renewal or reauthentication while the SPA remains loaded, future mutations use the old token and receive 403. No session-change reset or bounded refresh path exists.

**Evidence:** Source tracing. Actual Authentik JWT renewal timing was not measured, so the production trigger frequency remains unverified.

**Fix:** Invalidate cached token material on sign-out/session change and refresh it on an explicitly identified CSRF mismatch. Preserve the original idempotency key on any safe retry. Test changing session token material while keeping the app mounted.

### F14 — P2: Dashboard refresh can overwrite a signed-out state and retain stale privileges

References: [dashboard refresh](../../dashboard/src/state/dashboard.tsx#L99), [signed-out listener](../../dashboard/src/state/dashboard.tsx#L119).

After the health probe succeeds, resource requests use Promise.allSettled. A redirected request can notify the signed-out listener, but the loader then unconditionally sets connection to online. Failed sources retain previous data, including identity/writes_enabled and service status. A logout during the refresh can leave the UI displaying online with stale operator state. Backend checks still reject writes; this is a misleading UI state, not a demonstrated authorization bypass.

**Evidence:** Source tracing of notification and promise completion order.

**Fix:** Give signed-out state precedence, clear session-specific state on authentication failures, and report failed resources explicitly. Add a test where health succeeds but identity/session resource requests redirect.

### F15 — P2: Existing jobs become inaccessible after 100 newer jobs

Reference: [job lookup](../../ctl/api/routes/jobs.py#L44).

Detail/event endpoints search only the latest 100 jobs instead of using the existing indexed `store.get(job_id)`. A retained job's ID therefore starts returning 404 once it falls out of that window, even though its events and database row exist. Latest/core-job searches elsewhere also use bounded history and can omit long-lived work during heavy activity.

**Evidence:** An isolated retained job ordered before 100 newer rows returned 404 while `store.get()` still found it.

**Fix:** Lookup details by primary key. Query active/latest jobs by service directly rather than filtering a global page. Add historical lookup and heavy-activity tests with explicit timestamps.

### F16 — P2: Development dependencies have unresolved security advisories

References: [dashboard dependencies](../../dashboard/package.json#L31), [lockfile](../../dashboard/package-lock.json).

`npm audit` reports affected Vite, Vitest, esbuild, and @vitest/mocker packages: one critical, one high, and two moderate. These are development tooling, not proof that the FastAPI-served production bundle is vulnerable. The critical Vitest issue requires its UI/API or browser-mode conditions; this repository's test command uses `vitest run` and does not enable that UI. Several Vite findings are platform/configuration dependent.

**Evidence:** Local dependency audit, with the critical issue's conditions checked against the [maintainer-published Vitest advisory](https://github.com/vitest-dev/vitest/security/advisories/GHSA-5xrq-8626-4rwp). The [esbuild advisory](https://github.com/evanw/esbuild/security/advisories/GHSA-67mh-4wv8-2f99) concerns its development server.

**Fix:** Upgrade to reviewed patched versions, regenerate the lockfile, rerun the existing checks, and add a dependency-audit gate with applicability-aware exceptions. Do not automatically apply `npm audit fix --force` without reviewing breaking changes.

### F17 — P2: “Verified” route/sign-in status exceeds the evidence collected

References: [service route projection](../../ctl/service_state.py#L394), [platform verification](../../ctl/core_setup.py#L283).

A configured Tailscale Serve port plus a healthy loopback app is enough for route verification. The code does not prove that this port targets the correct Caddy listener or that private HTTPS responds successfully. Core verification checks LobeChat OIDC environment fields, not a completed sign-in callback, yet its success message says sign-in and the private route passed live checks. A wrong Serve destination or broken OIDC callback can leave optimistic ready/verified status.

**Evidence:** Source tracing; the live verification function uses loopback HTTP probes, config-presence checks, and Serve-port membership.

**Fix:** Distinguish configured, probed, and user-verified status. Probe the actual canonical private HTTPS route and its expected auth behavior. Record successful user callback evidence separately; do not infer it from present environment variables. Add wrong-port-destination and broken-callback tests.

## Architecture, efficiency, and maintainability observations

- **The worker is a process boundary, not an enforced privilege boundary.** Both services run as the same user without sandboxing, and API log routes invoke Docker. That user also owns secret material and can invoke the Docker access helpers. The statement that the web process cannot execute host/Docker operations overstates the isolation. If that boundary is a security goal, use separate principals and a narrow worker protocol with restricted file access.
- **Schema setup and query amplification:** Store connections repeatedly run DDL and schema introspection. Service snapshots open stores repeatedly per app; the dashboard launches nine resource requests every ten seconds, with some projections repeated in other routes. Add migrations, indexed queries for active jobs/service history/events, and a shared bounded snapshot cache. Measure p95 request time and SQLite lock contention before optimizing further.
- **Subprocess output is not bounded in aggregate:** `docker_cmd_stream` truncates individual lines but retains all lines and uses an unbounded queue. Its timeout is checked while reading output, but a process that closes stdout and remains alive can reach an unbounded `proc.wait()`. Close streams and join readers in finally; apply the deadline through process exit; bound retained output and queue/backpressure.
- **Calendar work is not bounded by the requested output limit:** The backend reads/parses the complete REPORT, expands all occurrences, and only then truncates rows. Large or pathological calendars can consume disproportionate memory/CPU. Apply response-size, resource-count, and recurrence-expansion budgets.
- **Tool risk relies on names:** `_tool_risk` uses naming heuristics rather than a reviewed per-tool allowlist. Preserve conservative defaults and verify read classification against each connector contract before granting auto permission. No specific currently deployed misclassified write tool was established in this review.
- **React request lifecycles:** `useCalendarEvents` lacks request generation/abort protection, so a slower old month can overwrite a newer month. `useApi` has a newest-request guard but does not invalidate it when path becomes null or on unmount. `useAction` documents one-at-a-time execution but has no lock; overlapping calls can clear pending early. These merit focused delayed-response/double-invocation tests.
- **Large orchestration modules:** install.py has 2,763 lines, service_ops.py 1,035, actions.py 941, nextcloud_calendar.py 776, and install_batches.py 765. The existing lifecycle extraction is useful; extend it by separating phase transitions, external adapters, and public response projection rather than creating generic helpers without clear ownership.
- **Logging:** Persisted job messages are centrally redacted and MCP history avoids storing payloads. Worker containment/reconciliation prints raw exception text in places, without severity or traceback context. Route all exception logging through a structured, redacted logger; retain stable user-facing codes and operator diagnostics. Broad catch-all at a final worker boundary is reasonable, but silently completing failed cleanup is not (F04).

## Requirements and documentation mismatches

- README advertises arm64 support, but `core_setup.capacity()` rejects anything outside x86_64/amd64. Either implement/test arm64 throughout the core slice or correct the advertised platform support.
- PLAN.md describes a temporary HTML bootstrap dashboard, UI-created identity administrators, and tool confirmation as a future policy layer. README and current terminal installer describe newer behavior. Choose a canonical current product contract and move historical planning material out of active requirements.
- The calendar module still describes itself as read-only despite exposing writes; Vite comments describe a single status screen despite the multi-page split app. Refresh misleading comments alongside affected changes.
- `make verify` does not run every check in CI despite its documentation: shell syntax, YAML/Compose validation, secret scanning, and SBOM generation are separate CI steps.
- Backups/restore and updates are explicitly not shipped. Their unavailable state is appropriately documented; absence is not itself counted as an implementation defect. Backup copy coverage is a defect (F05).

## Testing and verification gaps

Passing tests establish consistency under their fixtures, not deployed acceptance. The highest-value additions are:

1. Concurrent credential updates across API and worker processes, first-use key races, simultaneous cleanup, and fault injection at file replacement.
2. Full account-install and identity retry flows, ensuring a job cannot be claimed before its required encrypted input is durable. Current service/core/batch paths publish queued rows before separately saving identities.
3. Cancellation and lease loss between every major side effect, stale-worker fencing, and worker restart during partially completed uninstall.
4. Calendar field-preservation, month/Home cache alternation, recurring/event edge cases, old/new request races, large REPORT bounds, and ETag conflicts.
5. Provider removal with gateway unavailability and verified revocation; failure must remain visible and retryable.
6. Real browser/VM acceptance for first installation, multi-user Authentik access, private routes, native OIDC callbacks, credential vault seeding, MCP approval enforcement, reboot recovery, and reinstall/uninstall isolation.
7. Restoration of identity and app data from a complete supported backup procedure. No such restore was verified in this review.

A full end-to-end browser suite was not present in the reviewed CI flow. Tests rely heavily on mocks for Docker, network, app-native account models, and SQL; Compose validation proves interpolation and schema shape, not upstream API compatibility or execution success. Direct LobeChat schema writes and application model scripts should be version-contract-tested against the actual pinned containers.

No full container vulnerability scan, Python dependency vulnerability audit, authenticated penetration test, load benchmark, live SSO round-trip, or legal/compliance certification was performed. Those remain separate verification activities, not silently passing checks.

## Suggested remediation order

1. Fix F01–F05 with regression tests and preserve all existing user data during migrations.
2. Fix identity publication/retry, native user access, and per-user MCP boundaries together (F06, F09, F10).
3. Unify image pinning, close SQLite connections deterministically, and make status reflect measured evidence (F07, F08, F17).
4. Resolve calendar cache/async behavior and browser session state (F11–F14); repair historical job queries (F15).
5. Patch development dependencies (F16), reconcile documentation, and establish VM/browser acceptance coverage before treating the documented user stories as verified.

## Isolated reproduction output

The reproductions used temporary runtime directories, dummy identities/credentials, and mocked Docker/gateway actions. SQLite descriptor counts were measured with cyclic garbage collection temporarily disabled, then restored.

```text
SQLite: 100 reads, open fd increase: 201
Concurrent workflow saves: requested 2, retained: 1
Cancel effects: [compose side effect, MCP side effect]
  job: cancelled; installation: running
  final error: invalid job transition: cancelled -> succeeded
Historical existing job detail: 404
Retry identity old/new: True False
MCP credential selection scoped by owner: False
Same token for all matching agents: True
Calendar replacement preserves notes/location/alarms/attendees:
  [False, False, False, False]
Provider removal with failed reconciliation: succeeded
Gateway key cleanup failure returned: None; logged: 1
```
