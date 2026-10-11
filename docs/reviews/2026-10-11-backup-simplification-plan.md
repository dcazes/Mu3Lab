# Backup simplification plan — 2026-10-11

Status: **approved by the owner and built on 2026-10-11** (branch `simplify/kopia-backups`). Code and tests are done; the live drill (section 8, step 7) is still to do. See [What was built](#9-what-was-built) for the differences from this plan.

Context: the [Milestone B review](2026-10-11-milestone-b-review.md) and the [Milestone C review](2026-10-11-milestone-c-review.md) found that the serious backup bugs live in Mu3Lab's own code around Kopia, not in Kopia. About 3,000 Python lines, 640 dashboard lines and 2,600 test lines support backup and restore. The owner asked for three things:

1. **No crash-resume for restores.** A failed or interrupted restore puts the original data back and shows a warning with **Retry**.
2. **One backup system** that does both the backing up and the scheduling.
3. **A clear list of what it replaces.**

## 1. The decision: Kopia does everything it can

Keep **Kopia** as the only backup system. Promote the existing Kopia server (today the read-only "Kopia backups" browser, `apps/kopia-ui`) to Mu3Lab's single backup service. It then does:

| Job | Done by Kopia today? | After this plan |
|---|---|---|
| Store, encrypt, deduplicate, compress | Yes | Yes (unchanged) |
| **Schedule** backups | No, Mu3Lab's `backup_schedule.py` | **Kopia policy** (`--snapshot-time-crontab`, `--run-missed`) |
| **Retention** (which old backups to delete) | No, Kopia's is switched off and rewritten in `backups.expired/prune` | **Kopia policy** (`--keep-latest/daily/weekly/monthly`) |
| Repository maintenance (reclaim space) | Mu3Lab runs it weekly | **Kopia server's automatic maintenance** |
| Stop an app before its backup, start it after | No, Mu3Lab's job loop | **Kopia before/after actions** that ask Mu3Lab to stop or start that one app |
| Browse and restore single files | Yes (read-only UI) | Yes (same UI, admins only) |
| Restore a **whole app** safely | Kopia restores into a folder; Mu3Lab does the swap | Kopia restores into a folder; Mu3Lab does a **simple** swap (section 3) |

Verified on 2026-10-11 against the pinned image `kopia/kopia:0.23.1`:
- `policy set` offers `--snapshot-time`, `--snapshot-time-crontab`, `--run-missed`, `--keep-latest/daily/weekly/monthly/annual`, `--before/after-snapshot-root-action`, `--action-command-mode` and `--action-command-timeout`.
- `server start` offers `--enable-actions`, which actions require.
- The image contains `curl`, so an action can call Mu3Lab without extra tools.

**restic is removed completely:**
- the `restic` host package the installer still installs (`ctl/install.py` `BASE_PACKAGES`);
- the "legacy restic repository" reporting in `backups.readiness` and `models.py`;
- the restic restore instructions in `docs/backup-recovery.md`.

The old restic folder on existing machines is left on disk, untouched, and mentioned once in the release notes.

### Why not switch to another tool (Backrest/restic, borgmatic)?

Both would also schedule and prune. But Kopia is already integrated, already pinned and already serves the backup browser. Switching would mean a fourth engine change, a new repository format and a new browser. Kopia's server covers the owner's "backup and scheduling in one system" requirement.

## 2. How a backup runs after the change

```
Kopia server (always running, loopback only, admin-only web page through Authentik)
  │  policy: "30 3 * * 0" (weekly, Sunday 03:30), run missed, keep 10 latest / 7 daily / 4 weekly / 12 monthly
  │
  ├─ time for source /data/mealie
  │     before-action ──curl──► Mu3Lab: "stop mealie for its backup"   (waits until stopped)
  │     Kopia snapshots /data/mealie (read-only mount)
  │     after-action  ──curl──► Mu3Lab: "start mealie again"           (health-checked)
  │
  └─ the same for every app, for each core app (Authentik, Vaultwarden …),
     and for the "mu3lab" source, whose before-action makes the consistent copy of
     Mu3Lab's database and keys (today's export_control_plane)
```

- **"Back up now"** in the dashboard asks the Kopia server to snapshot that source now, so it runs the same actions as a scheduled run.
- **Stop and start** go through **one** Mu3Lab function per app, the same one the installer uses. This removes the class of bug behind C-C1 (Authentik not restarting).
- **The hook endpoint** is a new, narrow API, `POST /api/v1/backup-hooks/{source}/{before|after}`. It accepts only a dedicated hook token held by the Kopia container, and can only stop, start or export a named installed source. The worker does the stop or start (R18: the API process never runs Docker); the endpoint waits for that job, within Kopia's action timeout.
- **Fail-safe:**
  - `--action-command-mode=essential`: if the before-action fails, the backup is skipped, so a running database is never copied half-written.
  - A small worker check restarts any app a backup stopped more than 30 minutes ago. This replaces `platform_backup.recover` and its marker file, and covers a Kopia crash between the two actions.

## 3. How a restore runs after the change (no crash-resume)

```
1. Kopia restores the chosen backup into  .mealie.restoring   (app keeps running; failure here = nothing changed)
2. Stop the app
3. Rename  mealie            → .mealie.original
   Rename  .mealie.restoring → mealie
4. Start the app and wait for its health check
5a. Healthy  → delete .mealie.original → "Restored the backup from …"
5b. Anything fails in 2–4 → put .mealie.original back, start the app
                         → "The restore did not work, so your data was put back as it was." [Retry]
```

- **No operation journal, no resume, no automatic retry.** The folder names are the only state.
- **After a crash or power cut mid-restore:**
  - The worker does nothing automatic.
  - Any app with a leftover `.original` folder is shown as **Needs attention: a restore was interrupted**, with two buttons:
    - **Put my original data back:** rename back and start the app. This is the default and safe choice.
    - **Retry restore.**
  - Nothing else may start that app until the owner picks one. The existing `recovery_required` block shrinks to "an `.original` folder exists".
- **No extra pre-restore backup.** The original folder *is* the safety copy until the app is verified healthy. This removes the same-day safety snapshots that pushed out manual backups (B-M1).
- **Release switch.** A backup made from another app version still switches the app back to that version's images. The version comes from the snapshot's tag, so no journal is needed and B-H1 (the journal dropping the variant and definition) no longer applies to restores.

## 4. What it replaces

Lines are non-blank Python lines unless stated. Estimates are ±20%.

| Today | Replaced by | Removed | Added |
|---|---|---|---|
| `ctl/backup_schedule.py` (scheduler, DST maths, missed-run settling, queueing a job per app) | Kopia policy `--snapshot-time-crontab` + `--run-missed`; a ~50-line translator from the dashboard setting to the policy. The Home warning reads Kopia's last snapshot times. | ~210 | ~50 |
| `backups.expired`, `prune`, `Retention`, `KEEP_LATEST`, `protected()` exemptions | Kopia retention policy | ~110 | ~10 |
| `backups.start_verification`, `data_check_due`, `maintain` | Kopia server's automatic maintenance (the weekly 5% verify is the open question in section 7) | ~50 | 0 |
| `backups._wait_for_survivors`, survivor-container stop logic | Not needed: scheduled backups run inside the Kopia server, not one-shot containers | ~40 | 0 |
| `ctl/platform_backup.py` stop/backup/start loop, marker file, `recover()` | Kopia actions per source + hook endpoint; keeps `export_control_plane` (consistent database copy) | ~170 | ~40 |
| `ctl/operations.py` (operation journal: phases, steps, attempts, orphan detection, needs-attention) | The `.original` folder rule in section 3 | ~275 | ~30 |
| `ctl/lifecycle/maintenance.py` backup/restore/recover paths (`_restore`, `_recover`, `_abandon`, `_put_back*`, `_save_before`, `reconcile`, resume branches) | A new ~150-line `ctl/lifecycle/restore.py` with the section 3 flow | ~380 | ~150 |
| `backups.plan_restore/stage/swap/undo/leftovers` (journaled multi-state swap) | Same idea, two renames each way, no saved plan | ~100 | ~40 |
| `ctl/job_processes.py` (fencing leftover commands of a dead worker) | Not needed without resume; the one-worker lock in `resource_locks` stays | ~114 | 0 |
| Worker loops: orphan reconcile, backup schedule tick, verification thread | One "restart apps a backup left stopped" check | ~40 | ~15 |
| API: `recovery_required` / `recovery_pending` journal checks in `routes/services.py` | One check: an `.original` folder exists | ~35 | ~10 |
| restic leftovers (host package, legacy reporting, docs) | Nothing | ~15 + docs | 0 |
| New hook endpoint + Kopia server settings (`apps/kopia-ui` becomes the backup service, read-write, `--enable-actions`, host network on loopback, hook token) | — | 0 | ~150 |
| **Python total** | | **~1,540** | **~495** |
| Dashboard: Backup schedule card (150), recovery buttons in BackupsCard, platform card (82) | Schedule card writes Kopia policy; one interrupted-restore prompt | ~200 TSX | ~80 TSX |
| Tests: `test_operation_journal.py` (301), most of `test_backup_schedule.py` (305) and `test_platform_backup.py` (288), journal parts of `test_maintenance.py`, job-process parts of `test_resource_locks.py` | New tests for the restore flow, hook endpoint and policy translator | ~1,100 | ~400 |

**Net: about 1,050 fewer Python lines (~35% of backup code), about 120 fewer dashboard lines, about 700 fewer test lines.**

### What stays

- `export_control_plane`: a consistent SQLite copy and keys. Kopia cannot do this alone.
- Per-app status ("checked / stale / missing"), now read from Kopia's snapshot list.
- The recovery sheet and vault copy.
- `./install.sh --restore` (`rebuild.py`), unchanged in purpose, using the same simple restore per app.
- The one-worker lock and per-app locks.
- Deployment bundles and release IDs, which app **updates** still use (section 6).

## 5. Bugs this removes

| Finding | How |
|---|---|
| **C-C1** (CRITICAL): Authentik not restarted after the self-backup | Every stop and start goes through one shared start function; the hook path gets a real-Docker test |
| **B-H1** (HIGH): the journal drops the release variant and definition | No journal for restores. Updates: see section 6. |
| **B-M1** (MEDIUM): retention deletes manual backups | No more same-day safety snapshots; Kopia retention with `keep-latest 10` |
| **B-M2** (MEDIUM): restore always pulls images | Restore pulls only when the backup's version differs *and* the image is not already present |
| B-L1: "restore tested" without a start | Recorded only after step 4's health check |
| B-L2: narrow swap crash windows | Two renames each way; leftover folders always mean "offer to put the original back" |

## 6. App updates (same rule, phase 2)

Updates also use the journal today. Once restores no longer use it, the same model can apply to updates:
1. Take a normal backup and capture the deployment bundle.
2. Apply the new release.
3. Check health.
4. On failure, put the bundle back and run the section 3 restore of that backup.
5. On a crash, a small `update-in-progress` file per app produces **Needs attention: an update was interrupted**, with **Undo update** and **Keep new version**.

That removes `operations.py` entirely and with it B-H1. Recommended, but it is a separate step for the owner to approve.

## 7. Trade-offs and open questions

| Topic | Trade-off | Recommendation |
|---|---|---|
| Kopia server becomes read-write | Today's browser cannot change backups. Read-write lets an administrator delete a backup in Kopia's UI. | Accept: it is admin-only behind Authentik (verified), and Kopia's UI is the single tool. Alternative: a second read-only server process, which adds complexity. |
| Kopia needs to reach Mu3Lab | The server moves to host networking (loopback) and holds a hook token that can stop or start apps | Accept: the token cannot do anything else, and Kopia already holds the repository password |
| No automatic resume | After a power cut mid-restore, the owner must click one button; nothing finishes by itself | Owner's stated preference |
| **Weekly 5% data verify** | Kopia's server has no verify schedule; dropping it loses early detection of silent disk damage | **Owner decides:** keep a tiny weekly verify job (~30 lines), or rely on Kopia verifying data as it reads it during restores |
| Scheduled backups depend on the Kopia server running | If it is stopped, nothing runs | Its health shows on Home; `--run-missed` catches up after downtime |
| One schedule for every app | Today one schedule covers all apps; per-app times are possible in Kopia but not offered | Keep one household schedule |
| Things to confirm in a disposable test before building | (1) the environment variables Kopia passes to actions; (2) that an after-action runs even when the snapshot fails; (3) the server API call for "snapshot now" | Prove all three with the opt-in real-Kopia test first |

## 8. Order of work

Each step is shippable and reversible on its own.

1. **Fix C-C1 now**, independently: one start function for Authentik. Until then, switch the schedule off.
2. **Prove the three Kopia unknowns** in the real-image test (section 7).
3. **Restore rewrite (section 3)**, with tests for: failure at each step puts the original back; an interrupted restore shows the prompt; Retry works; a stopped app is never marked "restore tested".
4. **Kopia server as the backup service:**
   - hook endpoint;
   - policies (schedule and retention);
   - migrate the owner's schedule setting into the policy;
   - delete `backup_schedule.py`, custom retention and the verification thread;
   - remove restic.
5. **Self-backup through actions:** delete `platform_backup`'s loop and marker; add the "restart apps a backup left stopped" check.
6. **Phase 2:** the updates rule (section 6), then delete `operations.py` and `job_processes.py`.
7. **Live drill on this host:**
   - one scheduled run, with Authentik's stop and start verified;
   - one restore;
   - one restore with the worker killed between the renames, then "Put my original data back".

## 9. What was built

Built on 2026-10-11, all in one go: steps 1 to 6 of section 8. The owner said "go ahead" without choosing on the open questions, so the recommended defaults were taken.

| Open question | Taken |
|---|---|
| Weekly 5% data check | **Kept**, as the existing small weekly thread. Kopia's server has no verify schedule. |
| Kopia read-write | **Accepted.** The backup browser is now the backup service. |
| Phase 2 (updates) | **Built too.** `operations.py` is deleted. |

Proved first, in throwaway tests against the pinned `kopia/kopia:0.23.1` image:

- **What Kopia hands its actions:** `KOPIA_ACTION`, `KOPIA_SOURCE_PATH` and `KOPIA_SNAPSHOT_PATH`. `KOPIA_SNAPSHOT_ID` is **not** the backup's id, so Mu3Lab notes each app's release just before its backup and matches it to the snapshot by time.
- **When an essential before-action fails,** Kopia skips that backup and does not run the after-action. When the backup itself fails after the before-action, the worker's check starts the app again (about 3 minutes).
- **Actions also have to be allowed on the connection** (`repository connect --enable-actions`), not only on the server.
- **"Back up now"** is `POST /api/v1/control/trigger-snapshot`. It only works for a source the server knows, so Mu3Lab sets a policy on every source first.
- **The server backs up one source at a time,** so each app stops only for its own backup.
- **Its schedule runs in this computer's time zone** once `/etc/localtime` is mounted.
- **The ownership Mu3Lab sets is kept without a container hostname,** even on the host network.

What differs from the plan:

- **Size.** Measured as code lines in `ctl/` (no docstrings or comments): 7,955 before and 7,615 after, so **about 340 fewer, not about 1,050.**
  - Removed: `operations.py` (243), `backup_schedule.py` (236), most of `maintenance.py` (619 → 83), half of `platform_backup.py` (227 → 89), and Mu3Lab's own retention and staged swap in `backups.py` (686 → 488).
  - Added: `backup_service.py` (449, keeping the server in step), `restore.py` (234) and `update.py` (189).
  - The estimate missed two things: tracking which folders the server can see (a bind mount follows the folder, so a restore's swap needs a server restart), and the updates rewrite.
  - Tests are about 300 lines shorter.
- **Kept, unlike the plan:**
  - `job_processes.py`, because it protects every job (installs too), not only restores;
  - the one-off Kopia container survivor wait, because restores and an update's own backup still use one-off containers.
- **A failed restore's Retry** reuses the existing job retry: `POST /api/v1/jobs/{id}/retry`.
- **An interrupted restore leaves one note** (`ctl/lifecycle/in_progress.py`) next to the `.original` folders. It records which folders and which deployment copy to put back, and does nothing on its own.
- **A second bug of the C-C1 kind was found and fixed:**
  - Restarting Vaultwarden outside the installer dropped its private HTTPS address (`DOMAIN`). The live Vaultwarden container was running without it, after the review's platform backup restarted it.
  - Fixed by `ctl/lifecycle/compose_settings.py`, the one start for both apps.
