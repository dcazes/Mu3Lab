# R08 and R20 implementation — 2026-10-08

> **Superseded status (2026-10-08):** this is a historical record of one tranche. Statuses it gives for R03, R05 and R20 were later advanced; the [review tracker](2026-10-07-project-review.md#4-prioritized-change-tracker) is authoritative, and the [gateway authority](2026-10-08-gateway-authority-implementation.md) and [status/gateway/revocation](2026-10-08-status-gateway-revocation-implementation.md) records describe the later work.

This continues the [project review](2026-10-07-project-review.md) after the [first security tranche](2026-10-08-priority-security-implementation.md). It records the implementation contract, migrations, regression coverage, and outstanding acceptance work. The review table remains the authoritative status tracker.

## Checkpoint and branch

Owner: Codex. Started: 2026-10-08. Branch: `fix/review-priority-a`. Baseline checkpoint: `14947c5`. Both registered worktrees were clean before this tranche. `/home/dak/Desktop/Mu3Lab-expansion` remains on `expansion/apps-2026` at `5b090ef`; that branch is already included in main and was not changed. Main and origin/main were at `ca29915` when this remediation branch was selected.

Implementation commit: the commit introducing this record, titled `fix: bind mutation identity and require verified service routes`. No push or deployment is included. No production installer, application restart, or live data migration was performed.

## Scope and status

| Finding | Implemented scope | Tracker status | Remaining work |
|---|---|---|---|
| R08 | Subject/namespace/request-bound replay; explicit active-operation conflicts; atomic encrypted inputs, jobs and desired state; batch command identity | implemented | Disposable-runtime and actual browser double-submit acceptance before release verification |
| R20 | Successful required route probes determine usable state and launch links; cached configured routes cannot claim readiness | in progress | Sign-in evidence expiry, observer freshness, bounded debouncing, and typed observations across API/worker/UI |
| R05 | No additional policy authority changes in this tranche | in progress | Durable revision/revocation and publication acknowledgment |
| R03 | No personal/shared scope behavior changed | open | Decide shared-credential fallback, then implement immutable caller credentials and data scope |

A4 is implemented. A5's immediate route-readiness task is implemented; this does not close all R20 acceptance criteria or certify Milestone A for release. Earlier write containment remains in place.

## R08: accepted command identity

The old global key lookup could return another person's operation or conflate a start with a stop. An active job could also be returned while the API wrote desired state for a different action. The new acceptance contract is:

1. Authenticate and authorize the request through the existing API dependencies.
2. Require the verified immutable subject for API-created jobs. Usernames remain audit/display attributes. Internal callers without an external subject retain an explicit `actor:` fallback; this is not gateway personal authorization.
3. Bind the command to its kind, resource, action, and complete typed request payload. Serialize deterministically using sorted JSON keys and fixed separators; reject non-finite values.
4. Hash that canonical command with HMAC-SHA256 using a private random deployment key. Only the fingerprint is stored in mutation metadata; raw provider credentials are not stored there, in audit details, or in job parameters. Keyed hashing avoids publishing an ordinary offline guessing oracle for sensitive values.
5. Scope an idempotency key by immutable subject and API namespace. Matching identity returns the original operation, including after completion. The same scoped key with a changed command returns HTTP 409 `idempotency_conflict`.
6. If a resource already has a queued, running, or confirmation-waiting job, return it only when subject, namespace and fingerprint match. Otherwise return HTTP 409 `resource_busy`, with `blocking_job_id` and an explicit wait/cancel recommendation. A different person does not inherit the active job just by supplying the same key.
7. Remember equivalent active requests submitted under additional keys. Those aliases continue to replay the original operation after it completes.
8. Recheck identity and the resource lock under `BEGIN IMMEDIATE` at insertion. An earlier lookup is an optimization and allows replay before state-dependent validation; it is not the concurrency enforcement point.

API request keys must contain non-whitespace text and be at most 128 characters. Empty headers are treated as absent. Without a key, equivalent active work may be deduplicated, but a completed operation is not a durable HTTP replay promise.

### Namespaces and callers

| Caller | Namespace | Additional identity inputs |
|---|---|---|
| Service actions | `services.actions` | Complete typed action body, including deletion confirmation |
| Provider credential save | `providers.save` | Provider selector, label and trimmed credential |
| Provider actions | `providers.actions` | Canonical provider resource and action |
| MCP lifecycle actions | `mcp.actions` | Connector resource and action |
| Self-update | `system.update` | Reviewed update action/resource |
| Core installation | `core.install` | Core-suite installation command |
| Core verification | `core.verify` | Core-suite verification command |
| Explicit job retry | `jobs.retry` | Original job ID, inherited kind/resource/action |
| Batch child installation | `batch.install` | Batch ID and item ordinal, owner subject |
| Batch child reset | `batch.reset` | Batch ID and item ordinal, owner subject |
| Batch creation/append | Dedicated `batch_requests` table | Owner subject, ordered service selection and clamped download parallelism |

All former global lookup callers were converted together. Provider aliases resolve before comparing their resource. A completed removal, including an unsupported legacy provider, can replay before checking whether the connection still exists. Core and service replay similarly precede checks whose state may have changed because the original operation succeeded.

Batch append remains an intentional operation: the owner's new selection may append to their active queue. Its key is recorded even when it appends rather than creates. Retrying that selection does not append it again. Different owners may use the same key without sharing a batch. Changing selected apps or effective parallelism under the same scoped key conflicts.

### Atomicity and concurrency

`JobStore.create` accepts two internal callbacks: `prepare(job_id)` stores encrypted inputs; `commit_state(job_id)` writes associated desired state after inserting the job and mutation mapping. Both run only for a new accepted operation, within its SQLite transaction. A duplicate or rejected request does not execute either callback.

The existing connection manager reuses a connection for nested calls to the same database in one thread. Provider credential records, workflow identities, jobs, audit records, mutation mappings and control state therefore share the transaction in the real runtime. Batch child enqueue/reset/resume uses this same contract for item links and batch state. The worker cannot claim a job before its required private inputs and state are committed.

Provider and workflow store read/modify/write functions now use `db.transactional`. They previously used file locks around a database rewrite. Invoking those functions while holding a job SQL transaction could invert lock order against another process holding the file lock while waiting for SQLite. Database serialization preserves concurrent updates without that inversion. Their encryption key initialization retains its existing separate private key lock.

Explicit retry wraps original lookup, replay, cancellation of a confirmation-waiting original, new job creation and state callbacks in one transaction. If preparation fails, cancellation is rolled back and the original remains available. This is enqueue atomicity; it does not provide an execution phase journal, lease fencing, or exactly-once external side effects. R09/R10 remain open.

## Migration and operational requirements

Migration `0003_mutation_identity.sql` adds `actor_subject`, `request_namespace` and `request_hash` to jobs, replaces the globally unique job-key index with `mutation_requests`, and replaces the globally unique batch-key index with `batch_requests`. Mapping uniqueness is scoped; both tables reference their original operation. Migration uses the existing serialized migration infrastructure and preserves old jobs, batches and audit history.

Legacy rows deliberately retain an empty fingerprint. Their original key cannot prove the immutable caller or complete request body, so replay under that key returns a conflict rather than guessing identity. A legacy active job is a blocking operation; wait for it to finish or explicitly cancel/retry it through the authorized existing workflow. A new key must never be used to blindly repeat a destructive operation whose outcome is unknown.

The HMAC key is stored beside the database as `<database-stem>.request-key`, with owner-only permissions and an interprocess initialization lock. The lock closes a first-use race in which one process could see another process's newly created but unfinished key. An invalid key is rejected. If fingerprinted records already exist and the key is missing, acceptance fails instead of silently generating a replacement and changing request identity.

For upgrade/restore:

1. Stop the API and worker when taking a deployment checkpoint; preserve a consistent database and its private keys. If taking an online SQLite copy, use the SQLite backup API rather than copying a live main file without its WAL.
2. Preserve `mu3lab.db`, its `mu3lab.request-key`, and the existing `secrets.key` together under appropriate private storage. Application-only backups are not proof that control-plane recovery is complete; R14 still tracks that gap.
3. Start the updated code against the checkpoint in a disposable runtime first. Check schema version 3, unchanged historical jobs, legacy replay rejection, and normal new-request replay.
4. Do not run older code against the upgraded database. A code downgrade needs the matching pre-migration checkpoint, reconciled against any effects executed since it was taken; restoring a stale database can otherwise repeat operations. Full safe update rollback remains R12/R17 work.
5. When the request key is missing or damaged, restore its original value with the matching database. Never delete mutation history or regenerate the key as a shortcut around a conflict.

The generated OpenAPI and TypeScript schema include the optional blocking job identifier. Dashboard API errors continue to display the server's explicit conflict explanation; no new client bypass or automatic retry was introduced.

## R20: required route readiness

`ctl.service_state.status` now regards only a successful probe (`route_state=verified`) as required-route readiness. Publication/configuration alone is insufficient. A healthy process whose required private HTTPS route does not answer retains healthy process information but becomes `needs_setup`; the public projection displays `needs_attention`, explains the route failure, and offers no launch URL.

Missing DNS similarly cannot establish readiness. An internal service such as Ollama with no required browser route can still become ready. Once the actual probe succeeds, the route and UI become ready again. Stopped/uninstalled apps do not acquire a launch link just because the registry has a port.

`ctl.api.service_view` also guards older cached observations. A cached `configured` route cannot preserve an old true readiness flag, green running claim, or launch link before the worker produces a corrected snapshot. API reads remain observation-only and do not run Docker, subprocesses or route probes. The display projection retains a specific route explanation instead of replacing it with generic sign-in advice.

### Remaining R20 implementation sequence

1. Introduce a typed observation carrying process health, route configuration, route verification and sign-in verification separately, with source and per-check timestamps. Adapt existing worker observations at one boundary rather than spreading new string rules through handlers.
2. Define each required check from the manifest/account contract. Internal services have no browser-route prerequisite; manually authenticated apps must not be described as having verified sign-in without corresponding evidence.
3. Compute usable state from all required checks and their explicit freshness deadlines. Keep installed/process-running facts independently visible. Expired observations suppress usability/launch claims, even if the previous cycle was successful.
4. If transient failure debouncing is required, retain the actual last success and failure timestamps, bound the grace period, and expose checking/stale status. Never advance a success timestamp because another service completed its probe.
5. Add failed/missing Serve, expired account evidence, stale-observer and successful-recovery fixtures against worker, API projection and dashboard. Coordinate per-check timestamps and cycle budgets with R21.
6. Exercise the pinned route and actual sign-in in a disposable installation, then verify browser/phone behavior before marking R20 verified.

## Validation

New mutation and required-route tests reproduced the old defects before the changes. Additional transaction and concurrency regressions were added as implementation review exposed races and rollback requirements.

- Mutation regressions cover changed resource/body, renamed username, separate subjects, equivalent alias keys after completion, confidential fingerprint inputs, desired-state rollback, encrypted-input rollback, concurrent first-use requests, missing-key recovery, waiting-job retry rollback, actual API 409 responses, completed service replay, legacy schema migration, batch scope and changed parallelism, and batch enqueue rollback.
- Route regressions cover a healthy app with a failed probe, missing DNS, successful recovery, internal services and a cached legacy configured observation. Existing registry tests explicitly mock successful probing when their subject is Compose path/materialization.
- Existing provider/workflow store concurrency tests exercise simultaneous threads and processes after the move to database serialization.
- Final `make format` and `make verify` results are recorded after completion below. Optional real Caddy tests and VM/client acceptance are distinct gates; this tranche does not claim to run them.

## Next security decisions

R05 still requires an independent revocation/publication contract. Before editing it, define durable policy revision allocation, the gateway's required revision and accepted content hash, and how revocation becomes effective when policy publication fails. Merely incrementing a JSON field cannot deny reads still authorized by an old valid file. Stage revocation intent before accepting a switch mutation; if that intent cannot be persisted, reject the mutation without claiming revocation. Require acknowledgment/readiness for the intended revision, test crash points and gateway restart/rollback, and define token rotation. Preserve the unconditional chat-write deny until full R02 is accepted.

R03 requires a product choice for connectors that only support shared service credentials. The pending question asks whether they should be operator-only, explicit household opt-in, or disabled until personal delegation exists. That answer determines the scope contract; it must not be inferred from a timeout. R03 does not become implemented because control-plane jobs now have subject-bound request identity.

### Final local results

| Check | Result |
|---|---|
| `make format` | Passed |
| Architecture guard, Ruff lint and formatting | Passed; 281 Python files checked for formatting |
| OpenAPI consistency and Mypy | Passed; 141 source files type-checked |
| Full Python suite | 757 run: 751 passed, 6 explicit optional skips |
| Dashboard lint, TypeScript, generated schema consistency | Passed |
| Dashboard tests | 98 passed across 15 files |
| Production dashboard build | Passed |
| `git diff --check` | Passed |
| Optional pinned-Caddy tests, Compose rerender, VM/client release gates | Not rerun in this tranche; no Compose or ingress configuration changed |

The build's existing bundle-size warning and FastAPI/Starlette dependency deprecation remain under R24/R29. Optional skips are not passes. No finding gained release-verified status from these local checks.
