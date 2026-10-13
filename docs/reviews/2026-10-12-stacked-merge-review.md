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
| New-host rebuild | `rebuild`, installer restore step; rebuild and real Kopia simulated new-root test | Key round-trip, matching snapshot selection, old recovery retirement and cleanup ownership fixed; second-machine acceptance remains |
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

## Follow-up: first three rebuild blockers fixed

The user authorized these three implementation fixes after their user/system
impact was explained. The following findings describe the original behavior;
they are now addressed in the isolated review checkout.

### P1 — Rebuild reactivated operations from the old machine

**Found by:** tracing `prepare_database` into the worker's maintenance
reconciliation, then reproducing recovery being queued after only the original
job was cancelled.

**Fix:** cancel active and needs-attention operations, remove old-host restore
plans, append terminal journal evidence, cancel unfinished install batches/items,
and remove old write confirmations. Restore a consistent gateway database with
its matching encryption key; discard raw project WAL/SHM files, revoke old
approvals/credentials, invalidate policy acknowledgement, and mark interrupted
dispatches as outcome-unknown. History remains available.

**User effect:** old work cannot silently resume on the new computer. Tool
connections need fresh credentials and new writes need fresh approval. An old
write whose outcome cannot be known is not automatically repeated.

**Evidence:** real SQLite journal/batch regressions verify maintenance queues no
recovery and unresolved operations no longer block new work. Authority tests
check approved and dispatching operations, credential regeneration, and the
actual export/restore key/database path including stale raw WAL removal.

### P1 — Rebuild silently paired incompatible platform and app snapshots

**Found by:** reviewing independent newest-by-time selection, which discarded
release metadata and never checked restored settings against app data.

**Fix:** app snapshots carry an opaque HMAC fingerprint of all deployed project
files (including release/Compose/env), canonical saved app secrets/settings, and
folder layout. Platform exports record expected contexts from their consistent
control database. Optional app mutation locks protect export alongside core
locks. Rebuild validates restored deployment/configuration and selects the
newest matching snapshot for every required app before placing its database or
app data. Shared/invalid layouts and incomplete or unproven sets are refused.

**User effect:** a newer incompatible snapshot is skipped for a compatible one;
if none exists, rebuild stops naming the app. Legacy backups remain manually
recoverable but cannot be used for automatic rebuild. Take fresh platform and
included-app backups without intervening deployment/settings changes. Optional
apps keep running during export; settings/deployment changes briefly wait.

**Evidence:** regressions cover a newer incompatible candidate, a missing app
proof, rotated secrets, and a legacy platform export. The real pinned Kopia
new-root rebuild exercises newly written tags, exported contexts and restored
app data/ownership.

### P1 — Interrupted rebuild cleanup did not establish ownership

**Found by:** reading the marker-driven blanket `rm` before the existing-data
check; corrupt markers also reached that destructive path.

**Fix:** an atomic ownership record names only registered destination roots.
Completed inventories hash contents and metadata as root. Retry validates all
paths before any deletion and deletes only exact unchanged owned paths. It
rejects corrupt/legacy records, unsafe paths, changed source path/repository
identity, source overlap, changed contents, and a crash before inventory was
completed. Repository copying no longer blindly removes existing targets.

**User effect:** added/unrelated files survive. Uncertain interrupted attempts
stop for manual recovery instead of deleting data. Hashing adds disk-read time
on large rebuilds. Owner-supplied repositories are never registered for cleanup.

**Evidence:** tests cover unrelated data, changed owned contents, corrupt/legacy
records, unrecorded partial restores, source overlap, changed sources and
replaced repositories. Real container-owned files exercise inventory hashing,
refusal after a foreign file is added, and exact cleanup preserving unrelated
data and the source repository. A wrong-password retry is checked against the
real pinned Kopia image.

Focused validation for this follow-up: 115 unit tests across rebuild, platform
backup, backups, authority, review recovery and uninstall; mypy checks 162 source
files. Real Kopia checks use disposable roots and the production-path rejection
wrapper. These checks do not constitute second-machine acceptance or validation
of the unresolved findings below.

