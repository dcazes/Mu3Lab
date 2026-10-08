# Priority security implementation — 2026-10-08

> **Superseded status (2026-10-08):** this is a historical record of one tranche. Statuses it gives for R02, R03, R05 and R20 were later advanced; the [review tracker](2026-10-07-project-review.md#4-prioritized-change-tracker) is authoritative, and the [gateway authority](2026-10-08-gateway-authority-implementation.md) and [status/gateway/revocation](2026-10-08-status-gateway-revocation-implementation.md) records describe the later work.

This records the first implementation tranche of the [project review](2026-10-07-project-review.md), specifically A1, A2 and the immediate protections in A3. It does not claim completion of all 35 findings or the rest of Milestone A.

## Branch and checkpoint

- Fetched `origin` before selecting the base. Local `main` and `origin/main` both pointed to `ca299152c3a08a1ee0c7d4695e9e8938f4a880e8`.
- Inspected both registered worktrees: `/home/dak/Desktop/Mu3Lab` and `/home/dak/Desktop/Mu3Lab-expansion`.
- The expansion worktree was clean. `expansion/apps-2026` at `5b090ef` was already an ancestor of `main`; its outstanding difference from main was the subsequent workflow updates. It was not an appropriate base for new work.
- The main worktree contained only the review documents and their README link. Created `fix/review-priority-a` from main and committed all that work as **`d403d09`**, `docs: checkpoint project review and remediation plan`.
- Both worktrees were clean after the checkpoint, before application changes. No stashes existed. Existing expansion work was not altered.
- Implementation stays on `fix/review-priority-a`. No push, deployment, owner-host installer run, or production-container restart was performed.

Owner: Codex. Started: 2026-10-08. Implementation commits: **`5ae7f72`** (R01 ingress/installer) and **`a25b010`** (R02 containment, R04 transport, R05 policy/publication, chat UI and documentation).

## Implemented behavior

| Finding | Trigger | Previous behavior | Result in this tranche |
|---|---|---|---|
| R01 | Request reaches dashboard before identity setup | Starter adds proxy trust token and forwards caller identity | Starter responds 503 without forwarding to the API; health remains independently available |
| R01 | Signed-in caller supplies extra identity headers | Missing response identity fields could retain caller input | Ordered dashboard route removes all `X-Authentik-*` and the proxy token before forward-auth; only returned identity is copied |
| R01 | Reinstall sees healthy old ingress | Health alone could skip applying changed ingress security | Content stamp requires a restart for the changed boundary; stale runtime dashboard configuration becomes the denying starter until gate verification |
| R01 | Fresh installer publishes dashboard | Route was published before protection | Loopback gate verification runs first; dashboard Serve publication follows it |
| R02 | Enabled direct or wrapped write requested | Connector executes without gateway approval proof | Every reviewed write is rejected before obtaining/dispatching its connector, even with an enabled saved switch |
| R02/R33 | Assistant or operator inspects tools | Write tool and UI descriptions promised approval | Chat discovery and instructions report writes unavailable; write switches in the chat category UI are disabled and show unavailable |
| R04 | Reply lost after a call with unknown/nonrepeatable semantics | The transport could send the call twice | Default transport semantics allow one dispatch and report an unknown outcome, with no retry |
| R04 | Safe read loses its session | Retry could skip initialization | Explicitly safe reads/discovery retry at most once, initialize a fresh session, and stop after the bounded attempt count |
| R05 | Loaded policy is deleted, unreadable or corrupt | Missing file retained cached grants | Every policy read reopens the file; failure clears cached authority, forgets connector sessions, and produces unavailable HTTP/readiness responses |
| R05 | Policy content changes with identical modification time | Change could be ignored | Content SHA-256, rather than timestamp, controls cache/session invalidation |
| R05 | Two publishers update policy | Predictable temporary file and pre-lock snapshots could race | Build and publication are serialized under a file lock; unique 0600 temporary files are flushed and atomically replaced |

### R01 implementation details

`apps/ingress/Caddyfile` retains the dedicated 204 health endpoint and identity/vault entry points. Its dashboard handler has no dashboard reverse proxy and never injects the ingress token. Requests to dashboard assets, identity/session endpoints and mutations receive setup-in-progress HTTP 503 until the authenticated variant is applied.

`apps/ingress/Caddyfile.authenticated` removes the incoming `X-Authentik-*` family and `X-Mu3Lab-Proxy-Token` inside the explicitly ordered `route`, before either outpost forwarding or forward-auth. Successful forward-auth copies its own identity response; the final dashboard proxy injects the configured shared token. Caller cookies remain available for legitimate Authentik session authentication. The dedicated Caddy health endpoint does not expose dashboard data.

`ctl/install.py` now orders `authentik_setup → dashboard_protection → serve`, and `PHASES` matches that order. The existing protection probe already calls loopback with the tailnet Host header, so no publicly shared dashboard address is required for that probe. Existing authenticated API traversal remains the later signed-in confirmation; it is not replaced by the local anonymous probe.

The installer hashes both ingress variants and records `.ingress-boundary.sha256` only after Caddy has restarted and its health endpoint answers. An old installation without the stamp must apply the boundary even if its Caddy health check succeeds. `_safe_caddy_configuration` preserves the current authenticated base when it matches; otherwise it rebases the current denying starter while retaining managed optional app routes. `recreate=True` ensures a unchanged mount path does not prevent reading new contents. Authentik setup/protection errors cannot make this new starter forward dashboard requests.

This is an implementation safeguard, not proof of every power-loss point during an existing-host upgrade. Until a running installation is updated, its old process still runs its old configuration. A full disposable-VM interrupted-install/upgrade exercise and real Authentik/Tailscale sign-in remain acceptance gates.

### R02 implementation details

The enforcement point is `call_tool`, shared by direct everyday calls and `change_with_tool`. A reviewed write never reaches `CONNECTORS.get` or `tools/call` once enabled-switch checks have passed. `use_tool` also refuses writes. The restriction has no client-supplied bypass flag, approval string or trusted-model exception.

`_enabled` reflects effective chat availability: only enabled reviewed reads in enabled categories qualify. Consequently direct write tools and `change_with_tool` are absent from the listed surface; `find_tools` keeps write names visible but reports them off, supplies no runnable path, and explains the approval limitation. A client that cached an earlier write tool list still receives rejection when it calls one.

The control plane also publishes every write tool as disabled in gateway policy, independently of saved permission. This protects callers of an older gateway image during replacement: its existing switch check denies those writes too. Development refreshes explicitly rebuild changed `Dockerfile`/`gateway.py` content under a build lock before starting the service, cache only successful builds by content digest, and stop on build failure. Tagged releases continue to use their verified immutable image without a local build.

Saved permissions are retained. The dashboard category UI displays all writes unchecked and disabled, excludes them from available-tool counts, and labels them unavailable in chat. Read controls still work. Assistant instructions explain the same effective behavior regardless of saved write permission. The operator test console retains its separate nonce-confirmed execution path and existing backend permission checks; this work does not certify that path as the future unified approval service.

Full R02 remains open: R03 must first provide immutable caller identity and explicit data scope. Then implement a durable pending/approved/dispatching/outcome approval operation bound to subject, tool, canonical arguments, connector revision, policy revision and expiry. Until that complete enforcement and pinned-client acceptance passes, keep this hard deny in place. Do not restore write execution just because a UI shows a confirmation prompt.

### R04 implementation details

`Connector.request` accepts explicit `retry_safe`, defaulting to false. No tool-name heuristic establishes safety. Only reviewed read calls and `tools/list` pass true. No write support is being claimed through this flag; writes remain blocked above transport.

Initialization failures before dispatch may retry once. After the call has been dispatched, HTTP, URL and OS transport failures on an unsafe operation clear the session and return an unknown-outcome error without reissuing it. The message tells the caller to inspect the app before trying again. Safe reads can retry transport failures and session-rejection HTTP 400/404 once; other HTTP errors do not loop. A cleared session is initialized before every following dispatch.

A real commit-then-disconnect connector fixture, durable `outcome_unknown` storage and a reconciliation UI are still needed before enabling writes. The current regression injects the reply-loss failure at the transport seam and proves dispatch count is one.

### R05 implementation details and remaining work

`PolicyStore.get` bounds the policy read, parses JSON, validates the version, app/upstream shape, category switches, tool/category references and read/write access values. Invalid switch values cannot become truthy grants. Malformed structural exceptions are normalized to policy errors. Missing/unreadable/invalid policy clears cached policy and connector sessions; health and MCP POST return 503 rather than authorizing with the old snapshot. Identical timestamps do not hide changed content.

`ctl.mcp_gateway.write_policy` holds the existing interprocess lock across snapshot construction and publication. It uses the shared `write_atomic` helper, whose temporary file is unique, owner-only from creation, flushed and replaced atomically. No new private-file implementation is introduced.

Full R05 is not complete. Durable monotonic policy revisions, authenticated rollback/replay handling across gateway restart, externally visible revision diagnostics and token-rotation coverage remain. If publication fails while an older valid file remains present, read authorization can still reflect that older policy; a separate revocation epoch or acknowledgment contract is needed to close that case. All chat writes stay denied independently of the retained file.

## Regression and integration evidence

New tests were run against the old code first and failed for the intended reasons: enabled direct/wrapped writes executed, missing/unreadable policy preserved authority, equal timestamps hid revocation, unsafe calls were resent, read retry lacked the new contract, starter forwarded dashboard requests, identity stripping was missing, and the installer order was reversed. Subsequent upgrade regressions also failed before the configuration-stamp and legacy-runtime fixes.

- `tests/test_gateway_security.py` checks every write path, effective discovery, cached-policy revocation, equal-timestamp changes, malformed access, unsafe transport dispatch count, safe session recovery, private serialized publication, actual HTTP gateway behavior with a valid bearer token, legacy-image write denial, source-change rebuilds, build-failure handling and release-image preservation.
- `tests/test_ingress_security.py` launches only uniquely named `mu3lab-test-ingress-*` containers using the repository's pinned Caddy image, ephemeral loopback ports, a mock identity provider and the real FastAPI application through a test upstream. It checks anonymous forgery, absent identity response fields, genuine operator CSRF issuance and identity-provider failure without dashboard fallback.
- Bootstrap/installer tests assert gate-before-publication order, legacy runtime replacement and restart requirements. Existing registry and discovery expectations were updated to the intended denial behavior.
- Dashboard tests assert saved-enabled writes remain unchecked/disabled and read/category controls continue to work.

The Caddy integration test is explicitly opt-in to keep the normal test suite independent of Docker. Run it with:

```sh
MU3LAB_TEST_CADDY=1 .tools/bin/uv run --frozen python -m unittest tests.test_ingress_security -v
```

The test container keeps the image-required `NET_BIND_SERVICE` capability (the image's executable carries that capability), drops other capabilities, uses a non-root user, and mounts only its generated temporary config. No owner-host service or installation container is stopped.

### Final validation (2026-10-08)

| Check | Result | Limits |
|---|---|---|
| `make format` | Passed | Existing review evidence's Python code block was reformatted without changing the historical reproduction |
| `make verify` | Passed with pinned uv/Python and pinned Node-container workflow | Includes app-ID check, Ruff lint/format, API schema check, mypy, Python tests, dashboard checks/build; does not mean all CI security/release gates ran |
| Python discovery | 738 run, 732 passed, 6 skipped | Four existing live integrations and two opt-in Caddy tests skipped in default discovery |
| Dashboard checks | 98 tests in 15 files passed; ESLint, Prettier, API generation and TypeScript passed | No real browser or mobile acceptance implied |
| Production dashboard build | Passed | Main chunk 632.25 kB / 204.12 kB gzip; existing chunk-size warning remains under R24 |
| Explicit real-Caddy boundary suite | 2 passed | Pinned Caddy with mock identity provider and real API; includes forged GET and mutation requests, valid operator CSRF, and identity-server failure |
| Temporary rendered Compose validation | 36/36 passed | Configuration only; no production app startup |
| Diff whitespace check | Passed | No secret scan/provenance certification implied |
| Fresh-VM interrupted install/upgrade; real Authentik/Tailscale sign-in; pinned LobeChat acceptance | Not run | R01/R02 release acceptance remains pending |

The existing FastAPI/Starlette deprecation and unclosed subprocess/socket resource warnings remain tracked under R29/R35. Dashboard dependency installation reported zero vulnerabilities for its audited dependency set; this is not a scan of every Python dependency, connector or image.

No production services were deployed or restarted. The test Caddy containers were removed after execution. The second registered worktree remains untouched and clean. All implementation and tracker changes are committed on `fix/review-priority-a`; nothing was pushed.

**Status:** A1/A2 and immediate A3 containment are implemented and locally checked. R01/R04 are marked implemented, R02/R05 remain in progress, R33 has only its chat-related caveats addressed. No finding is marked release-verified.

## Next work in the authorized plan

Update: R08/A4 and the immediate R20/A5 route task are now implemented; see [the subsequent mutation/readiness record](2026-10-08-mutation-readiness-implementation.md). The list below preserves the original handoff; use the main review table for current statuses.

1. **R08 / A4:** inventory all idempotency lookup/create callers before changing the store contract. Bind keys to immutable actor and operation namespace; compare canonical request fingerprints; return explicit conflicts for changed payloads and different active operations. Update request/state writes and API error contracts together, and regenerate API clients if response contracts change. Reproduce queued start followed by stop/delete as a failing test before edits.
2. **R20 / A5:** require verified required routes for usable status; preserve valid internal-service cases. Reproduce failed route probes across API projection and dashboard before changing status derivation.
3. **R03 then full R02:** settle personal/shared/operator scopes from existing product decisions, implement subject-bound gateway credentials, and build durable per-call approval before replacing this tranche's write deny.
4. Complete the R01 fresh-VM and pinned-chat-client acceptance gates, and finish R05 policy revision/revocation acknowledgment. The test results below must not be used as a deployment or release acceptance certificate.
