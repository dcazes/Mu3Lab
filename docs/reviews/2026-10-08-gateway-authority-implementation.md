# Gateway authority and human approval implementation

Date: 2026-10-08. Owner: Codex. Branch: `fix/review-priority-a`.
Baseline checkpoint: `68ced9a`. This record covers R05, R03 and R02 from the
[project review](2026-10-07-project-review.md). The user selected **operator-only**
access for R03. It supersedes the earlier blanket write containment described in
[the security implementation record](2026-10-08-priority-security-implementation.md).

## Tracking and decisions

| Finding | Implementation | Release acceptance |
| --- | --- | --- |
| R05 | Implemented: durable revocation revision, committed policy hash, acknowledgement and fail-closed publication | Disposable built container checked; installer/VM and production upgrade not run |
| R03 | Implemented for operator-only shared connector credentials; person/provider/app/version binding and isolated gateway sessions | Real two-account Authentik/LobeHub role-change and upgrade checks outstanding |
| R02 | Implemented: immutable encrypted requests, authenticated human decisions and one durable dispatch claim across chat and console | Pinned real LobeHub approval-link flow and real-app mutation checks outstanding |
| R04 | Additional evidence: real HTTP commit-then-disconnect yields one dispatch and durable unknown outcome | Real app-specific read-back and VM crash acceptance outstanding |

Implementation completion does not close these release acceptance gates or the
other findings. Personal delegated credentials and household-member access are
outside the selected R03 scope. Every current reviewed connector explicitly
uses `data_scope: operator_only`, `delegation: none`, audience `operators`.
Operators share the upstream service account's access to application data. Two
operators do **not** gain private upstream data isolation from different gateway
bearers. Household members retain their chat history and ordinary chat, but no
managed application connector authority.

## Architecture and ownership

`gateway_authority.py` is the shared authorization implementation used by the
control plane and gateway image. The gateway mounts only its dedicated
`authority/` directory read/write, policy read-only and metadata logs read/write.
It does not receive the control-plane database, Authentik credentials or host
execution access. The container retains the installing UID/GID, read-only root,
no capabilities and no-new-privileges settings.

The authority directory is mode 0700. Its SQLite database and encryption key are
mode 0600. Initialization is locked across processes, creates keys atomically,
and records SQLite schema version 1. An existing database without its matching
key is refused; keys are never silently regenerated for existing state. SQLite
transactions serialize policy revisions, credential rotation, approval decisions
and dispatch claims. Storage errors fail closed with sanitized diagnostics.

The store records policy revision/hash/acknowledgement, verified operator
subjects with expiry, scoped credential versions and encrypted bearer values,
operations with encrypted arguments and binding fingerprints, and human decision
metadata. JSON canonicalization rejects non-finite values. Argument fingerprints
use HMAC; schema fingerprints use SHA-256. The maximum argument payload is 64 KB.
Operation history and gateway logs do not copy arguments or tool results.

## R05: durable permission changes

The gateway reads and validates the policy on each request. File timestamps and
cached grants never establish permission. Policy version 2 requires the explicit
scope and approval contract. A file authorizes only when its revision and content
hash match the committed authority row. The gateway acknowledges that revision
in the store; dashboard readiness checks both the health response and persisted
acknowledgement.

Permission changes follow this sequence under the interprocess policy lock:

1. Remove the previous policy file and fsync its directory.
2. Increment the durable authority revision, clear its committed hash and
   acknowledgement, revoke pending/approved operations and erase their arguments.
3. Change saved category/tool switches or connector state. Connector stop/disable
   holds the publication lock through the state change and rebind, preventing a
   concurrent publisher from recreating an old enabled snapshot during the stop.
4. Build and validate the replacement snapshot with the new revision.
5. Write a private temporary file, flush/fsync and atomically replace policy.
6. Commit the exact payload hash. Until this commit, the gateway rejects the file.
7. Verify gateway revision/hash acknowledgement and synchronize assistants.

Failure before durable invalidation prevents the requested mutation. Failure
later leaves the old grant unavailable. Rebuilding an identical already committed
policy is a no-op, so the five-minute assistant sync does not revoke otherwise
valid pending requests. Snapshot-build failure invalidates the previous policy.
Restoring an older policy file alone cannot restore its authority.

Revocation prevents claims that have not committed. It cannot cancel a connector
request that already crossed the dispatch boundary. That in-flight operation
must reach a recorded terminal outcome or become unknown.

## R03: person-bound operator access

`people.operator_subjects()` reads current active Authentik operators. Provisioning
and each human approval/execution verify that membership, rather than trusting a
caller-supplied role header. Scoped credentials bind immutable Authentik subject,
provider (`lobehub`), application and monotonically increasing credential version.
Wrong-app tokens, retired app-wide tokens, disabled people and stale versions are
refused. Gateway connector sessions are also keyed by person/provider/version,
preventing session reuse across people or rotated credentials.

