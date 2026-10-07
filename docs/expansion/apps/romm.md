# RomM (task 5-05)

Retro game library manager with in-browser play. Mu3Lab never provides
games; people add their own legally obtained files.

## 1. VERIFY list

- Latest stable release (4.x at planning time; docs list 4.9.x); image
  `rommapp/romm:<ver>`; digest; licence (AGPL-3.0); arm64.
- Database: MariaDB (official compose) or Postgres support
  (`ROMM_DB_DRIVER`, VERIFY). Use whatever the **official** compose uses.
  Pin the major.
- Redis/Valkey: built into the image or separate (VERIFY).
- Required env: `ROMM_AUTH_SECRET_KEY`, `DB_HOST`, `DB_NAME`, `DB_USER`,
  `DB_PASSWD`, `ROMM_BASE_URL` (must match the public URL).
- Paths: `/romm/library` (games: media), `/romm/resources` (scraped
  metadata/covers: regenerable), `/romm/assets` (**saves, save states,
  screenshots**: user data!), `/romm/config` (`config.yml`), `/redis-data`.
- OIDC env (reported): `OIDC_ENABLED`, `OIDC_PROVIDER`, `OIDC_CLIENT_ID`,
  `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI` (`/api/oauth/openid`),
  `OIDC_SERVER_APPLICATION_URL`, plus role claim support (`OIDC_CLAIM_ROLES`,
  `OIDC_ROLE_ADMIN`, `OIDC_ROLE_EDITOR`, `OIDC_ROLE_VIEWER`; VERIFY names
  and behaviour). Does OIDC auto-create users, and with which default role?
  How are users matched (email)?
- First admin: the setup wizard on first visit. Is there an API to create
  the first admin (unauthenticated only while no users exist)? Can password
  login be disabled (`DISABLE_USERPASS_LOGIN`, VERIFY)?
- Metadata providers and their keys: IGDB (Twitch client ID/secret),
  ScreenScraper (user/password), MobyGames (API key), SteamGridDB (API
  key), RetroAchievements (API key), Hasheous (no key), LaunchBox (local
  DB). Which work with no keys at all.
- EmulatorJS: whether some cores need `Cross-Origin-Opener-Policy:
  same-origin` and `Cross-Origin-Embedder-Policy: require-corp`
  (SharedArrayBuffer). Does RomM set them itself?
- Health endpoint (`/api/heartbeat`, VERIFY).
- MCP servers for RomM (search). Mobile clients (community Android
  launchers that sync with RomM, e.g. "Argosy"; VERIFY).

## 2. Services

| Service | Image | Port | Data / media |
|---|---|---|---|
| `romm` | rommapp/romm | 8080 → `127.0.0.1:8091` | `data/romm/assets` → `/romm/assets`; `data/romm/config` → `/romm/config`; `data/romm/resources` → `/romm/resources`; `data/romm/redis` → `/redis-data`; media `roms` → `/romm/library` (rw: RomM may rename and upload) |
| `mariadb` (or per VERIFY) | mariadb:<major> pinned | internal | `data/romm/db` |

## 3. Sign-in and accounts

- `sign_in.method: oidc`, `route.access: open`.
- **First admin without a wizard:**
  a. If an API creates the first admin when no users exist: call it after
     healthy with the owner's username/email and a random password; mark it
     admin; OIDC login then matches by email (VERIFY matching). Password
     login off after OIDC verification (`DISABLE_USERPASS_LOGIN`).
  b. If OIDC auto-creates users **and** maps roles from claims: skip the
     wizard by making the owner's first OIDC login create an admin through
     the role claim (`role_claim` mapping `mu3lab-operators → admin`), with
     `initial_owner_guard`. VERIFY that the setup wizard does not block OIDC
     before any user exists.
  c. Neither → STOP.
- Roles: operators → admin, household → viewer or editor (recommend
  **editor**, so people can upload their own games and keep saves; VERIFY
  what editor allows).

## 4. Metadata keys

`configuration:` fields (all optional, secret type): IGDB client ID and
secret, ScreenScraper username and password, SteamGridDB key,
RetroAchievements key. Each has a one-line "how to get one" with an HTTPS
link. With none set, use the keyless providers (Hasheous, LaunchBox; VERIFY)
and say "Add an IGDB key for richer game details" on the app page.

## 5. Chat connector

Optional and low value. If a maintained MCP exists, review it (read-only
categories: `library`, `platforms`, `collections`). Otherwise **no
connector** in this task; note it in `notes/future.md`.

## 6. Phone clients

Web (PWA) with EmulatorJS (touch controls) as primary. Community launchers
listed only if VERIFY confirms they support RomM with a token or OIDC.

## 7. Backups

Include `assets` (saves!), `config`, and the database. Exclude `resources`
(regenerable), `redis` and the ROM media library (opt-in). Stop-snapshot-start.

## 8. Edge cases

| # | Case | Handling |
|---|---|---|
| R1 | Cores needing COOP/COEP | Add the headers at the route **only** for the emulator paths if RomM does not set them (VERIFY which paths). Test one threaded core. Global COEP can break cover images from other origins. |
| R2 | Huge uploads (disc images, GBs) | Route timeout and body limit; recommend copying into `media/roms` directly for big files; the page says how. |
| R3 | Folder structure | RomM needs `roms/<platform-slug>/...` (VERIFY); install creates `media/roms` empty with a README listing platform slugs. |
| R4 | Scan load | First scans can be heavy; run in the background; no install wait. |
| R5 | Legal | The app page says "Add only games you own". No downloading feature added by Mu3Lab. |

## 9. Acceptance (VM)

Standard journey plus: add a homebrew or public-domain ROM (for example a
freely licensed homebrew NES game), scan, play in the browser, save state,
back up, restore, and the save state is back.
