# Milestone C3, step 1: Kopia replaces restic — 2026-10-08

This records the first step of C3 in the [project review](2026-10-07-project-review.md): replacing the backup engine, which is task 2-1 of the [Kopia plan](../expansion/06-kopia.md). R14 itself stays **in progress**. Backing up Mu3Lab itself, the recovery sheet, nightly backups, the SFTP copy and the Kopia web interface come next.

Branch: `fix/review-milestone-c`, on top of all of Milestones A and B. Owner: Claude Code, authorized by the owner on 2026-10-08.

## Owner decisions

- **Switch to Kopia now**, before the rest of C3 is built on the backup engine (2026-10-08). The owner was shown that the switch is contained (about 190 engine lines plus test fakes) and that the B guarantees are engine-independent.
- **No side-by-side engines.** The old restic repository stays untouched for manual restores (expansion decision 2.3, no migration).
- **Pulling the official Kopia image** to verify behaviour was approved.
- **Recorded for later C3 steps:** a printable recovery sheet plus a vault copy, and a brief nightly stop for core apps.

## What was verified against the real image

Image: `docker.io/kopia/kopia:0.23.1@sha256:89fd95ee2942880ca00eae964266958a394421ddbdf69bca62e38afc55f5900e` (built 2026-06-16; the `0.23.1` tag and `latest` had the same digest on 2026-10-08).

| Fact | Consequence |
|---|---|
| Kopia applies a source's retention policy **every time it takes a snapshot** (by default it kept 10 latest, 48 hourly, 7 daily …) | Mu3Lab sets the global policy to keep everything and applies retention itself (`expired`/`prune`: latest 3, then one per day ×7, week ×4, month ×12). This keeps B1's rule: no pruning while an unfinished operation needs a backup. |
| **Pinning a snapshot rewrites its manifest and changes its ID** | No pins (the old plan assumed them). Snapshot IDs stay stable in the journal, in `release:` history and in the dashboard. |
| `snapshot restore <id>/<folder> <dir>` gives `<dir>` the folder's own owner and mode, and keeps every file's owner (uid 999, mode 700 checked) | Staging restores straight into the sibling staging folder, which is then renamed into place. Restoring under an extra parent would have left a root-only parent the worker cannot move out of. |
| `snapshot restore --delete-extra` exists | Not needed: the staged swap from R13 already gives an exact result, and keeps the previous folder until the app is healthy. |
| With `KOPIA_PASSWORD` in the environment, Kopia writes no password file | The password goes through a private 0600 `--env-file`, never the command line. Create/connect also pass `--no-persist-credentials`. |
| By default Kopia skips folders marked as caches (`CACHEDIR.TAG`) | Turned off (`--ignore-cache-dirs=false`), so a restore is exact. |
| The image is Debian-based (`rm`, `sh` present) | It also serves as the root `rm` for restore leftovers, so no second image is needed. |

## What changed

Only the engine section of [`ctl/backups.py`](../../ctl/backups.py) and its tests changed. Every public function keeps its name, arguments and return shape, so the journal, locks, release IDs, settings store, deployment bundles, staged restore and per-app status from Milestone B are untouched.

- **Commands.** `_kopia` replaces `_restic`. It uses the same networkless, named, labelled container (`mu3lab.role=backup`), the same repository lock, the same wait for surviving containers, and the same stop on timeout.
- **Locations.**
  - The repository is at `/srv/mu3lab/backups/kopia`.
  - The configuration and cache are at `/srv/mu3lab/state/kopia`.
  - On a new machine or after lost state, `_connect` reconnects to an existing repository; it is never recreated.
- **Snapshots.**
  - One source per app, `/data/<app>`, with each folder mounted read-only at `/data/<app>/<folder>`.
  - Tags: `app`, `reason`, `version`, `release` and `folders`. The `folders` tag lets listings report each backup's folder set for R13's completeness check.
  - A snapshot that skipped unreadable files is not accepted as a backup.
- **Checks.** `check()` runs `snapshot verify` for structure, and `--verify-files-percent` for the weekly data read-back (5%).
- **Space.** `maintain()` runs full maintenance after the weekly check to reclaim the space of pruned backups.
- **Status and the old repository.**
  - `readiness()` reports `engine: kopia` and the legacy restic folder, if present. The dashboard's Security & backups page tells the owner it is kept.
  - The missing-password guard now looks at the Kopia repository.
- **Documentation.** New [docs/backup-recovery.md](../backup-recovery.md): where things are, a manual Kopia restore, and a manual restore from the old restic repository using its previously pinned image.

## Tests

