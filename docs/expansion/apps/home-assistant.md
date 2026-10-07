# Home Assistant (task 5-11) and Mosquitto (task 5-10)

Home automation hub. **Container edition** (not HA OS). Owner decisions:
tailnet-only route **without** an Authentik gate; each person gets an HA
login created by Mu3Lab through HA's official API and saved to **their**
vault, so the **Companion phone app** works (location, notifications,
widgets).

---

## Mosquitto (task 5-10, do first)

### M1. VERIFY list

- `eclipse-mosquitto:<2.x>` latest stable, digest, licence (EPL/EDL).
- Config: `listener 1883`, `allow_anonymous false`, `password_file`,
  `persistence true`, `persistence_location /mosquitto/data/`.
- Generating password-file entries: `mosquitto_passwd -b <file> <user> <pass>`
  inside the image (VERIFY it exists in the image).
- Health: a TCP check, or `mosquitto_sub -t '$SYS/broker/uptime' -C 1` with a
  health user.

### M2. Design

- App folder `apps/mosquitto/`, `tier: optional`, `group: infrastructure`,
  capability `mqtt_broker`, no route, `local_port: 1883`, `health: {kind: tcp}`.
  Published on `127.0.0.1:1883` only (HA on the host network connects there).
  Also on an internal Docker network `mu3lab_mqtt` for container clients
  (Frigate). Never on the LAN.
- **Per-client credentials** ([../05-platform.md](../05-platform.md) §6.4): a
  `provision_client` script adds or returns a user for each consumer
  (`homeassistant`, `frigate`, later `zigbee2mqtt`) with a random password,
  writes the password file, and sends `SIGHUP` to reload (VERIFY reload
  behaviour). Passwords stored in each consumer's secret scope.
- ACLs (optional now): an `acl_file` restricting topics per user is good
  practice. Start with Frigate → `frigate/#` (read/write) and HA → `#`
  (read/write). Record it.
- Shown in Discover under Infrastructure, installed automatically when HA
  or Frigate is installed (`depends_on`).
- Backups: `data/mosquitto/data` (retained messages; small) and config.

---

## Home Assistant (task 5-11)

### H1. VERIFY list

- Release: `ghcr.io/home-assistant/home-assistant:<YYYY.M.patch>`; digest;
  licence (Apache-2.0). HA releases monthly with breaking changes; read the
  release notes of the version you pin.
- **Host network** and binding to loopback: the `http:` options
  `server_host` (VERIFY: may be deprecated in recent versions; find the
  current way to bind the frontend to `127.0.0.1` only). If HA **cannot**
  bind loopback-only on the host network: STOP and ask the owner, with these
  options: (a) bridge network on loopback port publish (loses mDNS/SSDP
  discovery; devices added by IP), (b) host network listening on the LAN
  with HA's own auth (LAN devices could reach the login page). Recommend (a).
- Reverse proxy: `http: use_x_forwarded_for: true`,
  `trusted_proxies: [127.0.0.1, ::1]`.
- `homeassistant: external_url / internal_url` (both the tailnet HTTPS URL).
- **Onboarding API** (what the frontend calls): `POST /api/onboarding/users`
  (`client_id`, `name`, `username`, `password`, `language`) → `auth_code`;
  `POST /auth/token` (grant `authorization_code`) → tokens;
  `POST /api/onboarding/core_config`; `POST /api/onboarding/analytics`;
  `POST /api/onboarding/integration` (`client_id`, `redirect_uri`). Confirm
  each for the pinned version, and the "already onboarded" responses.
- **User management** via WebSocket (admin token): `config/auth/create`
  (name, group_ids `system-admin`/`system-users`, local_only),
  `config/auth_provider/homeassistant/create` (user_id, username, password),
  `config/auth/update` (is_active, group_ids), and
  `config/auth_provider/homeassistant/admin_change_password`. VERIFY exact
  command names and payloads.
- **Long-lived access token** creation via WebSocket
  `auth/long_lived_access_token` (client_name, lifespan).
- **MCP server integration** (`mcp_server`, in HA core): how to add its config
  entry by API (config flow), endpoint (`/api/mcp`), transport (SSE vs
  Streamable HTTP; the gateway needs Streamable HTTP. If SSE only, check
  `platform/mcp-adapter/server.py` as a bridge, else STOP), auth (long-lived
  token), and how "exposed entities" control what it can see.
