# Audiobookshelf (task 5-03)

Audiobooks and podcasts with progress sync and official phone apps.

## 1. VERIFY list

- Latest stable release; image `ghcr.io/advplyr/audiobookshelf:<ver>`;
  digest; licence (GPL-3.0); arm64.
- Internal port (80 in recent images; older ones used 13378; VERIFY),
  paths: `/config` (database), `/metadata` (covers, cache, backups),
  library folders. Process UID (runs as root by default? Is there a
  `user:` / PUID option?).
- **Server initialisation API:** on a fresh server, the first user (root) is
  created by `POST /init` with `{newRoot: {username, password}}` (reported).
  Confirm the request and response, and that it is refused after init.
- Login API (`POST /login`) returns a token for API calls.
- **OIDC configuration by API** (not env): `PATCH /api/auth-settings`
  (reported) with fields such as `authOpenIDIssuerURL`,
  `authOpenIDAuthorizationURL`, `authOpenIDTokenURL`, `authOpenIDUserInfoURL`,
  `authOpenIDJwksURL`, `authOpenIDLogoutURL`, `authOpenIDClientID`,
  `authOpenIDClientSecret`, `authOpenIDButtonText`, `authOpenIDAutoLaunch`,
  `authOpenIDAutoRegister`, `authOpenIDMatchExistingBy`
  (`username`/`email`), `authOpenIDMobileRedirectURIs`,
  `authOpenIDGroupClaim`, `authOpenIDAdvancedPermsClaim`,
  `authActiveAuthMethods`. Get the exact names from the pinned version's
  server source (`server/controllers/MiscController.js` or similar).
- Redirect URIs: web `/auth/openid/callback` and
  `/auth/openid/mobile-redirect`; the mobile app scheme
  `audiobookshelf://oauth`.
- Group claim semantics: values must be `admin`, `user` or `guest`
  (reported).
- Library creation API (`POST /api/libraries`) with folders and media type
  (`book`, `podcast`).
- Health endpoint (`/healthcheck`, VERIFY).
- Official apps: Android (Play Store) and iOS (App Store or TestFlight;
  VERIFY the current state). Third-party clients (ShelfPlayer and Plappa on
  iOS, Lissen on Android; VERIFY). Which support OIDC login.
- Community MCP servers for Audiobookshelf (search). Licence and activity.

## 2. Services

| Service | Image | Port | Data / media |
|---|---|---|---|
| `audiobookshelf` | advplyr/audiobookshelf | 80 → `127.0.0.1:13378` | `data/audiobookshelf/config` → `/config`; `data/audiobookshelf/metadata` → `/metadata`; media `audiobooks` → `/audiobooks` (rw), `podcasts` → `/podcasts` (rw) |

`rw` media: podcasts download episodes into their folder; audiobook metadata
embedding is a user choice. VERIFY whether `ro` audiobooks still allow normal
use; prefer `ro` for audiobooks if so.

## 3. Sign-in and accounts

1. **Root = owner, with no password ever used by a person:**
   - After healthy, an `api_bootstrap` step (in `apps/audiobookshelf/hooks.py`
     `bootstrap_account`, VERIFY the hook contract in `ctl/engine/hooks.py`)
     calls `POST /init` with username = **the owner's Authentik username** and
     a random password (persisted encrypted first, as onboarding does).
   - Log in with it, then `PATCH /api/auth-settings` to enable OpenID with
     Authentik's endpoints, `authOpenIDMatchExistingBy: username`,
     `authOpenIDAutoRegister: true`, `authOpenIDAutoLaunch: true`,
     `authOpenIDMobileRedirectURIs: ["audiobookshelf://oauth"]`,
     `authOpenIDGroupClaim: "abs_roles"`, and keep `local` login enabled
     **for now**.
   - Owner's first Open → OIDC → matched to root by username.
   - Finalisation (worker, on callback evidence): verify the root user is
     linked to OIDC (VERIFY how: the user has an OpenID subject field via
     `GET /api/users/<id>`), then set `authActiveAuthMethods: ["openid"]`
     (password login off). Do **not** save the root password to the vault
     (SSO app); delete the durable copy once OIDC is verified, as other SSO
     apps do. Keep it only until verification.
2. **Roles:** use the per-app role claim ([../05-platform.md](../05-platform.md)
   §10): claim `abs_roles`, map `mu3lab-operators → admin`,
   `mu3lab-users → user`. Single value (`multi: false`) if Audiobookshelf
   expects a list containing one role (VERIFY).
3. **Phone redirect:** `extra_redirect_uris: ["audiobookshelf://oauth"]`
   ([../05-platform.md](../05-platform.md) §10.1).
4. **Route:** `access: open` (the app authenticates; phone apps talk directly
   to the server). Redirect the web login page to the OIDC start if
   `authOpenIDAutoLaunch` does not already skip it (VERIFY).

## 4. Libraries

After OIDC setup, create libraries via API if absent:
"Audiobooks" (media type `book`, folder `/audiobooks`) and "Podcasts"
(`podcast`, `/podcasts`). Idempotent: match by name, never duplicate.

## 5. Chat connector

Prefer a reviewed community MCP server. Otherwise `mu3lab-adapter` with:
`search_library`, `get_item`, `in_progress`, `recently_added`,
`listening_stats` (read); `add_podcast_feed`, `mark_finished` (write, off).
Block: user management, server settings, backups, file deletion.
Credential: a dedicated API token (VERIFY how ABS issues API keys for a
user; newer versions have API keys). A dedicated "mu3lab-mcp" user with the
"user" role plus access to all libraries is acceptable **if** created through
the API with a random password never shown to anyone. Its progress data is
separate from people's.

## 6. Phone clients

Official app (Android, iOS per VERIFY): steps: install, connect Tailscale,
server address `https://<host>:8462`, tap **Login with OpenID**, Authentik
signs in, the app returns. Third-party clients that support OIDC listed with
`support: community`. Web fallback: PWA.

## 7. Backups

`data/audiobookshelf/config` and `data/audiobookshelf/metadata`, excluding
`metadata/cache/**` and `metadata/streams/**` (VERIFY folder names).
Audiobookshelf has its own internal backups in `metadata/backups`; exclude
those from Kopia (duplicates) and turn off ABS's scheduled backups (VERIFY
the setting), so there is one backup system. Media excluded by default.

## 8. Edge cases

| # | Case | Handling |
|---|---|---|
| A1 | Owner's username conflicts with an existing ABS user (reinstall with kept data) | Root already exists, so skip `/init`; verify root's username equals the owner's; if not, STOP (do not rename silently). |
| A2 | Large library scan on first start | Scanning runs in the background; install does not wait for it. |
| A3 | Mobile OIDC redirect rejected | `audiobookshelf://oauth` must be in **both** Authentik's redirect URIs and ABS's mobile redirect list. Tested in the VM with the redirect-URI check; on a phone by the owner. |
| A4 | Media folder permissions | The container user must read `/audiobooks` and write `/podcasts`; a probe at install (05 §3.3). |
| A5 | Person's role changes | ABS updates its role from the group claim on next login (VERIFY). |

## 9. Acceptance (VM)

Standard journey plus: drop a small public-domain MP3 audiobook into
`media/audiobooks`, scan, play in the browser; progress saved; a second
person (household) has the "user" role and cannot see server settings.
