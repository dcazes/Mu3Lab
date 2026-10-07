# 01. Owner decisions (do not re-ask)

Recorded 2026-10-06. These add to the decisions in
[../rebuild-handoff.md](../rebuild-handoff.md) §1, which still apply, except
where this file explicitly supersedes one.

## 1. Standing rules that still apply

These come from earlier sessions. Every task in this plan MUST respect them.

1. **SSO-only from install.** An app with native SSO signs in through
   Authentik from the very first Open. No app login form, no first-run setup
   screen, no "link your account" step. Immich's setup is the model.
2. **No Repair button.** Installation verifies sign-in end to end
   (`ctl/lifecycle/signin_check.py`) and fails loudly if it does not work.
3. **Apps without SSO** keep an Authentik gate (when the app's clients allow
   one). Mu3Lab generates the login and saves it to the person's Vaultwarden
   with a URL Bitwarden matches, including the port. Nobody sees or guesses
   an initial password. Mu3Lab never keeps human passwords after they are in
   the vault.
4. **Bitwarden fills, Vaultwarden stores.** Never build server-side autofill.
5. **Approved versions only.** Every image is pinned by digest. Apps update
   only to versions the maintainer approved (`make approve`). Never use
   `latest`.
6. **Official interfaces.** Use each app's supported API, CLI or management
   command. No SQL written into another app's database, no browser-click
   automation, no patching app source. A narrow, documented extension point
   the app itself provides is allowed, like Grocy's `GROCY_AUTH_CLASS`.
7. **No app IDs in shared code.** Nothing under `ctl/` may contain an app ID
   string. App-specific behaviour lives in `apps/<id>/` (manifest, scripts,
   `hooks.py`). `tools/check_no_app_ids.py` enforces this.
8. **Never touch the owner's running stack.** Never run `./install.sh`,
   `./uninstall.sh` or `sudo` on the owner's computer. Never stop or modify
   containers whose names lack `-test-`. Lifecycle testing happens in the
   disposable VM (`tools/vm/fresh-vm.sh`).
9. **Say plainly when work is still running.** The owner reinstalls as soon
   as a turn looks finished.
10. **Chat assistants:** one app, one assistant, one connector. Every tool is
    reviewed in `review.yaml`. Tools that change data start switched off and
    ask before each call. See [../chat-connectors.md](../chat-connectors.md).

## 2. Decisions made on 2026-10-06

### 2.1 Apps to integrate

| App | Decision | Why / rejected alternatives |
|---|---|---|
| **Audiobookshelf** | Integrate | Native OIDC, official phone apps, podcasts too. |
| **RomM** | Integrate | Native OIDC, in-browser play (EmulatorJS), metadata scraping. |
| **HomeBox** (sysadminsmedia fork) | Integrate, **one shared household inventory** | The original `hay-kot/homebox` is archived; the sysadminsmedia fork is maintained and has native OIDC. |
| **Outline** | Integrate (replaces the requested Joplin) | Joplin Server is only a sync server (no web editor; SAML only, no OIDC). Trilium was considered but is **single-user**: everyone who signs in shares one notebook. Outline is multi-user, has native OIDC, and private plus shared collections. Licence: BSL 1.1, which permits self-hosting for your own use. |
| **ComfyUI** | Integrate | Owner-only (it runs arbitrary custom-node code). Household members reach image generation through the chat app. |
| **n8n** | Integrate, **tailnet only** | Community edition has no SSO (SSO needs a paid licence), so: Authentik gate plus a vault-saved login. No public webhooks. |
| **Dawarich** | Integrate, **live phone tracking with Tailscale always-on** | Native OIDC. Location data is the most sensitive data in the stack. |
| **Home Assistant** | **Container** edition (not HA OS) | Managed like every other app. Add-on equivalents become Mu3Lab apps. |
| **Frigate** | Plan and build, **install-gated until a camera exists** | Owner has no cameras yet. |
| **Beaver Habit Tracker** | Integrate (replaces the requested Habitica) | Habitica has no official self-hosting support, needs Node and MongoDB from community images, has no SSO, and its official phone apps only talk to habitica.com. Beaver is multi-user, light (SQLite) and supports trusted-header SSO. |

### 2.2 Supporting services

| Service | Decision |
|---|---|
| **Mosquitto** (MQTT broker) | Installed automatically as a dependency of Home Assistant and Frigate. |
| **Speaches** (Whisper speech-to-text plus Piper/Kokoro text-to-speech) | Integrate as a **server-wide speech service**. It must be usable by Home Assistant, by Open WebUI and LobeChat, by every other app on the server, **and by apps outside the stack** (for example dictating into Google Docs or LibreOffice on a laptop). See [08-speech.md](08-speech.md). |
| **Wyoming bridge** | Lets Home Assistant use Speaches. |
| **Photon** (geocoder) | Self-hosted for Dawarich, so location history never goes to a public geocoding service. Regional extract chosen at install. |
| ESPHome, Zigbee2MQTT | **Not now.** No dongle yet. Note them as future work only. |

### 2.3 Backups: Kopia replaces restic

- Kopia replaces restic **everywhere** restic is used today (`ctl/backups.py`
  and its callers).
