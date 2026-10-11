# Milestone A review — 2026-10-11

Review date: **2026-10-11**. Reviewer: Claude Code, requested by the owner.

Scope: [Milestone A](2026-10-07-project-review.md#milestone-a--contain-the-demonstrated-safety-gaps) (A1–A5: R01, R02, R04, R05, R08, R20, immediate R33), plus nearby issues found along the way. Milestones B and C follow in separate records.

Code reviewed: branch `fix/review-milestone-c` at `967c4a4`. Live system tested: `Mu3Lab-live` at `e74b5da` (7 commits behind the branch; Milestone A code is identical in both). All apps that can be stopped were stopped by the owner beforehand.

**Verdict: Milestone A is implemented and works as promised. No CRITICAL or HIGH findings.** One MEDIUM finding next to Milestone A (Firecrawl route open to the tailnet) and several LOW tidy-ups. Health score for Milestone A: **8.5 / 10**.

## Method

- Read the code for A1–A5: `apps/ingress/Caddyfile*`, `ctl/api/security.py`, `platform/tool-gateway/gateway.py`, `gateway_authority.py`, `ctl/mcp_gateway.py`, `ctl/api/routes/tool_approvals.py`, `ctl/jobs.py`, `ctl/api/routes/services.py`, status checks.
- `python -m unittest discover -s tests -t .`: **1064 run, all pass, 10 skipped** (opt-in live tests).
- `MU3LAB_TEST_CADDY=1 … tests.test_ingress_security`: **2 pass** (real pinned Caddy, mock identity provider, real API).
- An extra disposable Caddy test (pinned `caddy:2.11.4-alpine`, mock auth and echo servers), to check `copy_headers` when the auth reply omits a header.
- Live probes of the running install, through the tailnet and on loopback. To act as a signed-in operator, the reviewer sent requests the way Caddy does, using the proxy token read in-process from the secret store (never printed).
- **Live side effects (all reverted):**
  - Two lifecycle jobs with actor `review-probe`: start, then stop, Beaver Habits.
  - Tailscale Serve `:8461` removed and restored twice, about 3 minutes in total.

## Milestone A item by item

| Item | Promise | Evidence | Verdict |
|---|---|---|---|
| A1 (R01) | No forged identity at the dashboard | Forged `X-Authentik-*` and proxy-token headers through the tailnet: 302 to Authentik. The same headers straight to `127.0.0.1:8787` without the proxy token: anonymous identity, and the mutation is refused. The installer phase order is `authentik_setup → dashboard_protection → serve`. The extra Caddy test showed 2.11 removes request headers absent from the forward-auth reply, so trusted-header app routes (Paperless `Remote-User`, Grocy `X-Mu3lab-User`, `X-Mu3lab-Email`) cannot be spoofed. API, gateway, Authentik and Vaultwarden listen on loopback only; nothing listens on the LAN interface. | Pass |
| A2 (R02) | No chat write without per-call human approval | Writes become `pending` operations bound to subject, provider, credential version, app, connector revision, schema hash, canonical-input HMAC, policy revision and a 300 s expiry. `claim` moves `approved → dispatching` once, in a write transaction, immediately before dispatch. Unknown or bad bearer: 401 before the body is read. Unknown app: 404. Gateway container: non-root, read-only rootfs, `cap_drop: ALL`. `tests/test_gateway_authority.py` (16) and `tests/test_gateway_security.py` (12) cover deny, expiry, wrong person, altered inputs, replay, concurrency, revocation and restart. | Pass (code and tests); real chat-client acceptance still pending |
| A3 (R04/R05) | No automatic resend of writes; missing policy denies | `Connector._request` raises `UnknownOutcome` after an unsafe dispatch and never retries. `PolicyStore.get` re-reads every time and clears cached authority on a missing, invalid or stale file. | Pass |
| A4 (R08) | Request identity and busy/conflict responses | Live: replay of the same key gave the same job (`duplicate: true`). Stop while start was queued gave 409 `resource_busy`. The same key with a different action gave 409 `idempotency_conflict`. A 200-character key gave 400 `invalid_request_key`. | Pass (live) |
| A5 (R20) | Usable state needs fresh, successful required checks | Live: 26 stopped apps show `stopped`, `route_ready=false` and no launch URL. After removing Beaver Habits' Serve route, the next observation (≤ 10 s) showed `needs_attention`, the UI state `route_pending`, no URL, and the reason "route is not published in Tailscale Serve". It returned to `ready` on its own after restoring. Sign-in evidence from install time was reused, within the documented 24 h window. | Pass (live) |

## Findings

| # | Severity | Dimension | Finding | Why it matters |
|---|---|---|---|---|
| M1 | MEDIUM | Security | Firecrawl's tailnet route is `access: open` ([apps/firecrawl/app.yaml](../../apps/firecrawl/app.yaml)) and Firecrawl runs with `USE_DB_AUTHENTICATION: "false"`. An unauthenticated `POST /v1/scrape` from the tailnet succeeded. | Any tailnet device can use the server as an anonymous scraper: memory, bandwidth, home-IP reputation. A scrape of the LAN router (`192.168.68.1`) failed with only the `pdf`/`document` engines tried, so local-network SSRF appears blocked. No Mu3Lab component uses this route; the connector reaches Firecrawl internally. |
| L1 | LOW | Security | `GET /api/v1/services` accepts an anonymous `Identity` ([ctl/api/routes/services.py](../../ctl/api/routes/services.py) `list_services`). It returns app list, versions, container names and errors. | Reachable only on loopback (Authentik blocks it via Caddy), so it is a missing second lock. No internal caller relies on anonymous access. |
| L2 | LOW | Security design | The manifest `route.access` defaults to `"open"` ([ctl/manifest/models.py](../../ctl/manifest/models.py)). | A new app that omits the field is published ungated. The value should be required. |
| L3 | LOW | Performance | Every operator request runs `_record_operator_traversal`, which takes a file lock, rewrites `bootstrap.steps`, and decrypts the secret store 2–3 times ([ctl/api/security.py](../../ctl/api/security.py)). Measured: about 6 ms extra per request. | Negligible load, but `confirmed_at` now means "last seen" rather than "first confirmed". |
| L4 | LOW | Performance | The gateway calls `check_policy(acknowledge=True)`, a SQLite write transaction, on every POST, including unauthenticated ones, and on every `/health`. | 200 unauthenticated requests took 1.3 s, so this is not exploitable. It is wasted work before authentication. |
| L5 | LOW | Robustness/UX | A route failure shows `needs_attention` with `recommended_action: none`. `retry_setup` is offered to operators, but the hint does not point to it. | Non-technical operators may not know what to press. |
| L6 | LOW | Maintainability | The member check returns "operator identity required" with code `request_failed`. | Misleading message. |
| L7 | LOW | Logging | The gateway suppresses HTTP logging. The API log is dominated by 10 s snapshot polling. 5 modules use `logging`; there are 96 `print()` calls in `ctl/`. | Thin trail for production troubleshooting. |
| — | known (R27) | Architecture | `ctl/install.py` is 2,727 lines. 131 function-level `from ctl` imports across 36 files suggest import cycles. | Tracked in Milestone D. |
| — | known (R18) | Security | `mu3lab-ctl` and `mu3lab-worker` run as the owner's user, which is in the `docker` group. | Documented; enforcement deferred by the owner. |

Clean results:
- No hard-coded secrets.
- SQL is parameterized; the only f-string builds `?` placeholders.
- No `shell=True`, `yaml.load` or `pickle`.
- `dangerouslySetInnerHTML` is used only for locally generated QR SVG.
- All 49 container images are pinned by digest; Python dependencies are pinned exactly, and the gateway's are hash-pinned.
- One app-ID branch remains (`ctl/mcp_ops.py`, `nextcloud-context-agent`).

Tech stack notes (LOW):
- The tool gateway uses stdlib `http.server`, mitigated by its own connection, deadline and rate limits.
- The dashboard is on React 18; 19 is current.
- Starlette `TestClient`/`httpx` deprecation warning, plus ResourceWarnings in tests (R29/R35).

## Remediation

There are no red alerts. Suggested order:

1. **M1:** set Firecrawl `route.access: gate` (Authentik in front), or remove the route if no person opens it in a browser.
2. **L1:** `list_services(identity: Member)`.
3. **L2:** make `route.access` required in the manifest model.
4. **L3/L4:** write the traversal record only when it is not already recorded; acknowledge gateway policy only when its digest changes.
5. **L5–L7:**
   - Recommend "Retry setup" on route failure.
   - Fix the member-required wording.
   - Log gateway refusals and errors as metadata only.

## Still open (unchanged by this review)

Milestone A's exit criteria still require:
- a fresh-VM interrupted-install test, which the owner declined for now;
- a real sign-in on a phone;
- a real chat-client approve/deny drill, which needs LobeChat running and a write-capable connector.
