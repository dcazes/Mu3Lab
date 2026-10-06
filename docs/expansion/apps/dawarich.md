# Dawarich (task 5-07) and Photon (task 5-06)

Private location history: a Google Timeline replacement. Phones report GPS
points continuously; Tailscale must be always-on (owner decision). Photon
turns coordinates into places **on this server**, so location data never
reaches a public geocoder.

**Location history is the most sensitive data in Mu3Lab.** Every choice
below favours privacy: no public geocoder, no telemetry, backups encrypted
(Kopia) and copied only to the owner's own computer.

---

## Photon (task 5-06, do first)

### P1. VERIFY list

- Official Photon (komoot/photon): latest release, licence (Apache-2.0),
  Java runtime need. Is there an **official** Docker image? If not, choose
  between: (a) a Mu3Lab-built image (Dockerfile in `apps/photon/`: pinned
  Eclipse Temurin JRE image + pinned photon release JAR with checksum), or
  (b) the community `rtuszik/photon-docker` (licence, activity, what it
  downloads at runtime). Prefer (a) unless (b) is clearly maintained and
  transparent. Record the choice.
- Index downloads: country extracts (reported at GraphHopper's download
  server, by country code), their sizes, update frequency, and checksum
  files. The full planet index (~200 GB+) is **not** supported in Mu3Lab.
- Photon's port (2322), API paths (`/api`, `/reverse`), health check.
- Memory needs for one or a few countries.

### P2. Design

- App folder `apps/photon/`, `tier: optional`, `group: infrastructure`,
  capability `geocoder`, no route, `local_port: 2322` (loopback, health only).
- `configuration:`: `countries` (comma-separated ISO codes). Default: the
  country from the host time zone if derivable (helper in `ctl/hostinfo.py`
  with a test), otherwise required input. This is open question **Q5**:
  confirm the default with the owner once.
- **Index download** in a rule or `hooks.py` before first start: download each
  extract with resume support, verify checksums if published, extract into
  `data/photon/index`, show progress (size can be several GB). Preflight disk
  space: refuse if free space < 2.5 × the expected size, with a clear message.
- Monthly index refresh (optional, `configuration: refresh_monthly`, default
  off): download into a staging folder, swap atomically, restart.
- **Backups:** exclude the index (regenerable). Nothing else to back up.
- Network: internal network shared with Dawarich (`mu3lab_geocoder`), plus
  egress for downloads during install/refresh only (VERIFY that the engine
  can run the download in a separate helper container with egress while the
  service itself has none; if too complex, allow egress and record it).

---

## Dawarich (task 5-07)

### D1. VERIFY list

- Latest stable release; image `freikin/dawarich:<ver>`; digest; licence
  (AGPL-3.0); arm64.
- The **production** compose from upstream (reported
  `docker/docker-compose.production.yml`): services (`dawarich_app`,
  `dawarich_sidekiq`), database (`postgis/postgis:<pg>-<postgis>`), Redis.
  Exact env: `RAILS_ENV=production`, `SECRET_KEY_BASE`, `DATABASE_HOST`,
  `DATABASE_USERNAME`, `DATABASE_PASSWORD`, `DATABASE_NAME`, `REDIS_URL`,
  `APPLICATION_HOSTS`, `APPLICATION_PROTOCOL`, `TIME_ZONE`, `SELF_HOSTED=true`,
  `STORE_GEODATA`, `PHOTON_API_HOST`, `PHOTON_API_USE_HTTPS`,
  `DISABLE_TELEMETRY` (or equivalent), registration toggles.
- OIDC env (reported): `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_ISSUER`,
  `OIDC_REDIRECT_URI` (`/users/auth/openid_connect/callback`),
  `OIDC_PROVIDER_NAME`, `OIDC_AUTO_REGISTER`, `OIDC_PKCE_ENABLED`. Must be set
  on **both** app and sidekiq. Can email/password sign-in be disabled
  (`ALLOW_EMAIL_PASSWORD_REGISTRATION`, a login switch)?
- **Default/demo account:** Dawarich has historically seeded
  `demo@dawarich.app` / `password` on fresh installs. Confirm for the pinned
  version, and how to remove it (Rails runner, a rake task, admin UI only?).
- How a user becomes **admin** (an `admin` flag; first user?). A supported
  way to set it: a rake task, or `bin/rails runner` with the app's own model
  methods (allowed: it is the app's management command, like Django's
  `manage.py`; never raw SQL).
- API keys: per-user API key (settings page), used by phone apps as
  `api_key` query parameter or `Authorization: Bearer` (VERIFY both).
- Phone apps: official Dawarich iOS app; OwnTracks (Android/iOS, HTTP mode
  URL `/api/v1/owntracks/points?api_key=…`); Overland (iOS); GPSLogger
  (Android). Exact endpoint URLs per app from the docs.