- The owner wants **Kopia's web interface** available.
- Default destination: local disk (`/srv/mu3lab/backups/`, as today).
- Optional: **copy to another computer over the tailnet (SFTP)**. No cloud
  destination in this plan.
- Per [../rebuild-handoff.md](../rebuild-handoff.md) decision 1, no data or
  upgrade path from restic needs keeping. See [06-kopia.md](06-kopia.md) §8
  for what to do with an old repository.

### 2.4 Chat app choice. Supersedes rebuild-handoff decision 6 in part

- The household chat app is **either LobeChat or Open WebUI**, chosen at
  install. It can be **switched later** from the dashboard. Conversation
  history stays in the old app (exportable); assistants and connectors are
  recreated in the new one.
- **Hermes Agent** (Nous Research) is **not** a household chat app. It is an
  optional **owner add-on agent** (personal memory, skills, Telegram/Signal
  channels, scheduled tasks) that can sit beside either chat app.
- Everything else in decision 6 still holds **while LobeChat is the selected
  app**: device-code approval, official v1 API, no SQL in LobeChat's database.

### 2.5 Ollama and the GPU

- The host has an **NVIDIA RTX 3060 with 12 GB**, 12 CPU threads, 62 GB RAM,
  and 258 GB free on the system disk (2026-10-06).
- **No chat model is ever loaded into Ollama.** Ollama is used only for
  embedding models (and any future small non-chat model). Chat models come
  from external providers through FreeLLMAPI and LiteLLM.
- Whisper does **not** run in Ollama (Ollama cannot serve Whisper). It runs
  in Speaches.
- GPU policy: **auto-yield**. Every GPU app unloads models when idle, so
  whichever app is in use gets the memory. The dashboard shows GPU memory
  use and warns when it is tight. See [05-platform.md](05-platform.md) §4.

### 2.6 Sign-in and access decisions per app

| App | Route access | Sign-in | Accounts |
|---|---|---|---|
| Audiobookshelf | open (tailnet) | Native OIDC | Owner adopted as root; others auto-registered |
| RomM | open | Native OIDC | Owner admin; others auto-created |
| HomeBox | open | Native OIDC | Everyone joins the owner's household collection |
| Outline | open | Native OIDC | First sign-in restricted to the owner, who becomes admin |
| Dawarich | open | Native OIDC | Owner admin; the default demo account is removed |
| Beaver Habits | trusted_header | Authentik identity header | Each person gets a private habit list |
| n8n | gate, operators only | Vault-saved login | Owner only by default |
| ComfyUI | gate, operators only | Authentik gate only (ComfyUI has no accounts) | n/a |
| Home Assistant | **open, no Authentik gate** | HA's own login | Mu3Lab creates **each person's** HA account through HA's official API and saves it to **their** vault. Required so the Companion phone app works. |
| Frigate | trusted_header | Proxy identity and role headers | Operators are admins, household members are viewers |
| Kopia UI | gate, operators only | Vault-saved login | n/a |
| Open WebUI | open | Native OIDC | Operators are admins |
| Hermes dashboard | gate, owner only | Authentik gate, plus Hermes's own OIDC if verified | Owner only |

"open" still means **private to the tailnet**. Nothing in this plan is
reachable from the public internet.

### 2.7 Storage

- Media libraries live under **`/srv/mu3lab/media/`** (new), for example
  `media/audiobooks`, `media/podcasts`, `media/roms`, `media/frigate`,
  `media/ai-models`.
- Media is **excluded from backups by default** because it is large and can
  be obtained again. The owner can opt a library in.

### 2.8 Where the work happens

- One long-lived feature branch: **`expansion/apps-2026`** in this repository
  (not a GitHub fork). Worktree: `/home/dak/Desktop/Mu3Lab-expansion`.
- One short-lived task branch per task, merged into the feature branch. The
  feature branch merges into `main` only after Phase 6 acceptance and the
  owner's approval. See [03-workflow.md](03-workflow.md).

## 3. Open questions the implementer MUST NOT decide

When you reach one of these, stop and ask the owner. Bring a short
recommendation.

| # | Question | Arises in |
|---|---|---|
| Q1 | Should Home Assistant's voice assistant use an LLM for conversation? Ollama has no chat model, so this needs an OpenAI-compatible conversation integration. HA's official integrations may not accept a custom base URL. | [apps/home-assistant.md](apps/home-assistant.md) §Voice |
| Q2 | Install the community Frigate integration (via HACS-style custom component) into Home Assistant? It is unofficial code inside HA. Without it, HA sees Frigate only through MQTT. | [apps/frigate.md](apps/frigate.md) |
| Q3 | If Open WebUI has no built-in approval before a tool call, is the dashboard approval inbox ([07-chat-choice.md](07-chat-choice.md) §4) acceptable for household members on phones? | 3-2 |
| Q4 | Which ComfyUI starter model to download at install (licence and size differ)? | [apps/comfyui.md](apps/comfyui.md) |
| Q5 | Which Photon region(s) to index? Default: the owner's country from the host's timezone. | [apps/dawarich.md](apps/dawarich.md) |
| Q6 | Any time a VERIFY fails and its section has no fallback. | anywhere |
