# Status checks, gateway limits and durable revocation — 2026-10-08

This continues the [project review](2026-10-07-project-review.md) after the
[gateway authority record](2026-10-08-gateway-authority-implementation.md). It covers
R20 (completion), R06 and R07. The review table remains the authoritative tracker.

Owner: Claude. Branch: `fix/review-priority-a`. Baseline: `4a9975f`. Both registered
worktrees were clean; `/home/dak/Desktop/Mu3Lab-expansion` was not touched. Nothing
was pushed, deployed or restarted.

## Owner decisions (2026-10-08)

| Question | Decision |
| --- | --- |
| Keep sign-in evidence current | Re-check periodically. Evidence older than 24 hours, or a failed latest check, removes the usable claim. |
| A working route misses a check | Short, bounded grace: shown as *Checking* with Open still available, for at most two consecutive missed probes or 120 seconds since the last success. |
| Scope of deactivation | Durably revoke what Mu3Lab issues; document native app tokens per app as residual risk. Per-app session revocation is later work. |

## R20 — usable status from fresh, successful required checks

### Contract

`ctl/status/checks.py` defines one typed `Check` per requirement: `process`, `route`,
`sign_in`. Each carries `state` (`pass`, `checking`, `fail`, `pending`, `stale`,
`not_required`), `detail`, `checked_at`, `last_success_at`, `last_failure_at` and
consecutive `failures`. Only `pass`, `checking` and `not_required` support a usable
claim.

| Check | Required when | Evidence | Window |
| --- | --- | --- | --- |
| Process | Installed | Declared health probe and running Compose project | Observation age ≤ 90 s |
| Route | App declares a private HTTPS port | Real HTTPS request through Tailscale and Caddy | Pass resets; two missed probes or 120 s since last success fails |
| Sign-in | Authentik `oidc`, `trusted_header` or `gate` app with a browser UI (not the identity provider) | Browser-path sign-in check, rerun by the worker | Rechecked every 6 h; retried after 15 min on failure; usable only if ≤ 24 h old |

The worker stores process and route checks with each observation and reads the
previous observation first, so history survives cycles. A cached probe repeats its
original check time and is not counted again. A failed probe is cached for 30 seconds
(a successful one for 60), so a blip is confirmed or cleared quickly.

A Serve status that lists no listener for the app fails immediately with a specific
reason. A Serve status that cannot be read no longer marks every route missing; the
probe decides. An unknown tailnet address counts as a missed check.

The API judges age at read time: observation age (process and route become `stale`)
and sign-in evidence age. It never runs probes. When a check blocks, the response
names it in `blocking_check`, clears launch links and states the reason. Sign-in
blocking keeps `route_ready` true and reports the identity as `configuring` (never
checked) or `degraded` (failed or expired).

### Display states

| Situation | Display | Open |
| --- | --- | --- |
| All required checks pass | Running | Yes |
| Route missed one probe within grace | Checking | Yes, last success visible |
| Sign-in not yet checked (for example right after upgrade) | Checking | No |
| Route failed, Serve route missing, sign-in failed or older than 24 h | Needs attention, specific reason | No |
| Observation older than 90 s | Needs attention: status not refreshed | No |
| Recovery after a real successful probe/check | Running | Yes |

Internal services (no private port) need neither route nor sign-in. Separate-login
apps (`local`) and the identity provider never claim verified sign-in.

### Periodic sign-in verification

`ctl/status/sign_in.py` runs on its own worker thread every 60 seconds, beside the
status observer and independent of jobs. It checks only due apps whose latest route
check passed and that have no active job, using `verify_sign_in(..., patience=0)`
so each step is tried once instead of waiting minutes as an install does. Results
update `service_identity_state` (`ready` with a new verification time, or
`degraded` with a redacted reason). Nothing signs anyone in.

After upgrade, apps with no saved evidence (gate apps and core apps never recorded
one) show *Checking* until their first periodic check, normally within a minute of
the worker starting.

### Tests

`tests/test_status_checks.py`: grace and its bounds, cached-probe non-counting,
recovery, missing and unreadable Serve, unknown address, malformed history, worker
history carry-over, and through the real API: all-pass, stale observer, grace shown
as checking, failed route, unverified, expired and failed sign-in, internal service,
schedule and refresh behaviour (once per step, failure recorded, busy app skipped).
Dashboard tests cover the *Checking* label and required-check list.

### Remaining R20 acceptance

- Disposable installation: stop Tailscale Serve for one app, break an Authentik
  provider, stop the worker; confirm each state and recovery on desktop and phone.
- R21 still owns whole-cycle budgets; a single slow cycle can still approach the
  90-second window.

## R06 — gateway validation and resource bounds

All changes are in `platform/tool-gateway/gateway.py` (shared pin helper in
`gateway_authority.py`; policy field in `ctl/mcp_gateway.py`).