- **`tests/test_backups.py`**, rewritten around a fake Kopia runner:
  - folders are mounted read-only, with tags and networking off;
  - the password reaches Kopia only through the env-file;
  - unreadable files fail the backup;
  - a failed verify is not reported as verified;
  - a new repository gets Mu3Lab's identity and keep-everything policy;
  - a repository is reconnected, not recreated;
  - restores are staged into a sibling folder;
  - other apps' and other layouts' backups are refused;
  - listing parses Kopia's multi-line JSON after progress output;
  - surviving or timed-out containers;
  - Mu3Lab's retention (twenty a day keeps three, unreadable times are never deleted, only that app's expired IDs are deleted);
  - all the existing R13 staging cases.
- **`tests/test_kopia_integration.py`** (opt-in, `MU3LAB_KOPIA_INTEGRATION=1`), run once on 2026-10-08 against the real image. It passed in 8.3 s:
  - back up an app whose folder contains a uid-999, mode-700 database folder;
  - list it;
  - change a file and add a stray one;
  - staged restore, after which the content is original, the stray file is gone, ownership is 999 and mode 700, and no leftovers remain;
  - five backups pruned to three;
  - a 100% data verify;
  - full maintenance;
  - per-app protection reports `data`.
- The full suite passes: 951 tests, with 8 skipped including the opt-in one.

## Remaining (rest of C3, and acceptance)

- **Next C3 steps:**
  - back up Mu3Lab itself (control database through SQLite's backup API, the secrets key, deployment bundles, and core apps with a brief nightly stop);
  - the printable recovery sheet plus vault copy;
  - nightly backups;
  - the SFTP copy;
  - the read-only Kopia web interface;
  - the new-machine rebuild guide and drill.
- **VM:** repeat the B interruption matrix on Kopia, and back up and restore a real database app (Mealie, Immich).
- **Retention timing:** buckets use UTC days. Mu3Lab's nightly schedule will run in host local time, and DST is handled there.

## Step 1b: backing up Mu3Lab itself (2026-10-08)

- **What app backups hold.** Each app's data only. Its code comes from the git checkout and its images from their registries; media is excluded unless opted in. Confirmed with the owner.
- **What git deliberately does not hold.** Each installation's own state, without which restored data cannot be opened. [`ctl/platform_backup.py`](../../ctl/platform_backup.py) backs it up as the Kopia source `mu3lab`:
  - the control database, copied with SQLite's backup API, so the copy is consistent while the dashboard keeps running;
  - the encryption key;
  - the deployment copies (R12);
  - the tool gateway's database;
  - the runtime projects.
- **Core apps.** Core apps with data (Authentik, Vaultwarden, LobeHub, LiteLLM, FreeLLMAPI, Firecrawl) are then stopped in reverse dependency order, backed up as their own sources, and started again in dependency order, as the owner chose.
  - A marker records what the job stopped, so a worker that dies mid-way restarts those apps when it comes back.
  - Apps the owner stopped are backed up but not started.
  - The manifest field `backup: {include: false, reason}` leaves out data that can be fetched again. Ollama's downloaded models use it, and the reason is shown.
- **Dashboard and API.**
  - `POST /api/v1/system/backups/platform` queues the job.
  - `GET /api/v1/system/backups/platform` reports each source's own evidence.
  - Security & backups shows **Mu3Lab itself** with a **Back up now** button for administrators.
- **Restoring this backup.** Restoring the `mu3lab` source belongs to the new-machine guide and drill (a later C3 step); it is intentionally not a dashboard button.
- **Tests.**
  - `tests/test_platform_backup.py`: order, left-out apps, a consistent copy of a live WAL database, stop and start order, a failure still restarts everything, interruption, crash recovery from the marker, apps the owner stopped, the API, and a check that no step falls back to the real runtime root.
  - The opt-in integration test now also backs up the `mu3lab` source against the real image. It passed.

### Incident: a debug run wrote to the owner's real installation

While I was investigating a listing failure, a debug script run **outside the test harness** called the platform backup. A bug then showed: `_back_up_platform` did not pass its `paths` argument to the final snapshot call, so that call used the real runtime root. At 23:12 local time on 2026-10-08 it:

- migrated `/srv/mu3lab/state/mu3lab.db` from schema 2 to 7;
- added a `platform/backup-password` secret and the `backup-apps/mu3lab` and `backup/verification` records, which describe a snapshot of a throwaway test folder;
- created a Kopia repository at `/srv/mu3lab/backups/kopia` and its configuration in `/srv/mu3lab/state/kopia`.

No app data, container or existing secret was touched. The bug is fixed, and a regression test asserts the paths are passed through. Separately, the running dashboard and worker were started from `/home/dak/Desktop/Mu3Lab-expansion`, a worktree removed during branch cleanup earlier on 2026-10-08, so those services cannot restart from that path. Remediation awaits the owner's decision.


## Fixes before the owner's reinstall (2026-10-09)

- **Apps could not reach sign-in from inside Docker.** Ubuntu upgraded Tailscale from 1.102.5 to 1.104.1 on 2026-10-08, and it took effect at that afternoon's reboot. Since then, Tailscale Serve's port 443 listener is bound to `tailscale0` (`100.x%tailscale0:443`). It accepts the host and host-network containers but refuses containers on Docker networks, so Open WebUI and LobeChat could not load Authentik's sign-in settings, and their sign-in checks failed. This was observed, not taken from Tailscale's release notes.
  - **The relay.** New `platform/tailnet-relay` is a pinned nginx running on the host network. It listens only on Docker's host address (the default bridge gateway), port 443, and admits private source addresses only. It passes each TLS connection through unchanged, so the certificate is still Tailscale's and the relay holds no keys.
  - **Installer and apps.** The installer starts the relay in a new step, `tailnet_relay`, right after Authentik's private address is published. Every app that signs in through Authentik maps `MU3LAB_TAILNET_HOST` (now rendered into every app's settings) to `host-gateway` on the services that use the sign-in settings. A test enforces this for future apps.
  - **Verified live.** A container on a Docker network received the real HTTPS answer through the relay, and the hardened relay (read-only, all capabilities dropped except binding and switching user) started and listened.
