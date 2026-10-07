# Implementation record: first seven apps (2026-10-06)

Owner request: implement Outline, Beaver Habits, Audiobookshelf, RomM, Dawarich,
Open WebUI and speech, then stop and commit. Done on `expansion/apps-2026` as one
commit. Grocy fixes (task 0-1) and the remaining apps were not part of the request.

## What was built

| Area | What | Status |
|---|---|---|
| Platform | Media libraries under `/srv/mu3lab/media` (`media:` in manifests, created by the installer's layout step and on render, never deleted or backed up) | Done, tested |
| Platform | Apps working with other apps: `integrates_with` + `{{provider:KEY}}`; consumers re-rendered and restarted (only if changed) after a provider is installed or removed (`ctl/engine/rewire.py`) | Done, tested |
| Platform | `per_person_accounts` rule: a script from the app's folder creates each household member's account; reruns when the household changes | Done, tested |
| Platform | `initial_owner_guard`: script-based check (Postgres or in-container databases) and a `finish` step that must succeed before the household is admitted | Done, tested |
| Platform | Routes: `trusted_value: email` | Done, tested, `caddy validate` passed |
| Platform | OIDC: per-app role claim served as its own scope (`role_claim`), removed on uninstall | Done, tested |
| Platform | OIDC launch `form_post` for Rails-style sign-in forms (launch page + sign-in check) | Done, tested against Dawarich |
| Platform | Configuration defaults may name a fact; `{{country_code}}` from the host time zone | Done, tested |
| Platform | Loopback helper: plain-text replies and longer timeouts | Done |
| Apps | `beaver-habits`, `outline`, `audiobookshelf`, `romm`, `photon`, `dawarich`, `open-webui`, `speaches` | Done, see below |
| Speech | LiteLLM routes `mu3lab-stt` / `mu3lab-tts` to Speaches when installed; LiteLLM gains a Postgres database for personal keys; `/v1/*` with a bearer key skips the Authentik gate; request logging off | Done, tested live |
| Speech | Settings → Voice key (create once, replace, turn off); keys revoked when a person is deactivated | Done, API + UI |
| GPU | Ollama keep-alive 24 h → 10 min, one model at a time; no chat models | Done |

## Verified against the pinned images (throwaway `-test-` containers)

| App | Pinned | Findings that shaped the code |
|---|---|---|
| Beaver Habits | `daya0576/beaverhabits:0.10.0` | `TRUSTED_EMAIL_HEADER` trusts **existing** users only (401/redirect otherwise) → per-person accounts. New users also need an initial habit list or every page 404s. `/login`, `/register`, `/demo` redirected away. Runs as `nobody` → init container sets ownership. |
| Outline | `outlinewiki/outline:1.10.1` | First sign-in creates the workspace and becomes admin; later people join as members. `OIDC_ISSUER_URL` discovery is fetched **at startup** (crashes if unreachable; Mu3Lab starts OIDC apps only after Authentik publishes the client). Data folder must belong to UID 1001. |
| Audiobookshelf | `ghcr.io/advplyr/audiobookshelf:2.37.1` | OIDC is configured by API, not env. `/init` replies plain text. The web app lives under `/audiobookshelf` and `authOpenIDSubfolderForRedirectURLs` must be set, otherwise the redirect URI contains `undefined`. The phone app's redirect is handled by the server (`/auth/openid/mobile-redirect`), so Authentik needs no custom scheme. The group claim is requested **as a scope** → per-app role scope. No official iOS app in the App Store (TestFlight only) → iPhone uses the web player. |
| RomM | `rommapp/romm:5.3.1` + `mariadb:11.8` | Roles map straight from the standard `groups` claim; `DISABLE_SETUP_WIZARD` + `DISABLE_USERPASS_LOGIN` make it Authentik-only with no wizard. RomM's own nginx sets the emulator isolation headers. |
| Photon | `rtuszik/photon-docker:2.4.0` (Photon 1.3.0) | Official GraphHopper download server works (`BASE_URL`); Andorra ready in 15 s; the Canada index is ~5 GB compressed, so first start can take up to an hour. |
| Dawarich | `freikin/dawarich:1.15.3` + `postgis/postgis:17-3.5-alpine` | Seeds an admin `demo@dawarich.app` / `safepassword` whenever there are no users, and refuses to link OIDC to an existing account by email → the owner signs in first, then a finish script makes them admin and deletes the demo account (no re-seed once a user exists). Sign-in starts with a CSRF form → `form_post` launch. Without a geocoder setting Dawarich shows no place names (no public fallback). PostGIS is published for amd64 only. |
| Open WebUI | `ghcr.io/open-webui/open-webui:v0.11.4` | All env names confirmed in source. First OAuth user becomes admin → owner guard. `ENABLE_PERSISTENT_CONFIG=false` so Mu3Lab's settings always win. Healthy in 15 s. |
| Speaches | `ghcr.io/speaches-ai/speaches:0.8.3-cpu` / `-cuda` | Latest labelled-stable release (0.9.0 is still `rc`). With `API_KEY` set even `/health` needs the key → internal-only without a key, like Ollama. Speech → text → speech round trip exact; OpenAI voice names accepted. GPU: Whisper large-v3-turbo uses ~2.2 GB, 2 s first transcription. |
| LiteLLM | existing `v1.103.2` + `postgres:16-alpine` | Database migrations run on start (15 s). Personal key: speech works, chat refused (403), cannot mint keys (401), revoked key refused (401). A hex master key works for key management. |

## Departures from the plan documents (deliberate)

1. **Photon is a soft dependency of Dawarich**, not a hard one: its first index
   download can outlast an install; Dawarich gets place names automatically when
   Photon finishes (rewiring). `apps/dawarich.md` assumed `depends_on`.
2. **No custom-scheme redirect feature**: Audiobookshelf turned out not to need one.
3. **Open WebUI is an optional app** beside LobeChat, not yet the selectable household
   chat app. Tasks 3-1 (chat provider interface), 3-2 (gateway approvals) and 3-4
   (switching) remain; until then it has no Mu3Lab assistants or app tools.
4. **Speaches runs without an API key** (health endpoint would otherwise be locked);
   it is reachable only on Mu3Lab's internal network and loopback.
5. **No Wyoming bridge yet**: it serves Home Assistant, which is not installed.
6. **No chat connectors** for these apps in this step (Outline's needs an API key the
   owner would paste; the plan says to ask first — see open items).

## Not yet verified (needs the disposable VM or the owner)

- Full install → Open → sign-in through real Authentik and Tailscale for every app
  (VM acceptance, [../10-acceptance.md](../10-acceptance.md) §3). Containers and
  sign-in hand-offs were tested; the complete browser journey was not.
- Authentik accepting the `abs_roles` scope request and emitting the claim.
- Phone apps: Audiobookshelf (Android), Dawarich/OwnTracks with Tailscale always-on.
- Upgrade of an existing installation: LiteLLM now has a database, so the next core
  setup creates it (core reinstall recommended per rebuild decision 1).

## Open items for the owner

1. Outline chat assistant needs an Outline API key: one paste in Get started, or no
   Outline assistant? (plan `apps/outline.md` §4)
2. Pre-existing, not caused by this work: `tools/validate_compose.py` stops at
   AdventureLog (`MU3LAB_OWNER_EMAIL` missing in its test env), so it never checks
   later apps. All projects were validated separately for this commit.

## Second pass (same day): automatic owner accounts and fixes

Owner decisions: generated passwords saved to the vault (never the owner's own
password); Photon stays on the prebuilt all-Canada index; the owner runs VM testing.

**No first-visit step any more.** Authentik's `sub` for an app is the person's
`user.uid` (checked in Authentik 2026.8.3: `_resolve_sub` → `user.uid` for the
default `hashed_user_id` mode), the same value the dashboard receives as
`X-authentik-uid` and stores as the owner's ID. So each app's owner account is now
created at install, already linked to the owner's sign-in, through the app's own code:

| App | Script | How (verified against the pinned image) |
|---|---|---|
| Outline | `scripts/adopt-owner.js` | Outline's `accountProvisioner` (what its OIDC sign-in runs): workspace + owner admin + link (provider = Authentik's host, ID = sub). Re-run changes nothing. |
| Dawarich | `scripts/adopt-owner.rb` | Owner created as admin with `provider=openid_connect, uid=sub`; demo admin deleted through Dawarich's own deletion; not re-seeded after restart. Refuses an email already linked to another sign-in. |
| Open WebUI | `scripts/adopt-owner.py` | Open WebUI's account service creates the owner as admin with `oauth.oidc.sub`; also merges by email. Refuses a conflicting link. |

