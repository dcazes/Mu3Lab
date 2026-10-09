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