- MQTT integration config flow (broker, port, username, password) by API.
- Wyoming integration config flow (host, port) and Assist pipeline
  WebSocket commands to set STT/TTS engines.
- Companion app: connection URL behaviour, how it authenticates (HA login in
  a webview), whether it needs anything beyond the external URL.
- Health: `/manifest.json` or `/api/` (401 without a token is "up"; VERIFY a
  good unauthenticated probe).
- Bluetooth: needs `/run/dbus` mounted read-only and capabilities
  (`NET_ADMIN`, `NET_RAW`)? Optional; default **off**, documented.

### H2. Services and files

| Service | Image | Network | Data |
|---|---|---|---|
| `homeassistant` | home-assistant | `network_mode: host` (`service.host_network: true`) | `data/home-assistant/config` → `/config` |

- `local_port: 8123`. Caddy reaches `127.0.0.1:8123`.
- `cap_drop: ALL` may break HA (it runs pip installs for integrations at
  runtime). Test carefully. Start with the defaults plus
  `no-new-privileges`, then drop what testing shows is unused. Record the
  final set.
- **Configuration ownership:** Mu3Lab must not overwrite the owner's
  `configuration.yaml`. Use HA **packages**: ensure `configuration.yaml`
  contains `homeassistant: packages: !include_dir_named packages` (insert
  once, idempotently, with a comment), and own `packages/mu3lab.yaml` fully
  (http proxy settings, URLs). If the owner's file already defines
  `homeassistant:` or `http:` keys, merging rules apply. VERIFY HA's package
  merge rules for `http:` (it may not be mergeable via packages; then write
  `http:` into `configuration.yaml` once and never touch it again, and test
  that HA rejects a duplicate key with a clear error the owner can fix).

### H3. Install sequence

1. Ensure Mosquitto (dependency) is running; get HA's MQTT credentials.
2. Render config files (packages); start HA; wait for health (first start
   takes minutes: `start_timeout_seconds: 600`).
3. Loopback check ([../05-platform.md](../05-platform.md) §8.2): port 8123 must
   not listen on non-loopback addresses.
4. **Onboarding** in `apps/home-assistant/hooks.py` (`after_healthy`):
   - If not onboarded: create the **owner** via `/api/onboarding/users` with
     the owner's Authentik username, display name and a random password
     (persisted encrypted first), exchange the auth code for tokens, finish
     `core_config` (location: leave unset or use the host time zone only;
     **never** guess the home location; country/currency/units from the host
     locale), `analytics` (all off), `integration`.
   - Create a **long-lived token** "Mu3Lab" for Mu3Lab's own use (stored in
     `SecretStore`, scope `home-assistant`). Every later API call uses it.
   - If already onboarded (reinstall with kept data): use the stored token;
     if missing or invalid, STOP the install with `ha_token_missing` and the
     plain message "Mu3Lab lost access to Home Assistant. <recovery steps>".
     Recovery: the owner logs in (password in their vault) and creates a
     long-lived token; the dashboard offers one paste box for it. This is
     the only manual recovery path; never reset HA users.
5. Owner login → owner's **vault** (pending login, URL with port 8468).
6. **Household accounts** via the `per_person_accounts` rule
   ([../05-platform.md](../05-platform.md) §7) with `hooks.py` implementing
   `create_person` (WebSocket create user + credentials; group
   `system-admin` for operators, `system-users` for household),
   `disable_person` (`is_active: false`), `set_person_role`.
7. Integrations by config flow: MQTT (Mosquitto at `127.0.0.1:1883`), MCP
   server, Wyoming (if `speech_wyoming` installed, via `integrates_with`
   rule).
8. Connector provisioning (§H5).

### H4. Route and sign-in

- `route.access: open` (no gate). `sign_in.method: local`,
  `account.mode: api_bootstrap`, `save_login_to_vault: true`.
- Recommend that people enable HA's **two-factor (TOTP)** on their profile;
  the HA page says how. (Do not force it; the Companion app supports it.)