- **The core-app backup step now uses the job's runtime root** for its snapshots, check and pruning, not a default. This is the same class of bug as the incident above, and a regression test covers every backup call.

## Step 2: the recovery sheet and the vault copy (2026-10-09)

Owner decisions: the backup password goes on a printable sheet and into **every administrator's** vault. The schedule becomes a setting (default weekly, Sunday 03:30). The SFTP copy waits until the owner has a second computer. The read-only Kopia web interface is included.

- **Sheet.** `POST /api/v1/system/backups/recovery-sheet` is for administrators only and protected against cross-site requests. It returns the password and where the backups are, with `Cache-Control: no-store`. Each opening goes in the audit log, which never holds the password, and in a `backup/recovery-sheet` record, so the page can say whether anyone has opened it yet. The dashboard shows the password only after a confirmation, keeps it in memory only, and prints the sheet on its own. The password appears in eight groups of eight characters, as a QR code drawn in the browser, and with a check code: the first 8 hex characters of its SHA-256. [docs/backup-recovery.md](../backup-recovery.md) shows a shell command that computes the same code from a typed password; the command was verified against Mu3Lab's function.
- **Vault copy.** `vault_sync` adds **Mu3Lab backup password** to each administrator's *Mu3Lab app logins* collection. If no backup has needed the password yet, it is created first, so it is saved before it is needed. When the repository exists but its password is missing, nothing is saved.
- **Honesty about copies.** Until the SFTP step, the sheet and the guide both say the backups exist only on this server.
- **Tests:** the API (no-store, audited without the password, same password every time, status), household members refused, the check code, the vault copy for administrators only, and the dashboard (confirmation first, groups, check code, no copy warning).

## Step 3: scheduled backups as a setting (2026-10-09)

- **Setting.** `GET`/`PUT /api/v1/system/backups/schedule`, for administrators, with changes audited. The options are off, 6h, 12h, daily, or weekly with a day, plus a time in server-local time. The default is weekly, Sunday 03:30, as the owner chose. This replaces the plan's nightly default.
- **Runs are ordinary jobs.** [`ctl/backup_schedule.py`](../../ctl/backup_schedule.py) runs once a minute from the worker. It queues the platform backup (Mu3Lab plus core apps), then one `backup` job per installed catalog app, with the actor `schedule` and the idempotency key `schedule:<time>:<app>`, so a worker restart never queues a run twice. App backups from the schedule are tagged `reason:scheduled`; any other actor's stay `manual`. Apps that are installing or changing, that need attention, or that have another job running are skipped and listed.
- **Timing.**
  - Scheduled times are worked out in local wall time, so daylight-saving changes keep 03:30 at 03:30.
  - Only a time after both the last run and the last settings change counts.
  - A brand-new schedule starts from the first check, so this update never stops the core apps by surprise.
  - A missed run starts once the worker has been up for 10 minutes.
- **Dashboard.** A Backup schedule card shows the current schedule, the next run and the last run's outcome, including failures and skipped apps.
- **Tests:**
  - weekly, daily, 6h and 12h times;
  - US daylight-saving end (03:30 local moves from 07:30 to 08:30 UTC);
  - the floors from the last run and the last change;
  - validation;
  - queue order and idempotency;
  - skip reasons;
  - no immediate first run;
  - missed-run settling;
  - status from jobs;
  - the API (round trip, refusals, members refused);
  - the scheduled tag only for the schedule's own jobs;
  - the dashboard card.
