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