The owner guard and its script/finish additions are therefore unused and were
removed; `container_script` arguments may now name facts such as `{{dns_name}}`.

**Default accounts.** Every app's default administrator is now the owner's own
Authentik username (Grocy, Mealie, Audiobookshelf, Outline, Dawarich, Open WebUI,
Immich and the rest) or removed. Two internal service logins keep fixed names and
generated, vault-saved passwords: FreeLLMAPI (`mu3lab-gateway@localhost.test`) and
the LiteLLM admin page (`admin` + master key). They are installed during core setup;
renaming them to the owner is possible later but out of scope here.

**Fixed:**
- Grocy chat provisioning finds the owner by username (connector `owner_username: true`);
  verified in the real image with two administrators: same key reused; unknown or missing
  owner refused.
- Grocy time zone falls back to UTC; currency is a setting defaulting from the
  computer's country (`{{currency_code}}`, CAD in Canada).
- Regression test: Grocy `/api/*` without the key header still goes through Authentik.
- `tools/validate_compose.py` renders rules with a placeholder owner and reports every
  failing project: 36 of 36 now validate.

**Still open:** Photon's index is included in backups (manual and pre-update only);
excluding it belongs to the Kopia task's `backup.exclude`.

**Re-verified:** `make verify` (716 Python tests, dashboard checks and build), all 36
Compose projects, `caddy validate` on all 19 generated routes, and each new owner
script live against its pinned image.