| Area | Behaviour | Limit |
| --- | --- | --- |
| JSON-RPC envelope | `parse_message` requires one object, `jsonrpc: "2.0"`, a string method, an integer or short string id, and object params. Parse error −32700 and invalid request −32600 answer HTTP 400; invalid params −32602 and unknown method −32601 answer as JSON-RPC errors. `tools/call` requires a string `name` and object `arguments`. | id/method ≤ 128 chars |
| Nesting and size | Requests deeper than the limit are refused before use; arguments stay capped at 64 KB and validated against the verified schema before any dispatch. | depth 32; body 256 KB (413 above) |
| Schema drift | Policy carries `schema_sha256` for each tool whose input schema Mu3Lab captured when it verified the connector. A tool whose live schema no longer matches is hidden from `tools/list`, shown off in `find_tools` with a re-verify note, refused by `call_tool`, and refused before an approved write is claimed. | — |
| Rate limits | Token buckets per person, credential provider and app: all requests, and separately tool calls. Excess gets HTTP 429 with `Retry-After`. Bucket table is bounded. | 40 burst + 2/s; calls 15 burst + 0.5/s |
| Concurrency | At most N requests at once per principal (HTTP 429), and a fixed number of open connections (excess refused with an immediate 503, not queued). Waiting for a busy connector is bounded and answers "busy". | 4 per principal; 32 connections; 30 s connector wait |
| Deadlines | Socket reads time out; the body must arrive within its own deadline however slowly it trickles (408). Each request has a whole-request deadline covering connector wait, initialization and upstream reads; upstream bodies are read in chunks and stopped at the deadline or size cap. An approved write keeps a single 120 s dispatch window and is never retried. | socket 10 s; body 10 s; request 90 s |
| Stream replies | SSE events are parsed per event (multi-line `data:` joined); the reply is the message whose id matches the request. Notifications and server-initiated requests are ignored; a stream without the reply is an error (an unknown outcome for a write). A plain JSON reply with a different id is refused. | — |
| Output | Total budget covers text and non-text content; oversized or deeply nested items and items past the count cap are left out with a note; structured content must fit and stay shallow. | 60 000 chars; 50 items |
| Health | `/live` answers whenever the process does. `/health` stays readiness (policy committed and acknowledged) and now reports capacity counters: active requests, open and maximum connections, rate-limited, busy and refused counts. No secrets or subjects are exposed. | — |

Older policies without `schema_sha256` keep today's behaviour until the control
plane republishes policy, which happens on the next connector or switch change and
on gateway refresh. Older gateway images ignore the new field.

### Residual limits

- Pinning is trust-on-verification: Mu3Lab pins the schema it saw when it last
  verified (enabled, started or updated) the connector. A connector that already
  presents a changed schema at that moment is pinned as presented; the review
  covers tool names and read/write access, not schemas.
- Limits are per gateway process and reset on restart. They are sized for a
  household, not for hostile multi-tenant traffic.
- Authentication failures are not rate limited separately; bearer tokens are
  256-bit random values and failures return before any connector work.

### Tests

`tests/test_gateway_limits.py`: envelope errors and depth; SSE id matching,
multi-line data and missing replies; schema drift withheld on every path and never
dispatched, matching pins run, invalid arguments never dispatched, policy pins the
verified snapshot; output caps; rate-limit buckets and table bound; busy connector,
trickling and oversized upstream bodies; and through a real bounded HTTP server:
malformed params, oversized body, per-person 429 with `Retry-After`, concurrent slow
tools bounded per person, slow client body cut off with 408, excess connections
refused, liveness independent of readiness.

## R07 — durable deactivation and demotion

### Contract

`ctl/access.py` and migration `0004_access_revocation.sql` add two tables:

- `access_holds`, keyed by Authentik subject: `deactivated` or `demoted`, with who
  recorded it and when. This is Mu3Lab's record of intent.
- `access_revocations`: one row per (subject, target) with `pending`/`done`,
  attempts, next attempt time and a redacted last error. Rows cascade with their hold.

| Change | Immediate effect | Retried revocation targets |
| --- | --- | --- |
| Deactivate | Hold recorded before any external call. `resolve_identity` treats the subject as signed out of Mu3Lab (no role, no writes) whatever the Authentik session still claims. Chat tool (gateway) credentials are revoked before the API answers. | Authentik account disabled; Authentik browser sessions and tokens (including app passwords) deleted; gateway credentials and pending approvals revoked; voice key deleted; Mu3Lab chat assistants detached from their tools (conversations and agents kept). |
| Demote administrator | Hold recorded; Mu3Lab treats the subject as a household member at once. | Administrator and Authentik superuser groups removed; gateway credentials revoked; chat assistants detached. |
| Reactivate | Deactivation hold and its outstanding tasks removed; Authentik account re-enabled. An unfinished demotion is re-recorded so reactivation cannot hand back an administrator role. | — |
| Promote | Demotion hold removed. | — |

