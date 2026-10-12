# Stacked A–C merge review — 2026-10-12

**Decision: do not merge yet.** Focused fixes are included, but the recovery and
privacy defects below are unresolved merge blockers. This is an implementation
review checkpoint, not completion of every requested adversarial case or release
acceptance gate. In particular, a green CI result must not override these findings.

## Scope and installation protection

- Fetched `origin`; remote main and merge base both resolve to
  `ca299152c3a08a1ee0c7d4695e9e8938f4a880e8`.
- The supplied branch tip is `967c4a4862a64345340421a023b4be264a2b68f4`.
  Its complete difference from main contains 32 commits and 240 changed files,
  including Milestones A, B and C. No earlier milestone was dropped.
- The original checkout was clean. The running `ctl.worker` uses
  `/home/dak/Desktop/Mu3Lab-live`, so review changes were made in the isolated
  `codex/review-milestone-c` worktree, based on that exact tip.
- Tests use temporary runtime roots. Container integrations use disposable
  mounts and unique names; an additional local Docker wrapper rejects references
  to `/srv/mu3lab` and the live checkout. No installer, reset, restore, uninstall,
  service restart, deployment or release publication was run against the live stack.
- The October 7 review, subsequent implementation records and current recovery,
  architecture and Kopia documents establish scope. Intermediate descriptions of
  blanket write denial, restic, snapshot pins, and nightly scheduling are superseded.

## Compact feature inventory

This identifies delivered code, relevant test coverage and outstanding acceptance;
listing a feature here is not certification of all its failure modes.

| Feature | Implementation and tests | Compatibility / acceptance still needed |
| --- | --- | --- |
| Bootstrap and authenticated ingress | `apps/ingress`, installer boundary stamping; `test_ingress_security`, `test_install` | Preserve denying bootstrap and apply changed boundary before Serve publication; fresh VM interrupted upgrade and real Authentik/client acceptance remain |
| Mutation identity and batches | `jobs`, `install_batches`, API security and migration 0003; `test_mutation_identity`, batch/API tests | Subject/namespace/resource/action/full request binding; preserve request HMAC key and legacy conflict behavior |
| Truthful usability | `status/checks`, `sign_in`, service observations, API and dashboard; `test_status_checks`, route/dashboard tests | 90-second observations, two misses/120-second grace, 24-hour sign-in; cached success fix included; real private route/device checks remain |
| Tool approvals and policy | `gateway_authority`, tool gateway, `mcp_gateway`, console, approval API/UI; authority/security/HTTP/limits/container tests | Operator-only shared service credentials are intentional, not personal data delegation; exact stored inputs, durable claim and fail-closed publication remain the contract; real two-account/client acceptance pending |
| Durable revocation | `access`, identity, people, worker; `test_access_revocation` | Local denial then retryable external cleanup; native app sessions remain a documented residual risk |
| Commands and exclusivity | `process`, `job_processes`, worker flock and resource locks; process/lock tests | Paused-worker and surviving-container VM drills pending; later feature callers and nested locks need continued review |
| Operation journal and deployment identity | `operations`, maintenance, releases, settings, deployment bundles; journal/settings/deployment tests | Full release identity now survives journal serialization; legacy ambiguous versions must stay refused; exhaustive rename/full-disk/rollback matrix still pending |
| Kopia engine and platform backup | `backups`, `platform_backup`; backup/platform/Kopia integration tests | Digest-pinned 0.23.1, local filesystem repo, old restic untouched, no pins, keep-everything plus Mu3Lab retention; weekly verification is a 5% sample |
| Recovery material, schedule and browser | recovery sheet, vault sync, schedule, Kopia UI/routes; related API/UI tests | Administrator-only recovery material; Sunday 03:30 local default; no off-device copy promise; live vault/printing/read-only browser acceptance remains |
| New-host rebuild | `rebuild`, installer restore step; rebuild and real Kopia simulated new-root test | Key round-trip fixed, but incompatible snapshot selection, old recovery replay and cleanup ownership remain blockers below |
| Self-update | `self_update`, standalone `update_guard`; self-update/guard tests | Unsafe checkpoint rollback now refuses recovery; actual service-replacement and published-release acceptance pending |
| Calendar | local/Nextcloud calendar, explicit sync API/UI; calendar tests | GET remains read-only; legacy ETag and conditional resolution fixes included; ambiguous PUT/retry content comparison still needs review |
| Local AI | reviewed models, Ollama, routing/provider reconciliation, follower rules/UI; local-model tests | Preserve earlier embedding model/index restrictions; invalid local settings now fail closed; follower failure retains old models; incomplete switching remains a blocker below |
| Tailnet relay and API trust | host-gateway mappings, TLS pass-through relay and ordering; relay tests/docs | Compose validator now supplies relay configuration. API/worker share user, secrets and Docker authority; enforced isolation is intentionally deferred |

