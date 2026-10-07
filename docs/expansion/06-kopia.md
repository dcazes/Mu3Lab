# 06. Kopia replaces restic (Phase 2)

## 1. Goal

Replace restic with **Kopia** in every backup path, keeping every guarantee
the current code gives. Add what the owner asked for:

1. Kopia's **web interface**, behind Authentik, for operators.
2. **Scheduled** nightly backups (a deferred item from earlier sessions).
3. An optional **copy to another computer** over the tailnet (SFTP).

Default destination stays local: `/srv/mu3lab/backups/`.

## 2. What exists today (read before changing anything)

`ctl/backups.py` (323 lines) runs restic in a pinned, network-less container:

| Function | Purpose | Callers |
|---|---|---|
| `readiness(paths, retention)` | Repository and verification status for the dashboard | `ctl/status/host.py`, `ctl/api/routes/system.py`, `ctl/api/routes/services.py` |
| `ensure_repository(log, paths)` | Create the repository once | `snapshot` |
| `sources(data_directories, paths)` | Map data folders to `/data/<folder>` mounts | `snapshot` |
| `snapshot(service_id, dirs, reason, log, version=…)` | Back up a **stopped** app; integrity check; prune (`--keep-last 3` + 7 daily / 4 weekly / 12 monthly) | `ctl/lifecycle/maintenance.py` (manual, pre-update, pre-restore) |
| `snapshots(service_id, …)` | List an app's snapshots, newest first | `maintenance.py`, `api/routes/services.py` |
| `restore(service_id, snapshot_id, dirs, log)` | Make each stopped data folder match the snapshot exactly (`--delete`), each folder in its own run that can see only that folder | `maintenance.py` |

Key properties to **keep**:

- P1. The repository password is created once, kept in the encrypted
  `SecretStore` (`platform/backup-password`), and written to a 0600 temp file
  for each run. It is never on a command line or in a log.
- P2. Backups run in a pinned container with `--network none`, seeing only
  the repository, the password and that app's folders.
- P3. One stable identity (restic `--hostname mu3lab`), so history does not
  split per run.
- P4. Snapshots are tagged `app:<id>`, `reason:<manual|pre-update|pre-restore>`
  and `version:<v>`. Listing filters by app.
- P5. Restore validates the snapshot belongs to the app **and** its folder
  layout matches. It can never touch anything outside the app's folders. The
  result is an **exact** match (extra files removed).
- P6. Pruning failure leaves extra snapshots, never missing ones.
- P7. `maintenance.py` takes a safety snapshot before restore and rolls back
  on failure; a failed pre-update backup aborts the update.
- P8. Restoring the pre-update backup also restores the previous release
  (`releases.json`). This is in `maintenance.py`/`app_releases.py`; do not
  break it.

Read `tests/test_backups.py` and `tests/test_maintenance.py` fully. They are
your specification.

## 3. Design

```text
                       /srv/mu3lab/backups/kopia/      (filesystem repository)
                                 ▲
  worker job ──► docker run kopia (pinned, --network none, root) ──┘
     │            mounts: repo rw, config rw, password env-file ro,
     │                    app folders ro (snapshot) or one folder rw (restore)
     │
     └──► (optional) docker run kopia repository sync-to sftp  ──► other computer over tailnet
                                                    (network: default bridge, egress only)

  kopia-ui container (long-running, read-only connection) ◄── Caddy ◄── Authentik gate (operators)
```

- **Repository path:** `/srv/mu3lab/backups/kopia/` (new subfolder, so an old
  restic repository at `/srv/mu3lab/backups/` stays untouched; see §8). Add
  `RuntimePaths.kopia_repository`. VERIFY how `paths.backups` is defined in
  `ctl/runtime.py`.
- **Config and cache:** `/srv/mu3lab/state/kopia/` (`repository.config`,
  `cache/`), mode 0700. Never inside the repository.
- **Password:** reuse `_repository_password()` unchanged (same secret name).
  Pass it as `KOPIA_PASSWORD` through `docker run --env-file <0600 temp file>`
  (VERIFY Kopia reads `KOPIA_PASSWORD`; it does in current docs). Never use
  `--password` on the command line.
- **Identity:** create and connect with
  `--override-username=mu3lab --override-hostname=mu3lab` (VERIFY flag names
  in `kopia repository create filesystem --help` for the pinned version).
  Every snapshot then belongs to `mu3lab@mu3lab`.
- **One source per app:** mount each data folder at
  `/data/<app-id>/<folder-name>` (read-only) and snapshot the source
  `/data/<app-id>`. Kopia's source is one directory. This keeps one snapshot
  per app, like restic.
- **Tags:** `--tags app:<id> --tags reason:<r> --tags version:<v>` (VERIFY the
  tag syntax; Kopia uses `key:value` tags). Also set `--description`.