- `redirects`: none (HA's login page is the real login).
- `sign_in.note`: "Home Assistant has its own login so the phone app can
  connect. Mu3Lab created yours and saved it in your vault; Bitwarden fills it in."
- `signin_check` adaptation: for `local` sign-in, verify that the login page
  loads through the route and that the owner's credentials authenticate via
  `/auth/login_flow` (VERIFY the flow API). Without this, install would
  claim success with broken sign-in.

### H5. Chat connector

- HA's official MCP server: provenance `official`, credential = a
  **dedicated** long-lived token for a dedicated non-admin HA user
  "Mu3Lab Assistant" (created via WebSocket, group `system-users`). Avoid
  the owner's admin token. VERIFY that a non-admin user's token can use the
  MCP server.
- **What the assistant can control** = HA's "exposed entities" (Settings →
  Voice assistants → Expose). Default: HA's defaults. The HA page explains how
  to expose more.
- Review: HA's MCP tools are Assist intents (turn on/off, set, get state;
  VERIFY the list). Reading state = read; any intent that changes a device =
  write (off by default, approval each call). Block anything that edits
  automations or config, if offered.

### H6. Voice (Assist)

- With `speech_wyoming` installed: Wyoming integration →
  `127.0.0.1:10300`; set the default pipeline's STT and TTS to it, language
  from config.
- **Conversation agent:** HA's built-in local intent agent (no LLM) by
  default. An LLM conversation agent is **open question Q1**: Ollama has no
  chat model by decision, and HA's OpenAI integration may not accept a
  custom base URL. Ask the owner; do not build it.
- Phones: the Companion app's Assist button uses this pipeline.

### H7. Phone clients

- **Home Assistant Companion** (official, iOS and Android):
  1. Connect Tailscale and set it to always-on (location and notifications
     need it).
  2. Install the Companion app.
  3. Enter `https://<host>:8468` manually (auto-discovery won't find it over
     the tailnet).
  4. Log in: Bitwarden fills your username and password (in your vault as
     "Home Assistant").
  5. Allow location and notifications if you want presence-based automations.
- Notifications use HA's official push relay over the internet (HA → Nabu
  Casa's free notification service → Apple/Google). Say so plainly
  (privacy note: notification text passes through that relay).
- Web fallback: PWA.

### H8. Backups

`data/home-assistant/config`, excluding `backups/**` (HA's own backup
files), `tts/**` (cache), `*.log*`, and `.cache`. The recorder database
(`home-assistant_v2.db`) is included. `backup.quiet_hours_only: true`:
stopping HA pauses automations ([../06-kopia.md](../06-kopia.md) §6.7).

### H9. Updates

Monthly releases with breaking changes. Approval checklist in the notes:
read the "Backward-incompatible changes" section; test onboarding API,
user management, MCP and Wyoming in the VM before approving.

### H10. Edge cases

| # | Case | Handling |
|---|---|---|
| HA1 | Discovery protocols on host network expose ports (e.g. HomeKit bridge 21063, Sonos callbacks) | These are opened only when the owner adds such integrations. The HA page notes that some integrations open their own ports on the home network. |
| HA2 | Owner edits `configuration.yaml` and breaks it | HA fails to start → app degraded with HA's error line from the logs, and a link to HA's config check. Mu3Lab never "fixes" the owner's YAML. |
| HA3 | Person removed | Disabled in HA (not deleted); their Companion app session stops working (VERIFY refresh tokens are revoked on deactivation; if not, also revoke them via WebSocket). |
| HA4 | Person's vault entry deleted by them | Their HA password is unknown; an operator action **Reset Home Assistant login for <person>** sets a new password into their vault. This is not a "Repair sign-in" button: it is account management for a non-SSO app. |
| HA5 | Integrations installing Python packages at runtime | Needs egress and a writable `/config/deps` (default). Include `deps` in backup excludes (regenerable). |
| HA6 | USB radios later (Zigbee) | `service.devices` ([../05-platform.md](../05-platform.md) §8.5), when the owner buys one. |
| HA7 | Time zone and location | Time zone from the host; home location left unset (the owner sets it in HA). |
| HA8 | n8n/other services calling HA | Through `127.0.0.1:8123` from host-network peers only; Docker-network containers use MQTT or the tailnet URL with a token. |

### H11. Acceptance (VM)

Standard journey adjusted for local sign-in: the owner's HA login is in the
vault and works through the route; a household member gets a non-admin
login in **their** vault; a removed member cannot log in; the MQTT
integration is connected (publish a test message, see it in HA); the MCP
connector lists exposed entities (use a template/demo entity); port 8123 is
not listening on the VM's LAN address. Phone Companion app: owner-run steps.
