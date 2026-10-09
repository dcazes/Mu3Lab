# Backups and recovery

Mu3Lab backs up each installed catalog app's data with [Kopia](https://kopia.io), into an encrypted repository on this server. You restore an app from its **Backups** card in the dashboard; Mu3Lab stops the app, unpacks the backup beside the current data, swaps it in, and puts the old data back if the app does not start.

This page is for the cases the dashboard cannot handle: restoring by hand, and backups made before Mu3Lab switched to Kopia. A full guide to rebuilding Mu3Lab on a new computer from an off-site copy will be added here with the off-site copy itself.

## Where things are

| What | Where |
|---|---|
| Kopia repository (encrypted backups) | `/srv/mu3lab/backups/kopia/` |
| Kopia's connection settings and cache on this server | `/srv/mu3lab/state/kopia/` |
| Repository password | Mu3Lab's encrypted secret store (`platform` / `backup-password`) |
| Older restic backups (before the switch) | `/srv/mu3lab/backups/` (the `config`, `data`, `index`, `keys`, `locks` and `snapshots` entries) |

Kopia runs in a container pinned in [`ctl/backups.py`](../ctl/backups.py) (`KOPIA_IMAGE`, currently Kopia 0.23.1). Every snapshot belongs to `mu3lab@mu3lab` and is tagged `app:<id>`, `reason:<manual|pre-update|pre-restore>`, `version:<label>`, `release:<release id>` and `folders:<folder+folder>`.

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
