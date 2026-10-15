# Milestone C review — 2026-10-11

Review date: **2026-10-11**. Reviewer: Claude Code, requested by the owner. Follows the [Milestone A](2026-10-11-milestone-a-review.md) and [Milestone B](2026-10-11-milestone-b-review.md) reviews.

Scope: [Milestone C](2026-10-07-project-review.md#milestone-c--household-trust-and-recovery-product):

| Part | Findings | Topic |
|---|---|---|
| C1 | R03, R02 | Operator-only scope, durable approvals |
| C2 | R06, R07 | Bounded gateway, revocation |
| C3 | R14 | Kopia, platform backup, recovery sheet, schedule, backup browser, `--restore`, Home warning |
| C4 | R17, R18 | Self-update rollback, trust-boundary docs |
| C5 | R19, R31 | Calendar, AI switch |

Live system: a **fresh install** made for this review. The owner ran `./uninstall.sh --everything`, then cloned `fix/review-milestone-c` at `967c4a4` from GitHub and ran `./install.sh`. All 26 apps were running.

**Verdict:** most of C works as promised. The approval drill, gateway limits, the local-only AI switch and the recovery-sheet protections all passed live. But **the Mu3Lab self-backup cannot restart Authentik (CRITICAL)**, and the weekly schedule runs that backup automatically. Health score for Milestone C: **6 / 10**, about 8.5 once C-C1 is fixed.

## Live results

| Check | Result |
|---|---|
| R06 gateway limits (discovery-only credential) | Params as a list: -32602. 40-deep JSON, batch, bad JSON, long id: 400. Rate limit: 429 with `Retry-After`. Stalled body: 408 at exactly 10 s. The discovery credential cannot call tools. |
| **R02 approve/deny drill** (Grocy `shopping_list_add_item`, switched to needs-approval, then back off) | Chat request: pending with an approval link. Execute before approval: refused. Rejected request: never ran. Approval page: exact arguments shown. Approved request: ran **once**. Approve again: 409. Execute again: returned the recorded result with no second dispatch. Grocy held exactly one item; the reviewer then deleted it. |
| Default policy | All 7 connected apps have every write tool off |
| Kopia UI (backup browser) | Authentik binding is admins only. Anonymous tailnet visitors are sent to sign-in (302); direct loopback without Kopia's password gets 401. |
| Recovery sheet | `Cache-Control: no-store`; bad CSRF and household member get 403; audit entry written without the password |
| **R31 local-only drill** (Qwen 3.5 2B) | Job downloaded and checked first. LiteLLM then had **no outside route**, and `openai/auto:smartest` and `gpt-4o` were refused. Local answer "pong" in 2.7 s. LobeChat label: "local only, on this server". Switching back restored outside routes and deleted the model. |
| AI apps bypassing LiteLLM | None. LobeChat, Open WebUI (chat, RAG, STT, TTS) and SurfSense all use `litellm:4000`. |
| R19 calendar reads | Two of three read tests left calendar state unchanged. The code writes only when Nextcloud rejects the saved password (it records `authentication_expired`). One `calendar_connections` change during the first test was not reproduced and does not match the row's `updated_at` (20:35:29); treat it as unexplained, not confirmed. |
| Fresh-install defaults | Search model BGE-M3; outside providers allowed; schedule weekly, Sunday 03:30 |
| **Platform backup ("Back up now")** | Saved Mu3Lab's state and 6 core apps, then **failed to restart Authentik**. See C-C1. |

## Findings

### C-C1 — CRITICAL — The Mu3Lab self-backup leaves Authentik stopped; the weekly schedule triggers it

`platform_backup` stops the core apps, backs them up and restarts them with `maintenance.start_app`. That generic start runs `docker compose up` in `project_path(service)`. Authentik has no Compose file under `/srv/mu3lab/projects/authentik/`, so the start falls back to `apps/authentik/` **without** the two settings Authentik's Compose file needs: `AUTHENTIK_ENV_FILE` and `AUTHENTIK_POSTGRESQL__PASSWORD`. Only the installer (`_authentik_compose_env`) supplies them.

Observed live, in job `700c2502…`:
- The backup saved, then the job failed with `platform_backup_incomplete`: "env file … not found", "AUTHENTIK_POSTGRESQL__PASSWORD variable is not set".
- All three Authentik containers stayed exited. **Nobody could sign in to the dashboard or any app.**
- The reviewer restarted Authentik by hand with the installer's settings. The outage lasted about 11 minutes.

Why it matters:
- The default schedule (weekly, Sunday 03:30) queues this same job, so the household would be locked out every Sunday morning until someone fixes it in a terminal.
- `platform_backup.recover`, which restarts apps after an interrupted backup, uses the same start and fails the same way.
- `tests/test_platform_backup.py` uses a fake start, so the gap was invisible to the tests.

Fix:
- Start Authentik through one function that both the installer and the backup use. One way is to materialize Authentik like the other core apps, with its Compose file in `projects/authentik/` and `AUTHENTIK_ENV_FILE` written into that project's `.env`, so a plain `compose up` works. Another is to give `maintenance.start_app` the identity provider's Compose environment.
- Add a test that `start_app(authentik)` passes the same Compose environment as `fix_authentik`.
- Run the opt-in real-Docker check of a platform backup round trip.

Until it is fixed, **turn the backup schedule off** or expect a manual Authentik restart after each run.

### C-M1 — MEDIUM — R17's automatic rollback cannot run yet

`self_update` and `update_guard` only handle **tagged releases** (`platform_releases.assets` downloads and checksums the release's dashboard from GitHub). The repository has **no tags and no releases**, so every install is a "development checkout" and the dashboard's updater is blocked. The guard's logic reads correctly: a 5-minute steady check, then restore of code, packages and the database copy if the schema changed. But today's only update path is `git pull` plus `./install.sh`, which has no automatic undo. Publish a first release, or document this limit where the R17 status is claimed.

### LOW

| # | Finding |
|---|---|
| L1 | Four gated apps (Baby Buddy, Beaver Habits, Grocy, SurfSense) have **no Authentik policy binding**, so any active Authentik account passes the gate. OIDC apps are bound to the household groups. This works by design ("everyone Authentik knows is the household"; `authentik_blueprints.py`), but it is a missing second lock, for example while a removal's revocation is still pending. Bind gated household apps to the household groups too. |
| L2 | Home never prompts a brand-new install to make its first backup. The warning starts only once the first scheduled run is overdue (about 8 days with the weekly default). Suggest a "make your first backup" note. |
| L3 | `calendar_reader` checks only for a subject, so a deactivated person whose Authentik session survives can still read their **own** calendar until the session is revoked. Writes are blocked. |
| L4 | The "Fastest" reviewed chat model (Qwen 3.5 2B) uses hidden reasoning by default: 1,503 reasoning characters for a one-word reply. Consider turning thinking off for that entry. |
| L5 | A second execute of an already-finished approval adds another `succeeded` line to the gateway call log (0 ms, no dispatch), which reads like a second write. Log it as `replayed`. |
| L6 | The recovery password is 64 characters. The QR code and check code help, but typing it at `--restore` is error-prone; consider grouping it in blocks on the sheet. |
| L7 | `update_guard._record` writes through a fixed `.tmp` name, the pattern R16 removed elsewhere. |

### Reviewed as code (not live)

- **R07 revocation** (`ctl/access.py`):
  - an immediate local hold;
  - durable per-target tasks with backoff;
  - a username reused by someone else is never touched;
  - demotion removes the admin groups.

  Not exercised live: the reviewer does not create Authentik accounts. 23 tests cover it.
- **R14 `./install.sh --restore`** (`ctl/rebuild.py`):
  - refuses a machine with existing data;
  - checks the password before any change;
  - restores Mu3Lab's state, then each app;
  - cancels the old machine's running jobs.

  A rehearsal on a second machine is still outstanding.
- **R18:** the trust boundary is documented in [architecture.md](../architecture.md#trust-boundary); enforcement was deferred by the owner.

## Live side effects of this review

- **Platform backup:** one run (`97cdf85b` plus core-app snapshots).
- **Authentik:** stopped by C-C1 and restarted by the reviewer with the installer's settings. Its containers were recreated (`--force-recreate`); the database was untouched.
- **Grocy drill:**
  - `shopping_list_add_item` was switched to needs-approval, then back to disabled;
  - one approved operation and one rejected operation remain in the approval history;
  - the test shopping-list item was deleted.
- **AI settings:** switched to local only with Qwen 3.5 2B, then back to the original settings (outside allowed, no chat model, BGE-M3). The model was deleted.
- **Recovery sheet:** opened once (an audit entry records this).
- **Discovery credential:** used for the R06 tests (rate-limit counters only).

## Remediation order

1. **C-C1:** fix the Authentik restart and add a real-Docker test. Until then, switch the schedule off.
2. **From the B review:**
   - **B-H1:** the journal drops each release's variant and definition;
   - **B-M1:** retention can delete deliberately made backups.
3. **C-M1:** publish a first tagged release so R17 can actually protect updates.
4. **L1–L7** and the remaining A and B LOW items.