Verified operator membership expires after 600 seconds. The existing worker
refreshes connected chat operators every 300 seconds. Failed membership lookup
revokes connected subjects; per-person provisioning failure revokes that person's
credential. A stopped worker eventually causes access denial instead of retaining
operator authority indefinitely. Role demotion/deactivation through Mu3Lab
revokes gateway authority before updating Authentik; external Authentik changes
have the bounded freshness window. Human decisions always require a fresh lookup.

Assistant synchronization creates per-person credentials for operators. Members
receive an empty managed tool set; retired managed assistants lose connector
access without deleting conversation history. A separate `control-discovery`
credential supports initialization, ping and tool listing only. It cannot request
or execute changes or call read tools.

## R02: exact human approval

All enabled reviewed writes, including direct core tools and `change_with_tool`,
validate arguments against the connector schema and create a pending operation.
They return a dashboard review link and operation identifier, with zero write
requests sent upstream. `use_tool` continues to reject write tools. A missing
verified input schema or dashboard approval origin keeps a write unavailable.

An operation binds subject/provider/credential version, application, connector
server and deployment/review revision, tool, canonical input HMAC, schema hash,
policy revision, creation time, 300-second expiry and state. Equivalent requests
reuse only an unexpired pending/approved operation with every binding unchanged.
There are at most 20 unexpired pending/approved operations per person.

The dashboard displays the exact stored arguments as escaped JSON alongside app,
connector, tool, state and shared operator-only scope. It never accepts a model's
summary as consent. Approve/reject sends only the operation ID and decision;
execution takes no replacement arguments. Same-origin operator authentication
and the existing CSRF-bound mutation dependency protect decisions.

The dashboard console uses the same authority record. Its confirmation token is
the operation ID. Submitted arguments must match the retained HMAC, including
terminal retries; a changed payload cannot reuse approval. Console reads also use person-bound gateway credentials and the same policy checks.
Unreviewed connector
execution is refused rather than inferring safe access from unknown tools.

### State transitions

| Current state / event | New state / behavior |
| --- | --- |
| Valid reviewed write request | `pending`; encrypted inputs retained; no dispatch |
| Owner approves before expiry | `approved`; human decision metadata recorded |
| Owner rejects | `rejected`; encrypted inputs removed; no dispatch |
| Policy/credential/subject revoked | Pending/approved become `revoked`; inputs removed |
| Pending/approved expiry | `expired`; inputs removed; no dispatch |
| Valid approval claimed atomically | `dispatching`; one dispatch attempt allowed |
| Successful upstream response | `succeeded`; inputs removed; transient bounded result returned |
| Explicit upstream refusal/tool error | `failed`; inputs removed; no automatic retry |
| Transport loss/malformed reply after write submission | `outcome_unknown`; inputs removed; no automatic retry |
| Gateway restart with `dispatching` record | `outcome_unknown`; never redispatched |
| Dispatch older than 180 seconds observed by status view | `outcome_unknown`; never redispatched |
| Repeated execution of terminal/already dispatching operation | Metadata only; zero additional writes |

Immediately before the upstream `tools/call`, after session initialization and
while holding the connector lock, the gateway reloads policy and atomically
claims the approved operation. It rechecks subject freshness, credential version,
expiry, permission switches, policy revision, connector revision and schema hash.
Concurrent execution can produce at most one successful claim. A crash between
claim and submission is deliberately unknown rather than automatically retried.
This provides one attempted dispatch per operation, not a promise of exactly-once
external side effects across arbitrary upstream implementations.

For an unknown outcome, inspect the application using safe reads before requesting
another change. A new operation requires new consent and can still duplicate a
previous effect if the operator has not reconciled it. No automatic reconciliation
or upstream idempotency contract is claimed.

### API and navigation

- `GET /api/v1/tool-approvals`: the caller's last 50 operation metadata records.
- `GET /api/v1/tool-approvals/{id}`: owned operation, including arguments only while retained.
- `POST /api/v1/tool-approvals/{id}/decision`: `approve` or `reject`; approval dispatches the stored request.
- `POST /api/v1/tool-approvals/{id}/execute`: execute an already approved, never claimed operation, or return existing state.
- `/tool-approvals` and `/tool-approvals/{id}`: operator request list and exact review.
- Gateway execution endpoint uses a current scoped bearer and the same durable operation.

Results are transient. Terminal records preserve binding fingerprints and outcome,
not plaintext arguments or response bodies. Credential values and inputs remain
ciphertext in SQLite files; deleting the logical values does not claim secure
physical erasure of historical encrypted WAL/backup pages.

