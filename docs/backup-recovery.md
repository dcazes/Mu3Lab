# Backups and recovery

Mu3Lab backs up its own state and each app's data with [Kopia](https://kopia.io), into an encrypted repository on this server. Kopia's own server (the **Kopia backups** app) does the whole job: it runs the schedule, keeps old backups by its retention rules and reclaims space. Mu3Lab only stops each app just before its backup and starts it again just after, so databases are copied consistently.

You restore an app from its **Backups** card in the dashboard. This page is for the cases the dashboard cannot handle, such as restoring by hand. It also explains the recovery sheet, the one thing you must keep somewhere other than this server.

## When backups run

**Security & backups → Backup schedule** sets how often Kopia backs up everything: off, every 6 hours, every 12 hours, daily, or weekly (the default, Sunday at 03:30), in this server's local time. Mu3Lab writes the choice into Kopia's policy within a minute.

A run backs up one source at a time: Mu3Lab's own settings and secrets, each core app (sign-in, the password vault, chat) and each installed app. Each app is stopped only for its own backup, usually a few seconds to a minute. An app that is busy with another task, or whose last restore or update was interrupted, is skipped that time and listed under the schedule. Mu3Lab also backs up an app itself just before every update.

If the server was off at the scheduled time, Kopia runs the missed backup once it is back on. **Back up now** on an app's Backups card, or under *Mu3Lab itself*, asks Kopia for the same backup straight away.

Kopia keeps the newest 10 backups of each source, plus one a day for a week, one a week for 4 weeks and one a month for a year. Once a week Mu3Lab also reads back 5% of the stored data to catch disk damage early.

Home tells administrators when backups fall behind: when Mu3Lab's own newest backup is older than the schedule allows (its interval plus 12 hours; a week when scheduled backups are off), when it has never been backed up, or when a backup did not finish last time. Mu3Lab's own backup is the measure because an app's backup cannot be restored onto a new computer without it.

## Restoring an app

On the app's **Backups** card, choose **Restore…** and type the app's name.

1. Kopia unpacks the backup beside the current data while the app keeps running.
2. The app stops, its current data folder is set aside, and the restored one takes its place. A backup from an older version also brings that version back.
3. The app starts and must pass its health check. Then the data set aside is deleted.

If anything in steps 2–3 fails, Mu3Lab puts the data from before back, starts the app, and the card says **The restore did not work, so the data was put back as it was**, with **Retry**.

If the computer loses power or crashes in the middle, nothing continues by itself. The app shows **Needs attention: a restore was interrupted**, and nothing else runs on it until you press **Put my original data back**. After that you can restore again. An interrupted update offers **Undo update** or, if the new version was already in place, **Keep new version**.

## Browsing backups and restoring single files

Administrators can open **Security & backups → Open backups** (Kopia's own web page, behind Authentik; Mu3Lab signs in to Kopia for you). It shows every backup. Restore a single file or folder into `/srv/mu3lab/backups/restores/`, then copy it where you need it. This is the same Kopia server that makes the backups, so an administrator can also delete a backup there; only do that on purpose. To roll back a whole app, use that app's **Backups** card instead; it stops the app first.

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
| Kopia's connection settings and cache on this server | `/srv/mu3lab/state/kopia/` (Mu3Lab's own commands) and `/srv/mu3lab/state/kopia-server/` (the backup service's cache) |
| Repository password | Mu3Lab's encrypted secret store (`platform` / `backup-password`), the printed recovery sheet, and each administrator's vault |
| Files restored from the backup browser | `/srv/mu3lab/backups/restores/` |

Kopia runs in a container pinned in [`ctl/backups.py`](../ctl/backups.py) (`KOPIA_IMAGE`, currently Kopia 0.23.1). Every snapshot belongs to `mu3lab@mu3lab`; an app's source is `/data/<app>`, with one folder per data folder, and Mu3Lab's own is `/data/mu3lab` (`control` and `projects`). Backups an update makes are tagged `app:<id>`, `reason:pre-update`, `version:<label>`, `release:<release id>` and `folders:<folder+folder>`. Older backups may carry other reasons (`manual`, `scheduled`, `pre-restore`, `platform`).

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