- **Container:** `kopia/kopia:<version>@sha256:<digest>` (VERIFY the official
  image name on Docker Hub and the latest stable version). Pin it like
  `RESTIC_IMAGE`; add it to whatever list pre-pulls core images (VERIFY,
  search for `RESTIC_IMAGE` usages in installer and release tooling) and to
  `release-images.json` generation if that covers non-compose images.
- **Root inside the container:** needed to read app files owned by other UIDs
  and to restore ownership. Still `--network none`, `--cap-drop ALL
  --cap-add DAC_READ_SEARCH --cap-add CHOWN --cap-add FOWNER --cap-add
  DAC_OVERRIDE` (measure what restore needs; start from restic's flags).
- **Compression:** global policy `--compression=zstd` set at repository
  creation (VERIFY the value name).
- **Retention:** per-source policy set at first snapshot of each app:
  `--keep-latest 3 --keep-daily 7 --keep-weekly 4 --keep-monthly 12
  --keep-annual 0` (keeps P6 semantics: Kopia applies retention when
  snapshots are created and during maintenance). Keep the `Retention`
  dataclass and add `kopia_args()`.
- **Protect important snapshots:** Kopia retention does not look at tags. Use
  **pins** (`kopia snapshot pin <id> --add=<name>`, VERIFY) so retention never
  deletes: the newest `pre-update` snapshot per app, and the newest
  `pre-restore` snapshot per app. When a newer one is taken, unpin the older
  one. Test: 20 manual snapshots in a row never delete the pinned pre-update.
- **Maintenance:** set `kopia maintenance set --owner=mu3lab@mu3lab` at
  creation. Run quick maintenance after each scheduled run and full
  maintenance weekly (§6). The UI container never owns maintenance.
- **Concurrency:** all Mu3Lab Kopia commands take one file lock
  (`ctl.secret_file.locked(paths.state / "kopia.lock")`), so two jobs never
  run Kopia at once. The read-only UI does not take the lock.

### 3.1 Restore without `--delete`

Kopia restores **into** a directory and does not remove extra files (VERIFY
whether the pinned version has a `--delete-extra`-style option; if it does,
prefer it and skip the swap). Keep P5 with a staged swap:

1. For folder `D` (for example `/srv/mu3lab/data/immich/postgres`), create
   `D.mu3lab-restoring` next to it (same filesystem, so rename is atomic).
2. Restore the snapshot's subtree `<snapshot>/<folder-name>` into it (VERIFY
   how to restore a subdirectory: `kopia snapshot restore <id>/<sub> <target>`
   or `kopia restore <root-object-id>/<sub>`). Mount **only** the staging
   folder read-write. Preserve owners, permissions and times
   (`--no-ignore-permission-errors`, VERIFY the flags).
3. Rename `D` → `D.mu3lab-previous`, then staging → `D`.
4. Remove `D.mu3lab-previous` only after **all** folders of the app swapped
   successfully. On any failure, swap back every folder already swapped,
   remove staging folders, and raise `BackupError` (the existing rollback in
   `maintenance.py` then runs).
5. Startup cleanup: if the worker finds leftover `*.mu3lab-restoring` or
   `*.mu3lab-previous` folders (a crash mid-restore), it reports them and
   refuses to start that app until an operator chooses "finish restore" or
   "undo restore". Do not guess. Add an API action for each, with tests.

## 4. Task 2-1: Kopia engine (replace restic)

1. Rewrite `ctl/backups.py` internals. **Keep every public function name,
   signature, return shape and error type**, so `maintenance.py`, the API
   routes and the dashboard need no change. Return the same keys
   (`snapshot_id`, `files`, `bytes`; list items with `id`, `short_id`,
   `time`, `reason`, `version`, `paths`). Map Kopia JSON
   (`kopia snapshot list --json`, VERIFY its fields: `id`, `startTime`,
   `tags`, `stats`, `rootEntry`) to these.
2. `_SNAPSHOT_ID` regex: Kopia snapshot IDs are hex. VERIFY the length and
   adapt the pattern; keep strict validation.
3. `paths` in list items: report the folder names under the app source, as
   `/data/<folder>` (the same shape `restore()` validates today), so layout
   checks keep working. Compute them from the snapshot's root directory
   listing (`kopia ls <id>`) or from a tag written at snapshot time
   (`folders:<a,b>`). Choose the tag; it avoids an extra call. VERIFY tag
   value character limits.
4. Integrity check after snapshot: `kopia snapshot verify <id>
   --verify-files-percent=<n>` (VERIFY flags), keeping the existing
   `records.put("backup", "verification", …)` record.
5. `readiness()`: same keys; add `engine: "kopia"`, repository size
   (`kopia repository status --json`; VERIFY), last snapshot time per app.