## Unresolved merge blockers

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

### P1 — Replacement worker performs mutations before settling surviving commands

`ctl/worker.py:174–188` invokes `platform_backup.recover` and starts background
work before `JobProcesses.settle` at line 246. MCP reconciliation at line 228
also runs before this gate. This protects job claiming, but not the earlier
mutations.

If a worker dies during a platform snapshot while the Docker CLI or Kopia
container survives, the replacement worker acquires the released flock and
immediately restarts the core apps listed in the crash marker.
`platform_backup.recover` calls `_start_all`, then `maintenance.start_app`; none
of these checks the surviving backup container. Kopia's survivor gate only
protects later Kopia commands. The supposedly stopped database may therefore
resume writing while its earlier snapshot still reads it. MCP reconciliation
can likewise change a connector while an earlier command is still active.
Settle both tracked command groups and surviving backup containers before any
recovery or background mutation starts. This finding follows the source call
path; no crash drill was run in this review pass.

### P1 — Chat synchronization can grant access again after completed revocation

`ctl/lobehub_ops.py:118–121` reads the operator set, then separately grants each
subject. `access._gateway` (`ctl/access.py:185–190`) revokes gateway authority
without taking the chat synchronization lock; only the later chat-detachment
handler takes that lock.

A valid ordering is: synchronization reads a subject as an operator; demotion
records the local hold and completes gateway revocation; synchronization then
uses its earlier operator set to call `grant_subject`. `desired_assistants`
subsequently calls `operator_token`, which can mint a new credential. The gateway
revocation task is already marked done. Removing assistants later does not
revoke that new bearer token. The gateway only consults its own authority
store, so the local hold in the control database does not deny this credential.
It remains usable until a later synchronization revokes it or subject authority
expires (600 seconds).

Granting authority and recording/revoking a hold need a shared serialization or
revision contract that rejects stale grants. An extra unsynchronized membership
read is insufficient. This is a source-level concurrency finding, not a claim
that a live demotion was exercised.

### P1 — Calendar retry treats resource existence as proof of matching content

`ctl/local_calendar.py:398–410` accepts HTTP 412 as a completed upload for every
locally created event, then deletes its local record. However, `update_event`
(lines 274–287) preserves that event's href and `from_nextcloud=False` when it is
edited.

If Nextcloud accepts an initial PUT but its response is lost, the local event
remains. The user can edit it before retrying. The retry sends the new contents
with `If-None-Match: *`; Nextcloud returns 412 because the old contents already
exist. Mu3Lab drops the newer local edit and reports it moved. A unique UUID
proves resource identity, not content equality. A retry must fetch/compare the
remote event or preserve the edit as a conflict before deleting local state.
This finding follows the ambiguous-response and local-edit paths directly;
no DAV requests were sent.

### P1 — Process cleanup forgets surviving descendants after the leader exits

`ctl/process.py:160–173` returns immediately when the subprocess leader has
exited, and also returns as soon as that leader exits after SIGTERM. A descendant
can still belong to the same process group in either case. `run` then removes
the tracking row based only on `proc.poll()` (`ctl/process.py:298–300`);
`JobProcesses.settle` also checks only the recorded leader PID.

For a command that forks, an exited leader with a descendant holding the output
pipe reaches the deadline, but `stop` sends no group signal. A descendant that
ignores SIGTERM also survives when its leader exits promptly. The worker can
then release the resource lock and run another job while that descendant still
changes files. Cleanup and survivor tracking must establish process-group
quiescence rather than infer it from the leader. The existing grandchild test
keeps the leader alive and does not cover these paths. No process reproduction
was launched in this source-review pass.

## Other confirmed source findings

