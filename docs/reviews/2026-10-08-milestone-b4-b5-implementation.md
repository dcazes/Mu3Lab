# Milestones B4 and B5 implementation (R12, R13, R15) — 2026-10-08

This records B4 and B5 of the [project review](2026-10-07-project-review.md), which finish the implementation work of Milestone B:

- **R12:** rollback returns the complete previous deployment.
- **R13:** restores are staged and checked before they are swapped in.
- **R15:** backup status is per app, and verification is truthful.

It builds on [B1](2026-10-08-milestone-b1-implementation.md) (the operation journal), [B2](2026-10-08-milestone-b2-implementation.md) (locks) and [B3](2026-10-08-milestone-b3-implementation.md) (release IDs and canonical settings).

Status: **implemented; release acceptance pending**. Nothing here was run against real Docker or a VM. Milestone B's exit requires the VM interruption matrix.

Branch: `fix/review-milestone-b`. Owner: Claude Code, authorized by the owner on 2026-10-08.

## R12 — complete deployment rollback

[`ctl/lifecycle/deployments.py`](../../ctl/lifecycle/deployments.py) captures a **deployment bundle** before an update, restore or backup changes anything:

- every regular file of the runtime project: Compose file, overrides, rendered templates, `.env` and the release record. These are copied to `state/deployments/<app>/<bundle>/files`, with 0700 folders at every level and 0600 files.
- the app's canonical settings, encrypted in the secret store.
- a manifest with each file's SHA-256, the release ID it ran, and the data folders that release uses.

`restore` verifies every hash first and refuses a damaged bundle before touching anything. It then makes the project exactly the captured one, removing files the newer deployment added, and puts the settings back. The journal records the bundle ID before any change. Bundles are pruned to the newest per release, plus any an unfinished operation names.

Changes to the update flow ([`ctl/lifecycle/maintenance.py`](../../ctl/lifecycle/maintenance.py)):

| Before | After |
|---|---|
| The new definition was rendered after the app stopped; a render error was logged and ignored | New phase `stage_definition`, before the app stops. The bundle is captured, the new release's files are rendered, and `docker compose config` must accept them. On any failure the bundle is put back, and the job fails with `definition_invalid` and "Nothing changed". The app was never stopped. |
| Rollback restored data and the image record only | Rollback restores the pre-update data (staged, R13) and then the whole bundle: files, settings and release record |
| Restoring an older release's backup kept the current Compose files | If a bundle of that exact release ID exists, its files and settings come back with the data; otherwise the job log says the current files are used |
| Re-wiring re-rendered the consumer from the current checkout | `render(copy_files=False)` refreshes only settings and templates; new deployment files arrive only through a backed-up update |

## R13 — staged restores and complete folder sets

New functions in [`ctl/backups.py`](../../ctl/backups.py):

- **`plan_restore`:** validates the restore.
  - Only plain top-level data-root folders are accepted; symlinks are refused.
  - It refuses a backup made for another layout.
  - It refuses a backup that **omits a folder the target deployment uses** and that holds data on this server. A missing folder that has no data here either is recorded as `not_in_backup`.
  - The target deployment's folder list comes from that release's bundle when switching releases. Otherwise it comes from the installed project, no longer from the checkout (`uninstall.data_directories`).
- **`stage`:** restores each folder into a sibling `.<name>.mu3lab-staged-<tag>` on the same filesystem, using `restic restore --include /data/<name> --verify`. Restoring the parent path keeps each folder's own owner and mode, so a database folder stays owned by its container user. Each staged folder is journaled.
  - In a restore this runs **while the app still runs**: a failure, including a full disk, changes nothing.
- **`swap`:** moves the live folder aside to `.<name>.mu3lab-previous-<tag>`, then moves the staged one in, journaling each folder. Each rename is atomic; the set is not, so the step is written to be safely repeated. Folder names and the journal together show how far it got. A swap stopped between its two renames finishes on retry.
- **`undo`:** swaps back, and can also be repeated safely.
- **`wipe`:** deletes leftovers, whose files belong to container users, in a networkless container. It deletes only dot-prefixed leftover folders.

