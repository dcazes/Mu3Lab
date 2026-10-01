# App updates

Catalog apps (Mealie, Immich, Paperless-ngx and the rest) update only to versions the maintainer has tested with Mu3Lab. Core services (Authentik, Vaultwarden, LobeHub, LiteLLM, Ollama, Caddy) stay pinned and change only when Mu3Lab itself is updated.

## Updating Mu3Lab

**Settings → System → Mu3Lab updates** checks GitHub and lists the changes waiting. **Update Mu3Lab** does what `git pull` followed by `./install.sh` does, as long as no step needs your computer password. It installs Python packages, rebuilds the dashboard, updates the sign-in gate, downloads core images and restarts. Your apps keep running and stay on their versions. Apps with a newly approved version show **Update ready** afterwards.

The update checks first, using the installer's own checks from the new version. If anything would need administrator access (a new system package, say), it undoes the download before changing anything and tells you to run `git pull && ./install.sh` in a terminal. A copy with its own commits or edited files, such as a developer's, is always updated from the terminal.

## How it works

- **Approved version.** In the repository, `update.approved_version` in `services.yaml` and the digest-pinned images in `apps/<app>/docker-compose.yml` describe the one release Mu3Lab approves for each app. New installs get that release.
- **Installed version.** Each server records the release every app actually runs in `/srv/mu3lab/projects/<app>/docker-compose.digest.yml`, which pins each container to an image digest. Every Compose command uses it. Updating Mu3Lab, repairing an app or reinstalling over kept data never changes it.
- **Updating.** When the approved release is newer, the app shows **Update ready**. The update downloads the new images while the app keeps running, stops it, saves a backup, then starts the new release. If it doesn't come up healthy, the backup and the previous release are put back automatically.
- **Undoing.** Restoring the backup taken before an update also puts back the release it came from. `releases.json`, next to the digest file, records every release the server has run.
- **Supporting services.** A newer database or helper image under the same app version is offered as an update too, labelled as such.

## Approving a release (maintainer)

1. See which apps have a newer upstream release:

   ```bash
   make check-updates
   ```

2. Read the upstream release notes, especially database migrations and breaking changes. Then pin the release:

   ```bash
   make approve APP=mealie VERSION=v3.28.0
   ```

   This looks the release's images up in their registries (metadata only; nothing is downloaded), pins them by digest in the app's Compose file and sets `approved_version`. Your own test install is what downloads them. Only images whose tag contains the old version move; databases and helpers keep their pins. To move one of those, or for an app whose tags don't follow its versions (Firecrawl), name the images:

   ```bash
   make approve APP=firecrawl VERSION=v2.12.0 IMAGES="api=ghcr.io/firecrawl/firecrawl:2.12.1-production"
   ```

   Some projects re-publish one tag for every build. SurfSense, for example, publishes every self-hosted server build as `0.0.40`. Naming such a tag again (`IMAGES="backend=ghcr.io/modsetter/surfsense-backend:0.0.40 …"`) pins whatever it points to today. A `latest` tag is recorded by its digest alone.

   If the new release changes its Compose setup (new environment variables, services or volumes), edit `apps/<app>/docker-compose.yml` as well.

3. Test it on your own server: open the dashboard, go to **Apps → the app → Advanced → Updates** and run the update. Check that the app works, including sign-in and its chat connector.
4. Commit and push. Every server shows the change under **Mu3Lab updates**, and once it updates, the app shows **Update ready**.

Never raise a database's major version (for example PostgreSQL 16 to 17) this way. The new version can't read the old data files, so it needs its own migration.