- **P2 — Completely skipped scheduled runs disappear from status.**
  `ctl/backup_schedule.py:228` only constructs `last_run` when there are queued
  jobs. When the platform job is busy and all app jobs are busy or blocked,
  `queue` records skip reasons but status returns `last_run=None`, hiding why
  the scheduled protection did not run. Project a recorded run even when its
  job list is empty.
- **P2 — Rebuild resume permanently forgets apps that were busy.**
  `ctl/rebuild.py:393–395` catches enqueue conflicts, then unconditionally writes
  `apps_started=True`. Every later `resume` returns immediately, so an app that
  had a conflicting queued job never receives its promised start after that
  conflict clears. Record per-app completion or retain a pending resume until
  all eligible apps have been queued.

## Source review coverage in the follow-up pass

This pass read implementation and callers without running tests or integration
commands. It examined the following contracts in the current source at
`8f316d87529c19ea61099c9c4b1fdbb323b563c1`:

- **A:** bootstrap/authenticated Caddy ordering; subject/request fingerprinting,
  replay checks and nested SQLite transaction reuse for job preparation and
  desired state; installation-batch mutation order; status evidence aging and
  dashboard access projection.
- **B:** worker flock and resource-lock lifetime; tracked processes and group
  cleanup; startup/background ordering; operation recovery and attempts;
  deployment identity, canonical settings, bundle capture/restore; staged
  data swaps and undo before application startup.
- **C1/C2:** direct and wrapped writes, console and approval routes, stored
  argument binding, policy/schema/credential revision checks, transactional
  dispatch claims, non-retry of writes, terminal outcomes; revocation callers
  and chat synchronization. The shared operator service-account decision is
  preserved.
- **C3:** Kopia survivor handling and repository locks, platform export and
  crash restart, retention, rebuild selection/cleanup/sanitization/resume;
  schedule deduplication and status; administrator recovery-sheet handling and
  read-only Kopia UI mounts.
- **C4/C5/supporting:** standalone update recovery, local-AI save/apply/undo and
  UI labels, calendar conditional mutations and ambiguous retry handling,
  tailnet relay listener/source configuration and installer ordering.

No additional defect was confirmed in this pass in the direct/wrapped approval
claim or mutation-replay transactions. That is a scoped observation, not a
certificate for all concurrency, protocol-limit, or release-acceptance cases.
The five remaining P1 findings above prevent approval of the complete stack;
the two P2 findings also need correction. The three new-host rebuild findings
were addressed in the authorized implementation follow-up documented above.

## Prior checkpoint verification and remaining work

- Pinned-tooling `make verify`: the final fixed checkpoint passed 1,081 Python
  tests (10 intentionally skipped), 122 dashboard tests, lint, formatting, API
  schema, mypy and dashboard build. This is prior verification, not work
  performed during the source-only follow-up.
  Initial runs exposed the shared image-cache fixture and schedule display-order
  assumption; neither was dismissed as an environmental pass.
- Real Caddy integration: **2 passed** (`MU3LAB_TEST_CADDY=1`).
- Real Kopia integration: **3 passed** (`MU3LAB_KOPIA_INTEGRATION=1`), including
  staged restore/ownership/retention and the existing simulated new-root rebuild.
- Locally built gateway, production mount boundaries: **1 passed** using
  `MU3LAB_TEST_GATEWAY_IMAGE=mu3lab-test-review-gateway:8f316d8`.
- Rendered Compose: **38/38 passed** after the validator fix.
- Shell parsing and `git diff --check`: passed.
- Pinned Node dashboard `npm audit --audit-level=moderate`: **0 vulnerabilities**.
- GitHub CI must be checked on the exact proposed commit. The original tip's CI
  failed Compose validation before secret scanning and SBOM generation, so those
  skipped stages were not counted as passes.

The complete requested final-diff sign-off is **withheld**. Further adversarial
review remains necessary, including multi-root/interrupted undo at every boundary,
additional rebuild interruption boundaries and concurrent platform backup coherence,
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
draft PR while the P1 findings above remain; there is no merged commit and
no deployment or release. Remote main was fetched again and remained unchanged.