## Build, migration and recovery

The gateway Docker build now uses the repository root to copy the canonical shared
module. `.dockerignore` admits only gateway build inputs. Cryptography/JSON Schema
and dependencies are pinned with wheel hashes. Materialized runtime projects copy
the same module and use their local Dockerfile. Release build inventory now records
both build context and Dockerfile path; CI supplies the explicit Dockerfile.

This turn changes the checkout and test artifacts only. No production installation,
container restart, deployment, push or release image publication was performed.

Before deploying:

1. Run the verification commands below and the remaining client/VM checks.
2. Take a coherent checkpoint of the control-plane state, gateway policy and
   dedicated authority directory. For a live SQLite store use the SQLite backup
   API (or quiesce writers and copy all database/WAL state); pair the database with
   its exact encryption key. Do not copy only a live `authority.db` file.
3. Rebuild/approve the gateway release artifact, materialize the runtime project,
   create private authority storage and publish the version-2 policy.
4. Reconcile connected people and replace app-wide chat credentials with scoped
   operator credentials. Old app-wide bearers are not accepted by the new gateway.
5. Require matching policy revision/hash acknowledgement before declaring readiness.
6. Check that members retain chat history but lose managed connector authority,
   and that operators see shared scope and working approval links.

Gateway images from the baseline checkpoint reject version-2 policies. New images reject version 1.
For releases predating the missing-policy fix, stop the old gateway before
changing policy or credentials; those versions may retain cached grants. During
upgrade, removal of the old policy denies access before new publication; mixed
versions can lose availability but must not silently keep old write access.
Never downgrade by restoring only an old grant file. A full authority checkpoint
rollback can rewind revisions and receipts: reconciliation and credential rotation
are required before restoring access. General coordinated backup/restore and
rollback ceremonies remain R14 work. If the key/database pair is missing or
inconsistent, preserve evidence, restore the matching checkpoint and inspect
in-flight operations; do not delete the store to make health green.

## Verification and remaining acceptance

Automated tests cover missing/malformed/stale policy, failed publication, persisted
revocation across restart, token rotation, wrong subject/app, expired membership,
encrypted inputs, rejection/expiry, changed schema/connector/input, concurrent
claims and restart recovery. Real HTTP gateway/upstream/API tests verify direct
and helper preparation, CSRF and member denial, console/chat record equivalence,
permission revocation before dispatch, concurrent execution and commit followed by
a lost response. The latter records one write and an unknown outcome without resend.
Dashboard tests verify exact escaped arguments, decision-only payloads, rejection,
member denial and absence of an unknown-outcome replay button.

The opt-in built-container test runs with the production UID/read-only/capability
and mount boundaries, verifies revision acknowledgement, accepts a person-bound
ping, rejects a retired app-wide bearer and denies access after policy removal.
It creates/removes only a uniquely named test container with temporary storage.

Verified locally: `make verify` completed with 781 Python tests (7 opt-in skips),
102 dashboard tests, lint/format/type/schema checks and a production dashboard build.
All 36 rendered Compose projects validated. The built-container test passed in a
separate explicit run. The 23 authority/real-HTTP tests also passed separately.
The suite still reports the pinned Starlette/httpx test-client deprecation and
existing route-probe fixture socket cleanup warnings; neither is an approval test
failure. The existing dashboard chunk-size warning remains separate performance work.

Reproduce:

```sh
make format
make verify
.venv/bin/python -m tools.validate_compose
docker build -f platform/tool-gateway/Dockerfile -t mu3lab-test-gateway-authority:local .
MU3LAB_TEST_GATEWAY_IMAGE=mu3lab-test-gateway-authority:local \
  .venv/bin/python -m unittest tests.test_gateway_container -v
```

Outstanding release checklist:

- [ ] Run the pinned real LobeHub client with two real operator accounts and a member;
      check direct/helper reads and writes, approval link rendering, human SSO/CSRF,
      one real mutation and deliberate decline.
- [ ] Demote/deactivate a person during an open chat/session; verify immediate Mu3Lab
      revocation, external Authentik freshness expiry and preserved chat history.
- [ ] Exercise clean-install and upgrade paths in a throwaway VM; stop the gateway
      after dispatch claim and simulate lost replies against a real app.
- [ ] Verify coherent authority/key/control-plane backup and restore with explicit
      unknown-outcome reconciliation; never assume a restored receipt was not sent.
- [ ] Validate release image publication and supported architecture builds.

R06 still needs bounded HTTP concurrency, read deadlines, nesting/output validation,
rate limits and robust SSE frame selection. R09/R10 provider/control-plane job
boundaries and R14 general recovery remain separate findings. This implementation
adds relevant schema validation and credential revocation coverage without marking
those broader findings complete.