## Confirmed defects fixed in this checkpoint

All severities below describe the original branch. These fixes do not remove the
unresolved findings in the next section.

| Severity | Trigger and consequence | Fix / evidence |
| --- | --- | --- |
| P1 | A journal round-trip dropped compute variant and definition, changing a release's immutable identity during recovery | Serialize and load the full `Release` record; regression compares IDs, not dataclass equality |
| P1 | A platform or direct pruning caller could delete a snapshot referenced by unfinished maintenance | Enforce protected snapshot filtering inside `backups.prune`, covering every caller; disposable real journal regression |
| P1 | Crash after repository creation but before policy publication caused later snapshots to use automatic expiry | Reassert Mu3Lab's policy before snapshots, including reconnects; failure must raise rather than proceed |
| P1 | Platform export omitted `mu3lab.request-key`; restored fingerprinted requests could no longer replay | Export and restore the original private key; regression restores the actual database/key and successfully replays its request |
| P1 | Crash after staged-to-live rename but before journal success left retry unable to finish; an originally absent root could remain after undo | Recognize completed rename from staged state and on-disk evidence; regressions inject journal failure for present and absent roots and inspect restored contents |
| P1 | Platform crash recovery restarted in stop order, discarded failed recovery markers, and omitted an app whose stop partially failed | Restart in dependency order, retain failed marker, include attempted stops; restart/marker regressions |
| P1 | A cached route success could clear newer failure or remain valid indefinitely while observations advanced | Require success newer than failure and within evidence age; regression plus updated realistic timestamp fixtures |
| P1 | Legacy calendar edits/deletions lacked ETags and issued unconditional mutations | Keep edits as conflicts and retain legacy deletions without upstream writes; conditional conflict resolution also refuses concurrent changes and preserves the local edit |
| P1 | Invalid saved local-only model settings fell back to outside inference | Fail closed to local-only; invalid chat selection removes chat routes, never introduces an external route; routing/label regression |
| P1 | Schema rollback restored completed jobs as queued and erased jobs accepted after the checkpoint; failed service stops were ignored | Refuse rollback unless services stop and checkpoint restoration cannot discard changed records; preserve both databases and report needs-attention. SQLite regressions verify completed and newly accepted jobs survive. Unknown schema changes fail closed |
| P1 | The guard could verify the old worker before restart, lose an alternate runtime root, or fail to launch while the update claimed success | Wait for the old worker to exit; explicitly propagate the runtime root; report failed guard launch and withhold automatic restart |
| P1 | Failed/busy follower updates were logged and then their old models could be deleted | Retain all models and fail the job with the affected apps; regression checks no deletion after a failed follower restart |
| P2 | Same-second job timestamps did not define claim order; schedule test also mistook reverse chronological display order for execution order | Add durable SQLite row-order tie-breaker; test actual worker claims in platform-first order |
| P2 | CI Compose validation lacked required `MU3LAB_RELAY_CONFIG` | Supply disposable path; reproduced 37/38 failure, then verified 38/38 |
| P2 | Image-fetch tests used a shared `/tmp/mu3lab.db` via cache-parent lookup | Put cache beneath each test's private temporary root; both failing layer-reuse tests now pass |

`tests/test_review_recovery.py` contains five filesystem/database regressions that
failed on the supplied tip and passed with the recovery fixes. Other added
regressions live beside the relevant feature tests. No assertion was relaxed to
accept unsafe behavior.

## Unresolved merge blockers

### P1 — Rebuild reactivates operations from the old machine

`ctl/rebuild.py:260–292` cancels jobs but leaves `operations` active. The worker
calls `maintenance.reconcile` (`ctl/lifecycle/maintenance.py:747`), which interprets
those newly cancelled jobs as orphaned operations and queues recovery. Old restore
plans refer to data/swap folders from the original machine, not the independently
restored new layout. Approval state and install batches also require explicit
rebuild disposition; cancelling jobs alone is not a replay barrier.