A failed Authentik call during deactivation or demotion no longer fails the change:
the hold already denies Mu3Lab access and the Authentik task is retried. The person
lookup and the last-administrator check still need Authentik. A held administrator
no longer counts toward "at least one administrator". `operator_subjects()` excludes
held subjects, so chat synchronization cannot re-grant them gateway credentials; it
already removes managed assistants for anyone who is not a current operator.

The worker runs due tasks on its own thread every 30 seconds, independent of jobs.
Failures back off 30 s, 60 s, 120 s … up to one hour, and continue until done. The
people API also starts an immediate attempt in the background after a change, so the
common case finishes within seconds. Tasks are idempotent and check that a username
still belongs to the same subject before touching an Authentik account.

The People page shows "Sign-in disabled; N access revocations pending" (or
"Administrator access removed; …") with the last problem, and polls every 5 seconds
while anything is pending. The removal confirmation now says that some apps' own
mobile logins can keep working until they expire.

### Residual risk: native app sessions and tokens

Disabling the Authentik account and deleting its sessions stops new sign-ins
everywhere and stops Authentik-gated requests. It does not reach credentials an
app issued itself after a successful sign-in. These are expected behaviours from
each app's design and have not yet been verified against each pinned version; that
verification is part of release acceptance.

| App | Sign-in | What can keep working after deactivation | Owner action until per-app revocation exists |
| --- | --- | --- | --- |
| Immich | OIDC | Existing web/mobile sessions and personal API keys | Delete the user's sessions/keys or the user in Immich administration |
| Nextcloud | OIDC | Web session; app passwords and device tokens used by desktop/mobile sync | Disable the user in Nextcloud Users, which also blocks app passwords |
| Paperless-ngx | OIDC | Web session; REST API tokens (mobile apps) | Delete the user's token or disable the user in Paperless administration |
| Audiobookshelf | OIDC | Mobile app tokens | Disable the user in Audiobookshelf settings |
| Mealie | OIDC | Session token; long-lived API tokens | Delete the user's API tokens or the user in Mealie administration |
| Actual Budget | OIDC | Existing session token until it expires or is logged out | Remove the user in Actual's user management |
| AdventureLog | OIDC | Existing web session | Deactivate the user in AdventureLog administration |
| Dawarich | OIDC | Per-user API key used by location-tracking apps (uploads continue) | Rotate the user's API key or remove the user in Dawarich |
| Open WebUI | OIDC | Session token (lifetime is Open WebUI's JWT setting) and API keys | Set the user's role to pending or delete the user in Open WebUI |
| Outline | OIDC | Web session; API tokens | Suspend the user in Outline settings |
| RomM | OIDC | Existing web session | Disable the user in RomM administration |
| LobeHub (chat) | OIDC | Existing chat browser session until it expires. Mu3Lab's own chat key for the person stays with Mu3Lab; its assistants lose their tools. | None for Mu3Lab-managed tools; sign the person out in LobeHub if it offers it |
| Grocy, Baby Buddy, Beaver Habits | Authentik trusted header | Every request passes the Authentik outpost; access ends with the deleted session. Grocy API keys the person created remain. | Delete the person's Grocy API keys |
| LiteLLM, FreeLLMAPI, SurfSense | Authentik gate | Gated by the outpost; app-local logins behind the gate are administrator accounts, not per person. Voice keys are revoked by Mu3Lab. | — |
| Vaultwarden | Separate login | The person's vault account and its device sessions are independent of Authentik and are not disabled. | Disable or delete the user in the Vaultwarden admin page if their vault access should end |

### Tests

`tests/test_access_revocation.py`: deactivation while voice and chat are down denies
Mu3Lab access at once, revokes gateway credentials before the answer, keeps the two
failed targets pending with their reason, respects backoff, and completes after
recovery without another request; backoff doubling and cap; Authentik outage keeps
the deactivation and finishes later; held subjects get no operator credentials;
reactivation releases and cancels; repeat deactivation re-queues; a pending hold
does not count as an administrator; demotion keeps membership but removes
administration, retries a failed group change, survives deactivate/reactivate, and is
released by promotion; session and token deletion is limited to the person; a
reused username belonging to someone else is never touched; a held operator's
session is refused administration through the real API; an unreadable store fails
closed. `tests/test_people.py` keeps passing on an isolated store.

### Remaining R07 acceptance

- Real Authentik: confirm session and token endpoints and filters on the pinned
  version, and that deleted sessions end outpost-gated access promptly.
- Disposable install: deactivate someone while LiteLLM and chat are stopped, start
  them, and watch the People page reach "complete" with no further action.
- Verify each row of the residual-risk table against the pinned app versions;
  per-app revocation where an API exists is later work.