- Health endpoint (`/api/v1/health`, VERIFY).
- Memory/CPU: sidekiq jobs (imports and reverse geocoding) can be heavy.

### D2. Services

| Service | Image | Port | Data |
|---|---|---|---|
| `app` | freikin/dawarich | 3000 → `127.0.0.1:3006` | `data/dawarich/storage` (VERIFY paths: public/imports, storage) |
| `sidekiq` | same image, sidekiq command | none | same volumes |
| `db` | postgis/postgis pinned | internal | `data/dawarich/postgres` |
| `redis` | redis pinned | internal | `data/dawarich/redis` (if persistence needed) |

`depends_on: [photon]` (hard; owner chose self-hosted geocoding). Env
`PHOTON_API_HOST=photon:2322`, `PHOTON_API_USE_HTTPS=false`. VERIFY that the
host value accepts `host:port`.

### D3. Sign-in and accounts

- `sign_in.method: oidc`, `route.access: open`. The app authenticates the
  web UI via OIDC and phone apps via per-user API keys. No Authentik gate is
  needed, and none would work for phone apps.
- **Owner:** `initial_owner_guard`; the owner's first OIDC login
  auto-registers them. Finalisation (`container_script` after verification,
  using `bin/rails runner` with a script in `apps/dawarich/scripts/`) sets the
  owner as admin.
- **Demo account:** a `container_script` at `after_healthy` deletes or
  disables the seeded demo user **before the route goes live**, and a test
  proves `demo@dawarich.app/password` fails afterwards (directly and through
  the route).
- Email/password registration and login off once the owner is verified.
- Household members auto-register via OIDC as normal users; each person's
  location history is **private** to them (Dawarich default; VERIFY). Family
  sharing features, if Dawarich has them, are left to people's own choice.

### D4. Chat connector

**No connector in this task.** Location queries through chat would expose
one person's movements through an assistant that other household members
also use. Record in `notes/future.md`: "Dawarich chat only after
per-person connector credentials exist."

### D5. Phone clients (the core of this app)

Write numbered steps for each, all ending with checks:

1. **Tailscale always-on** first: iOS (Tailscale app → VPN On Demand /
   always-on; VERIFY the wording) and Android (Settings → Network → VPN →
   Tailscale → Always-on VPN). Explain the battery trade-off in one line.
2. **Dawarich iOS app** (official): server URL, API key from Dawarich
   settings.
3. **OwnTracks** (Android/iOS): Mode HTTP, URL
   `https://<host>:8465/api/v1/owntracks/points?api_key=<key>`. Explain that
   OwnTracks queues points while offline and sends them later.
4. **Overland** (iOS) and **GPSLogger** (Android) as alternatives.
5. **Import** history: Google Takeout (Timeline JSON), GPX, GeoJSON, with
   links to Dawarich's import docs.

### D6. Backups

Postgres and storage folders. Stop-snapshot-start (sidekiq included).
Location data is sensitive. Backups are encrypted by Kopia; the SFTP copy
goes only to the owner's own computer. Never add a cloud target for this app
without asking.

### D7. Edge cases

| # | Case | Handling |
|---|---|---|
| W1 | Phone offline for days | OwnTracks and the Dawarich app queue points; on reconnect they upload in bulk. Test a 5,000-point batch upload through the route (timeout/body size). |
| W2 | API key in a URL query string | It shows up in access logs. Make sure Caddy and Dawarich logs do not record query strings for `/api/v1/*` (VERIFY Caddy log settings; Mu3Lab may not log access at all), and that Mu3Lab job logs never include them. |
| W3 | Photon not ready (index still downloading) | Dawarich still records points; reverse geocoding retries later (VERIFY behaviour); the app page shows "Places will appear after the map index finishes". |
| W4 | Huge Google Takeout import | Runs in sidekiq; memory spikes. Note the RAM guidance; do not block install. |
| W5 | Time zones | `TIME_ZONE` from `${TZ}`. Points are stored in UTC. |
| W6 | Person removed | Authentik denies web access. **API keys keep working** for phone apps unless revoked. A `periodic` hook (or the per-person mechanism) disables the Dawarich user, or rotates their API key, when the person is removed. VERIFY a supported way; STOP if none. |
| W7 | Demo user reappears after an update (seed re-run) | The demo-removal script also runs on every app start (`after_healthy` each start; VERIFY the rule can run on start, not only install) and is idempotent. |

### D8. Acceptance (VM)

Photon: install with a small country (e.g. Luxembourg, `lu`), and reverse
geocode a known coordinate. Dawarich: standard journey; post 100 synthetic
points via the OwnTracks endpoint with an API key; they appear with place
names from Photon; the demo login fails; a second person cannot see the
first person's points; removing the second person stops their API key.