The restore flow is: download → `stage_restore` (app running) → stop → safety backup → `swap` → start → delete the folders kept aside → housekeeping.

- **If the app does not start on the restored data**, the folders kept aside are swapped back and the job ends `restore_rolled_back`, "nothing was lost". Previously the app was left on data it could not start with.
- **A failed swap** is undone. If undoing fails, the safety backup is restored (also staged). If that fails too, the app needs attention (B1).
- **Rollback of an update** restores the pre-update backup the same staged way, under its own journaled plan.
- **Before recovery:** while an interrupted operation is waiting for its recovery job, the API refuses everything except recover and restore (`409 recovery_pending`), so nothing can start an app with some folders swapped.

## R15 — truthful backup status

- **Per-app evidence.**
  - `backup-apps/<id>` records each app's newest backup: snapshot, release ID, reason and time. `backups.protection(app)` reports one of four states:
    - `missing`;
    - `stale` (older than 8 days);
    - `unchecked` (no repository check has passed since it was saved);
    - `checked`, with verification `structure` or `data`.
  - It also records the last successful restore (`restore_tested_at`) and says plainly that copies are on this server only.
  - Backing up one app cannot mark another protected.
  - The service backups API returns `protection`, and the Backups card shows it, as a warning unless `checked`.
- **Repository checks.** `backups.check()` records the structural check time and result in `backup/repository`. The global readiness counts as verified only if a check passed within 8 days.
- **Scheduled deeper verification.** A worker thread runs `restic check --read-data-subset=5%` about weekly and records `data_checked_at`. It runs under the repository lock, outside any app's downtime.
- **Downtime.**
  - A manual backup no longer runs `restic check` or retention while the app is stopped; both run in `housekeeping` after it restarts.
  - Pre-update and pre-restore safety backups are still checked before anything changes, because the change depends on them.
- **Retention.** It still skips while any other unfinished operation names a backup (B1), and it is scoped to the app's own snapshots.
- **No silent new password.** An existing repository whose password is missing now fails with a clear error, instead of Mu3Lab generating a new password that could never open it.

Migration `0007_deployment_bundles.sql` adds `operations.bundle` and `operations.restore_plans`.

## Tests

- **`tests/test_deployments.py`:**
  - a bundle round-trip removes added files and restores settings;
  - a damaged bundle is refused before any change;
  - bundles are private at every level and kept per release;
  - a failed update restores the previous Compose file and settings;
  - an invalid new definition stops before the app does;
  - a healthy update keeps the new files;
  - restored data that does not start is swapped back;
  - per-app protection states;
  - a remembered restore;
  - the password guard.
- **`tests/test_backups.py`:**
  - staging next to the live folder with `--verify`, then swap and tidy;
  - a missing folder with data is refused, and one without data is recorded;
  - a symlinked folder is refused;
  - a failed staging leaves live data untouched;
  - an interrupted swap finishes, and undo can be repeated.
- **`tests/test_maintenance.py` / `tests/test_operation_journal.py`:** now run on a fake staged-restore layer. They cover interruption during swap and rollback, failed undo leading to needs attention and then resolved, and the `recovery_pending` API block.

## Remaining acceptance and limits

- **VM matrix (Milestone B exit):** kill the worker before and after every update and restore boundary, including between renames. Also test a full disk during staging, a multi-folder app with one folder absent, an image whose entrypoint changed, and a database major-version change. In every case, confirm the app never starts on a mixed data set and returns to the exact previous deployment.
- **Disk space:** staging needs free space equal to the restored data on the data filesystem. There is no pre-check yet; restic fails and nothing changes.
- **Older releases:** a backup from a release that predates bundles still restores with the current deployment files, and the job log says so.
- **Off-device copies:** none yet; that is R14 (Kopia/SFTP), Milestone C3.
- **Verification strength:** the deep check reads a 5% sample, not all stored data. The level is reported as `data`, not "fully verified".
- **Migration:** 0007 cannot be undone; older releases refuse this database.