- **Not done:** the plan's Home warning after 3 days without a backup. With a weekly default, a fixed 3-day threshold would be wrong; the card's last-run outcome covers this for now.

## Step 4: the read-only backup browser (2026-10-09)

- **App.** `apps/kopia-ui` is a core app, installed by core setup. It uses the same pinned image as Mu3Lab's backups, on the ports the expansion plan reserved: 51515, 8470 and 19487. It is named `kopia-ui` because "kopia" already names the engine in shared code.
  - The repository is mounted `:ro`, and Kopia both connects and serves `--readonly`.
  - Its connection settings live in memory only (tmpfs, a read-only root).
  - Only `backups/restores` is writable.
  - Kopia reads the repository password and its own login from the environment, so neither appears in the process list.
- **Rule `reads_backups`.** Hands the app the repository password and the backups folder. Before the app starts, it creates the repository (with Mu3Lab's policy) if no backup has been made yet, plus the restores folder.
- **Route option `upstream_basic_auth`.**
  - After Authentik admits an administrator, Caddy adds `Authorization: Basic …` from `{env.MU3LAB_UPSTREAM_AUTH_<APP>}`, replacing anything the visitor sent.
  - `routes.write_upstream_auth` writes those values into a 0600 file that Caddy loads as an optional `env_file`. All four places that start Caddy now go through `routes.ingress_env`.
  - An open route may not use the option, and the login is also saved to administrators' vaults as a fallback.
- **Verified against the real image, with throwaway repositories:**
  - `repository connect --readonly` works on a read-only mount, and a fingerprint of every repository file was identical before and after the interface ran;
  - 401 without the password or with a wrong one, 200 with the Caddy-style header;
  - listing works;
  - a policy change is refused;
  - a full restore brought back the content and its owner;
  - the real Compose file reached healthy, and its health check never sends the password.
- **Tests:**
  - the read-only mounts and flags;
  - no secret on the command line;
  - the same image as `KOPIA_IMAGE`;
  - left out of backups;
  - the route (golden file);
  - the 0600 login file;
  - the open-route guard;
  - every Caddy start going through the helper;
  - the rule;
  - the vault fallback, for administrators only.

## Step 5: rebuilding on a new computer (2026-10-09)

The owner chose a guided terminal command and declined a VM rehearsal for now. The rebuild is therefore **built and tested but not rehearsed**, and the guide says so.

- **`./install.sh --restore`.**
  - Before any step runs, the terminal asks for the copied backups folder (it accepts the `kopia` folder or its parent, with no Docker needed) and the password. It shows the recovery sheet's check code to compare.
  - No new account is created: the old vault and sign-in come back.
  - The installer step `restore_backup` runs after `runtime_layout` and before `root_env`, `service` and every app. It is skipped and hidden on an ordinary install.
- **[`ctl/rebuild.py`](../../ctl/rebuild.py), in order:**
  1. Refuses if this computer has Mu3Lab data (its database or key, app data, projects, or a backups folder other than the source).
  2. Writes a "started" marker, then copies the backups into place as root, unless they are already there.
  3. Opens them with the typed password. The Kopia runner takes it directly, so no encrypted store is created before the real one is restored.
  4. Restores the newest `mu3lab` backup: the control folder is staged, and the projects go straight into place.
  5. Hands files owned by the old user ID to this user; container-owned files keep their owners.
  6. Puts the database, key, deployments and the consistent gateway database in place, and checks that the restored store holds the typed password.
  7. Cancels jobs left active on the old computer, clears setup progress and old command records, and audits the rebuild.
  8. Restores each app's newest backup into new folders.
- **Interruption.** A plain `./install.sh` refuses to continue a half-finished rebuild. `--restore` clears what the rebuild had written and starts again. It removes the backups folder only if the rebuild made it as a copy; backups the owner placed are never deleted.
- **Apps.** Once a core-suite install succeeds after the rebuild, the worker queues a `start` job for each catalog app that was running, once.
- **Tests:**
  - 15 unit tests (`tests/test_rebuild.py`), against a simulated old computer with a running job, setup progress, secrets, deployments, projects and app data:
    - wrong password;
    - existing data;
    - backups with no Mu3Lab backup;
    - interruption and its cleanup;
    - repeat runs;
    - step order;
    - forgetting the password;
    - `install.sh` passing `--restore`;
    - apps resuming once.
  - The opt-in integration test against the real image backs up an "old computer" and rebuilds a second temporary root, including the real root-owned copy. It passed in 16.7 s. It checks secrets, data and the database folder's 999/700 owner and mode, cancelled jobs, file ownership, and the wrong password. Both roots had the same user ID, so the different-user-ID path is covered only by unit tests.
- **Not done:** a rehearsal on a second machine, including Tailscale under the same name, every app starting on restored data, and `uninstall.sh` afterwards.