Disposable reproduction: begin a restore operation, advance it to `swap`, run
`prepare_database`, then call reconciliation. The original job is cancelled, but
one new recovery job is queued. Recovery state must be reconciled against the
chosen recovery set before any new-host job or approval can dispatch.

### P1 — Rebuild silently pairs incompatible platform and app snapshots

`ctl/rebuild.py:184–196` chooses each source independently and discards release
metadata. `restore` then places platform projects/settings/keys and the selected
app data without compatibility checks. A newer app snapshot after a release or
secret change can be paired with an older platform export; even a newer platform
export may be incompatible with an older app snapshot.

The selection reproduction accepts an old platform snapshot and a newer app
snapshot tagged with a different deployment. The current simulated new-root test
has only compatible fixtures and therefore passes. A recovery-set manifest or
another explicit compatibility proof must bind snapshots to recoverable settings,
secrets, releases and layout. Choosing snapshots merely by time is not such proof.

### P1 — Interrupted rebuild cleanup does not establish ownership

`ctl/rebuild.py:106–125,302–304` trusts the presence of a start marker, then removes
all data/project children before checking existing data. Files placed there after
an interrupted attempt are removed too; a corrupt marker still takes the cleanup
path. Source-path overlap and a changed source on retry need rejection before
cleanup. Cleanup needs an owned-path/attempt manifest or equivalent staging
boundary; the original empty-directory check does not prove current ownership.
This is a direct source finding; no destructive reproduction was run.

### P1 — AI switching can claim local privacy after failed rollback

`ctl/local_models.py:410–421` restores the earlier record before verifying rollback.
If a switch from local-only to outside providers partially succeeds and rollback
fails, settings again claim local-only while the running routing may still be
external. It reports that earlier settings were kept despite the failed undo.
The separate follower/model-deletion defect is fixed in this checkpoint.

The invalid-record fallback and follower retention are fixed here; failed routing
rollback is not. It needs durable applied/desired policy evidence and fail-closed
handling of unknown routing. This is a
source finding; real provider switching was deliberately not run on the household.

## Verification and remaining work

- Pinned-tooling `make verify`: the first fixed checkpoint passed 1,075 Python
  tests (10 intentionally skipped), 122 dashboard tests, lint, formatting, API
  schema, mypy and dashboard build. The final rerun is recorded in the PR evidence.
  Initial runs exposed the shared image-cache fixture and schedule display-order
  assumption; neither was dismissed as an environmental pass.
- Real Caddy integration: **2 passed** (`MU3LAB_TEST_CADDY=1`).
- Real Kopia integration: **3 passed** (`MU3LAB_KOPIA_INTEGRATION=1`), including
  staged restore/ownership/retention and the existing simulated new-root rebuild.
- Locally built gateway, production mount boundaries: **1 passed** using
  `MU3LAB_TEST_GATEWAY_IMAGE=mu3lab-test-review-gateway:967c4a4`.
- Rendered Compose: **38/38 passed** after the validator fix.
- Shell parsing and `git diff --check`: passed.
- Pinned Node dashboard `npm audit --audit-level=moderate`: **0 vulnerabilities**.
- GitHub CI must be checked on the exact proposed commit. The original tip's CI
  failed Compose validation before secret scanning and SBOM generation, so those
  skipped stages were not counted as passes.

The complete requested final-diff sign-off is **withheld**. Further adversarial
review remains necessary, including multi-root/interrupted undo at every boundary,
rebuild authority and batch sanitization, concurrent platform backup coherence,
ambiguous calendar PUT reconciliation, and partial AI changes. Passing this
checkpoint's tests cannot certify those paths.

Remaining release acceptance is distinct from these implementation blockers:
fresh/interrupted VM install and upgrade, real Authentik and native/mobile clients,
two-account operator/token checks, database-app update/restore interruption drills,
real vault/QR/printing behavior, new-host rehearsal, and published-release guard
survival/provenance. SFTP replication and the full second-machine rehearsal remain
explicitly deferred by the owner; operator shared credentials, native-session
revocation limits and shared API/worker privilege are accepted scope, not newly
invented merge requirements.

Local verification and reproduction logs are retained under
`/tmp/mu3lab-review-967c4a4/`; they contain only disposable runtime evidence.
The isolated branch preserves all 32 supplied commits. It is intentionally a
draft PR while the four P1 findings above remain; there is no merged commit and
no deployment or release. Remote main was fetched again and remained unchanged.
