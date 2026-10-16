# Mu3Lab

**A private home cloud that brings your household's apps, accounts, AI and day-to-day administration together on your own Linux computer.**

Mu3Lab installs and connects a curated collection of applications for files, photos, paperwork, meals, budgets, music, books, games and household life. Its dashboard gives you one place to open apps, manage people, see resource use, choose AI models, and handle installation, updates and recovery. Private remote access and shared sign-in are part of setup.

The goal is to reduce the work of running these applications together. Mu3Lab manages their deployment and integration; each application still provides its own screens, data and features. You choose what to install, supply your content, connect your devices and keep a separate copy of your backups.

## Three ways to read about the project

| Document | Audience and purpose |
| --- | --- |
| **[User guide: what to expect](docs/user-guide.md)** | Plain-language walkthrough of preparation, every setup stage, daily use, extra app requirements, backups and removal. Read this before installing. |
| **[Technical deep dive](docs/architecture.md)** | How the installer, app contracts, network, identities, worker, storage, dashboard, AI, calendars, backups and update mechanisms work, with links to implementation. |
| **This README** | Project overview, current scope and starting point. |

## What it handles

- **Private access:** Tailscale connects authorized devices to private HTTPS addresses without router port forwarding. Caddy routes requests; Authentik supplies shared sign-in where supported.
- **Accounts and passwords:** household administrator/member roles, invitations, app account provisioning and generated logins saved to Vaultwarden for use with Bitwarden clients. Some apps and mobile clients still need a separate login or device authorization.
- **Application management:** catalog discovery, configuration forms, download estimates, parallel image downloads, installation progress, start/stop, sign-in repair, app updates and removal.
- **AI:** Open WebUI by default, optional LobeChat, external provider routing through FreeLLMAPI and LiteLLM, local models through Ollama, document-search models, and optional local speech.
- **Chat tools:** app-specific assistants connect through ContextForge. Administrators select tools and inspect calls there. Household members also receive assistants. **Enabled tools can change application data without a separate approval dialog.** Connectors may use shared application credentials; these are not automatically limited to each person's private app data.
- **Calendar:** a personal calendar in Mu3Lab, with an optional connection to one Nextcloud calendar for viewing and editing, explicit transfer of locally kept events, and conflict handling.
- **Operations:** shared health observations, memory and disk accounting, durable background jobs, optional scheduled encrypted local backups, guided app recovery, and tagged Mu3Lab release updates for eligible installations.

## Applications in this checkout

The base installation includes Caddy, Authentik, Vaultwarden, Open WebUI, ContextForge, LiteLLM, FreeLLMAPI and Ollama, with Tailscale installed on the host. An AI provider or a downloaded local chat model is needed before chat can answer.

| Optional applications | What they provide |
| --- | --- |
| Immich; Nextcloud | Photos and videos; files, calendars and contacts |
| Paperless-ngx; Outline; SurfSense | Searchable paperwork; household wiki; AI research workspace |
| Mealie; Grocy; Actual Budget | Recipes and meal plans; pantry and household stock; budgeting |
| Baby Buddy; Beaver Habits | Baby-care records; personal habit tracking |
| AdventureLog; Dawarich | Travel planning and journals; personal location history |
| Navidrome; Audiobookshelf; RomM | Music; audiobooks and podcasts; a library and browser player for your game files |
| LobeChat; Firecrawl; Speech | Alternative chat; web extraction for AI; local speech recognition and speech playback |
| Kopia backups | Scheduled local backups and a backup browser |

Chat connectors are declared for Actual Budget, AdventureLog, Firecrawl, Grocy, Immich, Mealie, Nextcloud, Paperless-ngx and SurfSense. Installing an app does not give chat access to every feature of that app. Connector availability, credentials and the tools enabled in ContextForge determine what works.

The catalog is defined by [`apps/*/app.yaml`](apps). Proposals under `docs/expansion/` are not additional installable applications.

## Getting started

Use an **x86-64** computer with Ubuntu 22.04+, Debian 12+, or a compatible derivative accepted by the installer. ARM is currently rejected by the host check. You need administrator access, an internet connection, a Tailscale account and enough memory and disk for your chosen apps and data. There is no single capacity figure that guarantees the entire catalog will fit.

Run these commands as your ordinary Linux user, in a terminal:

```bash
git clone https://github.com/dcazes/Mu3Lab.git
cd Mu3Lab
./install.sh
```

Git must already be available to download the project; the script also needs `curl`, `tar` and `sha256sum`. See the [user guide](docs/user-guide.md) for preparation commands and the complete walkthrough.

Setup asks for your computer password, your name/email and a new Mu3Lab password, then guides Tailscale approval. It installs system software, prepares the apps and opens the private dashboard. **You must sign in and let the dashboard load before setup reports completion.** Follow Home's remaining steps for password autofill, AI, personal apps and other devices.

Keep the checkout where you installed it: the background services run from it. Re-running `./install.sh` checks and resumes setup; it does not download newer project source by itself.

## Boundaries to understand

Mu3Lab is in active development. These documents describe the checked-in implementation, not a guarantee that every upstream service or fresh-machine combination has passed real-device acceptance. See [acceptance](docs/acceptance.md) and [development](docs/development.md).

Private access does not mean that all activity stays on the server. Outside AI providers are allowed by default; **Local only** changes Mu3Lab's AI routing, while web searches, downloads and third-party app integrations can still contact external services. NVIDIA acceleration depends on working drivers and app support; AMD support varies by app and is not fully prepared by the installer.

Scheduled backups require installing **Kopia backups**. They remain on this server; off-device copying is your responsibility. Audiobook, podcast and game libraries under `/srv/mu3lab/media` are excluded. Preserve the recovery sheet and a separate copy of those libraries. App updates make a pre-update backup even without the scheduled backup app.

The dashboard and worker share credentials and Docker access. This is a trusted household server, not an isolation boundary for hostile users or arbitrary applications.

## Updates, removal and further reading

Use **Settings → System → Mu3Lab updates** for eligible tagged installations. Development or edited checkouts require manual source updates and `./install.sh`. App version changes are offered separately; installed apps retain their recorded versions until updated.

Preview full removal with:

```bash
./uninstall.sh --dry-run
```

Actual removal with `./uninstall.sh` deletes `/srv/mu3lab`, **including local backups, passwords and media**. Copy what you need elsewhere first. `--everything` also removes Docker and its unrelated contents, Tailscale and NVIDIA container support.

For detailed implementation and operating references, start with the [technical deep dive](docs/architecture.md). Existing focused guides cover [backups](docs/backup-recovery.md), [app updates](docs/app-updates.md), [chat connectors](docs/chat-connectors.md) and [development](docs/development.md); dated reviews and plans record earlier designs and should not be treated as current feature promises.

Mu3Lab is licensed under [AGPL-3.0-or-later](LICENSE). Included applications retain their own licenses.