6. **Manifest backup hints** (new optional field):

   ```yaml
   backup:
     exclude: ["cache/**", "thumbs/**"]   # globs relative to each data folder
     quiet_hours_only: false             # true: schedule only, never during the day (HA)
   ```

   Rendered as Kopia ignore rules on the app's source policy
   (`kopia policy set /data/<id> --add-ignore <glob>`; VERIFY). Media folders
   are never under `/data` and so never included unless opted in (§4.7).
7. **Media opt-in:** for a media folder with `backup: true`, or the owner's
   toggle in the dashboard (stored per app in `records`), snapshot a second
   source `/media/<name>`, tagged `app:<id>` and `kind:media`, on the
   **schedule only**. Not before updates: media is not touched by updates.
   Restore of media is a separate, explicit action.
8. Delete restic code, `RESTIC_IMAGE`, and restic-specific tests in the same
   commits that replace them. Keep `HOST`-equivalent identity constants.
9. Tests: rewrite `tests/test_backups.py` around a fake runner (the existing
   tests mock the subprocess; keep that seam). Add an **opt-in integration
   test** (skipped by default, like the existing four) that runs the real
   pinned Kopia image against a temp directory: create, snapshot, list,
   restore with an extra file removed, a pinned snapshot surviving retention.
   Run it once and record the output in the task notes.

Acceptance:
- [ ] All existing maintenance tests pass unchanged (or changed only where
      they assert restic specifics, with the reason in the commit message).
- [ ] P1–P8 each covered by at least one test.
- [ ] VM: back up Mealie, change a recipe, restore, recipe is back; update
      then roll back via the pre-update backup restores the old release.

## 5. Task 2-2: Kopia web interface

1. New app folder `apps/kopia/` (it is an app with a route; id `kopia`):
   `tier: core`, `group: infrastructure`, capability `backup_server`.
   VERIFY whether core apps are installed by core setup automatically; the
   UI should be **installed by default** but stoppable.
2. Service: `kopia server start --address=0.0.0.0:51515 --insecure
   --server-username=mu3lab-ui --server-password=$KOPIA_UI_PASSWORD
   --refresh-interval=5m` (VERIFY each flag). `--insecure` means plain HTTP;
   it is acceptable only because the port is bound to loopback and TLS ends
   at Tailscale/Caddy.
3. Connection: the container uses its **own** config file
   (`/srv/mu3lab/state/kopia-ui/`), connected **read-only** to the same
   repository (`kopia repository connect filesystem --path=/repo --readonly
   --override-username=mu3lab --override-hostname=mu3lab`; VERIFY that
   `--readonly` exists for the pinned version; if it does not, STOP and ask:
   a read-write UI can change policies Mu3Lab owns).
4. Mounts: repository **read-only**; `/srv/mu3lab/backups/restores` read-write
   (where people can restore individual files through the UI); **no** data or
   media folders. Restoring over live app data is done only through the
   Mu3Lab dashboard (which stops the app first).
5. Route: `access: gate`, `audience: operators`. Instead of making operators
   type the Kopia UI password, add a generic route option
   `upstream_basic_auth: {user_env, password_env}`. Caddy adds
   `header_up Authorization "Basic <base64>"` **after** the Authentik gate.
   The rendered value comes from the app's `.env`; keep the generated Caddy
   file mode 0600 (VERIFY the current mode). A local process hitting
   `127.0.0.1:51515` directly still needs the password. Also save the
   credential to operators' vaults (`save_login_to_vault`) for recovery.
6. Dashboard: Settings → System → Backups gets an **Open Kopia** button and a
   short note: "Browse every backup and restore single files into
   `/srv/mu3lab/backups/restores`. To roll back a whole app, use the app's
   Backups card."
7. Tests: route rendering with upstream basic auth; manifest validation.

## 6. Task 2-3: Scheduled backups

1. Settings → System → Backups: **Nightly backups** on/off (default **on**),
   time (default 03:30 host local time), and the list of apps included
   (default: every installed optional app; operators can untick).
   Stored server-side in `records`, never in browser storage.
2. The worker schedules one durable job per night. It runs apps
   **sequentially**: stop → snapshot (reason `scheduled`) → start, reusing the
   maintenance code path. Add `scheduled` to `REASONS`.
3. Skip an app (and record why) if it has an active job, is already stopped
   by the owner (back it up without starting it), or is mid-install.
4. A missed run (machine off at 03:30) runs **10 minutes after the worker
   next starts**, unless the last success is less than 20 hours old.
5. After all apps: quick maintenance. Sundays: full maintenance plus
   `snapshot verify --verify-files-percent=5` (VERIFY flag).
6. Failures: per-app status ("Last nightly backup failed: <reason>"). Home
   shows a warning if any included app has no successful backup for 3 days.
