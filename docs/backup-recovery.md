# Backups and recovery

Mu3Lab backs up each installed catalog app's data with [Kopia](https://kopia.io), into an encrypted repository on this server. You restore an app from its **Backups** card in the dashboard; Mu3Lab stops the app, unpacks the backup beside the current data, swaps it in, and puts the old data back if the app does not start.

This page is for the cases the dashboard cannot handle: restoring by hand, and backups made before Mu3Lab switched to Kopia. It also explains the recovery sheet, the one thing you must keep somewhere other than this server.

## When backups run

**Security & backups → Backup schedule** sets how often Mu3Lab backs up everything on its own: off, every 6 hours, every 12 hours, daily, or weekly (the default, Sunday at 03:30), in this server's local time. A run backs up Mu3Lab itself and its core apps first (sign-in, the password vault and chat pause for a few minutes), then each installed app one at a time. Apps that are being installed or changed, or whose last change needs attention, are skipped and listed. Mu3Lab also backs up an app before every update and restore, whatever the schedule.

If the server was off at the scheduled time, the run starts about ten minutes after it is back on. Changing the schedule never starts a backup straight away.

## Browsing backups and restoring single files

Administrators can open **Security & backups → Open backups** (Kopia's own web page, behind Authentik; Mu3Lab signs in to Kopia for you). It shows every backup Mu3Lab has made. Restore a single file or folder into `/srv/mu3lab/backups/restores/`, then copy it where you need it. The page is read-only: the repository is mounted read-only and Kopia connects read-only, so it cannot change, add or remove a backup. To roll back a whole app, use that app's **Backups** card instead; it stops the app first.

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

> **Not yet rehearsed.** The rebuild is built and covered by tests, but it has not been run end to end on a second machine. Until it has, keep the old computer's disk if you still can.

You need:
- **The backup password**: from the recovery sheet, or *Mu3Lab backup password* in Bitwarden.
- **A copy of the old computer's backups**: the folder `/srv/mu3lab/backups/kopia`, for example on a USB drive. Today the backups exist only on the old computer, so if its disk has failed they cannot be recovered. A copy to another computer is a later step.
- **A fresh computer** that has never had Mu3Lab, running Ubuntu, Debian or a similar system. A rebuild never overwrites Mu3Lab data it finds.

Steps:

1. **Tailscale name.** In Tailscale's admin console, remove the old computer, and give the new one **the same name**. Your apps' private addresses then stay the same.
2. **Get Mu3Lab.** On the new computer, get the code as for a normal install:

   ```bash
   git clone https://github.com/dcazes/Mu3Lab.git ~/Mu3Lab
   ```

3. **Start the rebuild:**

   ```bash
   cd ~/Mu3Lab && ./install.sh --restore
   ```

   It asks for the folder with the copied backups, then the backup password, and shows the password's check code. Compare it with the code on your recovery sheet; they must match. You do **not** create a new account: your old one comes back.
4. **Let it run.** Setup installs the system software, then restores before anything else is created: Mu3Lab's settings, people and secrets, then sign-in, the password vault, chat and every app's data from each one's newest backup.
   - Work that was running on the old computer is cancelled.
   - Setup progress starts afresh, so the dashboard reports only what has worked on this computer.
   - The normal setup then continues. Once the core apps are up, Mu3Lab starts the apps that were running before.
5. **Check it.** Sign in to the dashboard with your old account, and open the password vault, chat and a few apps.

If the rebuild stops halfway, a plain `./install.sh` refuses to continue with half the old data. Run `./install.sh --restore` again: it removes what it had restored and starts over. It never removes backups you put in `/srv/mu3lab/backups/kopia` yourself.

## Where things are

| What | Where |
|---|---|
| Kopia repository (encrypted backups) | `/srv/mu3lab/backups/kopia/` |
| Kopia's connection settings and cache on this server | `/srv/mu3lab/state/kopia/` |
| Repository password | Mu3Lab's encrypted secret store (`platform` / `backup-password`), the printed recovery sheet, and each administrator's vault |
| Files restored from the backup browser | `/srv/mu3lab/backups/restores/` |
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
