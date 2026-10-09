# Backups and recovery

Mu3Lab backs up each installed catalog app's data with [Kopia](https://kopia.io), into an encrypted repository on this server. You restore an app from its **Backups** card in the dashboard; Mu3Lab stops the app, unpacks the backup beside the current data, swaps it in, and puts the old data back if the app does not start.

This page is for the cases the dashboard cannot handle: restoring by hand, and backups made before Mu3Lab switched to Kopia. It also explains the recovery sheet, the one thing you must keep somewhere other than this server.

## When backups run

**Security & backups → Backup schedule** sets how often Mu3Lab backs up everything on its own: off, every 6 hours, every 12 hours, daily, or weekly (the default, Sunday at 03:30), in this server's local time. A run backs up Mu3Lab itself and its core apps first (sign-in, the password vault and chat pause for a few minutes), then each installed app one at a time. Apps that are being installed or changed, or whose last change needs attention, are skipped and listed. Mu3Lab also backs up an app before every update and restore, whatever the schedule.

If the server was off at the scheduled time, the run starts about ten minutes after it is back on. Changing the schedule never starts a backup straight away.

## The backup password and the recovery sheet

Every backup is encrypted with one password, which Mu3Lab created when it first needed it and never changes. Mu3Lab keeps it in its encrypted store on this server, so if the server is lost, that copy is lost too. Two other copies exist:

- **The recovery sheet.** An administrator opens it from **Security & backups → Recovery sheet** and prints it. It shows the password in eight groups of eight characters, as a QR code, and with a check code. Each opening is recorded in the audit log (who and when, never the password).
- **Each administrator's vault.** Mu3Lab saves it to every administrator's Bitwarden as **Mu3Lab backup password**, under *Mu3Lab app logins*. Bitwarden keeps a copy on each signed-in phone and computer, so it is still there when this server is off.

To confirm a password you typed back from paper is right, compare its check code with the one on the sheet:

```bash
read -rs PASSWORD && printf %s "$PASSWORD" | sha256sum | cut -c1-8 | tr a-f A-F | sed 's/..../&-/'; unset PASSWORD
```

Type the password without spaces and press Enter; nothing is shown while you type. It prints a code like `AB12-CD34`.

## Rebuilding on a new computer

> **Not yet complete.** The step-by-step rebuild, and a rehearsal of it on a test machine, are the last part of this milestone. Until then, this is what a rebuild needs; do not delete anything from the old server while it still runs.

You need: the backup password (sheet or vault), and a copy of `/srv/mu3lab/backups/kopia/` from the old server. Today the backups exist only on that server, so if its disk has failed they cannot be recovered. A copy to another computer is a later step.

## Where things are

| What | Where |
|---|---|
| Kopia repository (encrypted backups) | `/srv/mu3lab/backups/kopia/` |
| Kopia's connection settings and cache on this server | `/srv/mu3lab/state/kopia/` |
| Repository password | Mu3Lab's encrypted secret store (`platform` / `backup-password`), the printed recovery sheet, and each administrator's vault |
| Older restic backups (before the switch) | `/srv/mu3lab/backups/` (the `config`, `data`, `index`, `keys`, `locks` and `snapshots` entries) |

Kopia runs in a container pinned in [`ctl/backups.py`](../ctl/backups.py) (`KOPIA_IMAGE`, currently Kopia 0.23.1). Every snapshot belongs to `mu3lab@mu3lab` and is tagged `app:<id>`, `reason:<manual|scheduled|pre-update|pre-restore|platform>`, `version:<label>`, `release:<release id>` and `folders:<folder+folder>`.

## Restoring by hand with Kopia

Only do this when the dashboard cannot (for example, Mu3Lab itself is broken). Stop the app first: `docker compose --project-directory /srv/mu3lab/projects/<app> stop`.

```bash
# Run from a root shell. KOPIA_PASSWORD is the repository password.
docker run --rm -it --network none \
  -e KOPIA_PASSWORD -e KOPIA_CONFIG_PATH=/cfg/repository.config -e KOPIA_CACHE_DIRECTORY=/cfg/cache \
  -v /srv/mu3lab/backups/kopia:/repo -v /srv/mu3lab/state/kopia:/cfg \
  -v /srv/mu3lab/data:/data \
  docker.io/kopia/kopia:0.23.1 snapshot list --all
```

Then restore one folder into a new folder beside the live one, check it, and swap the two:

```bash
# … same docker run options as above, then:
snapshot restore <snapshot-id>/<folder> /data/.<folder>.manual-restore
```

Kopia gives the restored folder its original owner and permissions. Rename the live folder aside, rename the restored one into place, start the app, and delete the old folder only once the app works.

## Backups made before the switch (restic)

Mu3Lab used restic until 2026-10-08. That repository is **never modified or deleted** by Mu3Lab, and Mu3Lab no longer lists it. The dashboard's **Security & backups** page says so while it exists. Its password is the same repository password as above.

To restore from it by hand, use the restic image Mu3Lab pinned before the switch:

```bash
docker run --rm -it --network none -e RESTIC_PASSWORD \
  -v /srv/mu3lab/backups:/repo -v /srv/mu3lab/data:/data \
  docker.io/restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510 \
  -r /repo snapshots --tag app:<app>
# then: … restore <snapshot-id>:/data/<folder> --target /data/.<folder>.manual-restore
```

Once your Kopia backups are working, you may delete the restic entries in `/srv/mu3lab/backups/` yourself. Leave `backups/kopia/` alone.