7. Apps marked `backup.quiet_hours_only` (Home Assistant) are backed up only
   by the schedule. Manual backups still allowed, but with a confirmation:
   "Home Assistant automations pause for about N seconds."
8. Tests: schedule computation (DST changes, missed runs), skip rules,
   sequential execution, warning threshold. `make api-schema` for new
   settings.

## 7. Task 2-4: Copy to another computer (SFTP over the tailnet)

1. Settings → System → Backups → **Copy to another computer**: host (a
   tailnet name or IP), SSH port (22), user, folder path. **Test connection**
   button.
2. Mu3Lab generates an **ed25519 key pair** for this purpose only (stored
   encrypted in `SecretStore`, scope `platform`, name `backup-sftp-key`). The
   page shows the public key and copy-paste instructions to add it to
   `~/.ssh/authorized_keys` on the target for Linux, macOS and Windows
   (OpenSSH Server), and a note for NAS devices (Synology/TrueNAS: enable
   SFTP and add the key to the user). Recommend a dedicated user on the
   target.
3. **Host key pinning:** the first test connection reads the target's host
   key (`ssh-keyscan` in a container), shows its SHA256 fingerprint, and asks
   the operator to confirm it matches the target (instructions to read it on
   the target: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`). Store the
   confirmed key and pass it as Kopia's known-hosts data. Never use
   "accept any host key".
4. After each nightly run (and on demand): `kopia repository sync-to sftp
   --path=<folder> --host=<host> --port=<port> --username=<user>
   --keyfile=/run/key --known-hosts=/run/known_hosts` (VERIFY flags,
   including whether `--delete` is needed to mirror deletions). This
   container needs network access (default bridge) and **only** the
   repository read-only plus the key and known-hosts files.
5. **Deletions:** mirroring deletions means a destroyed local repository
   could propagate. Default: **do not** propagate deletions (`--delete` off).
   The target grows by what retention removes locally. Show the target's
   size estimate. Weekly full maintenance does not touch the copy. Document
   that the copy can be pruned manually. If Kopia requires `--delete` for
   correctness, STOP and ask.
6. Failures: target offline → status "Copy failed: <host> not reachable",
   retried next night. A warning after 3 consecutive failed nights.
7. **Disaster recovery guide** (new doc `docs/backup-recovery.md`, written for
   a beginner): reinstall Mu3Lab on new hardware, get the backup password
   from the vault (§9 E1), copy the repository back (or connect to the copy),
   restore each app. Test this guide in the VM end to end (VM A backs up and
   copies to VM B; destroy VM A; new VM C restores from VM B).

## 8. Old restic backups

Per [../rebuild-handoff.md](../rebuild-handoff.md) decision 1, there is no
migration. If `/srv/mu3lab/backups/config` (a restic repository) exists at
upgrade time:

- Never modify or delete it.
- Show a one-time dashboard notice: "Older backups made before this update
  are kept in /srv/mu3lab/backups. Mu3Lab now uses Kopia and does not list
  them. See docs/backup-recovery.md to restore from them by hand." Include
  the manual restic restore command in that doc, using the restic image
  that was pinned before this change (record its digest in the doc).
- The owner may delete the folder themselves after confirming the new
  backups work.

## 9. Edge cases

| # | Case | Handling |
|---|---|---|
| E1 | Backup password lost (server dies) | At repository creation, queue the password as a vault item for **every operator** ("Mu3Lab backup password", notes explaining it opens backups, the copy included). Without it, backups are unreadable; say so in the item notes and in the recovery doc. |
| E2 | Disk full during snapshot | Kopia fails. Report `backup_disk_full` with free space and repository size. Never mark success. The app is restarted. |
| E3 | Repository corrupted | Weekly verify reports it. Dashboard error with next steps (recovery doc). Never auto-repair. |
| E4 | Crash mid-restore | §3.1 step 5. |
| E5 | Two jobs at once | File lock. The second waits up to 30 min, then fails with `backup_busy`. |
| E6 | Kopia version update changes repository format | Approve Kopia updates like any image. **Never** run `kopia repository upgrade` automatically. If a new version requires it, STOP and ask. |
| E7 | App data has sockets, FIFOs, huge sparse files | Kopia skips special files (VERIFY, log them). Exclude known caches via `backup.exclude`. |
| E8 | Clock jumps (DST, NTP) | Schedule uses local wall time with DST handling. Snapshot times are UTC. |
| E9 | SFTP target fills up | The sync fails; status says so; local backups continue. |
| E10 | Read-only UI shows stale data | `--refresh-interval`; the page notes "refreshes every 5 minutes". |
| E11 | Restore to an app installed at a newer version | Existing behaviour (P8) puts back the old release. Test once with a DB app. |
| E12 | Owner restores a **media** snapshot | Separate confirm dialog naming the folder and size; staged swap like data. |
