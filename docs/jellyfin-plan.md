# Jellyfin integration plan

Status: built 2026-11-05 on `fix/app-file-updates` (uncommitted), not yet tried on the live install.
Verified against a throwaway Jellyfin 12.4 container: the setup wizard, accounts, roles, passwords,
libraries, the home-only filter (it refuses other addresses, and needs Docker's 10.210.0.0/16 listed
for Mu3Lab's own checks), per-person chat sign-in, signing out a person who leaves, all six chat tools,
and NVENC inside the image. Section 6 lists what is not built.

Jellyfin is the household's films and TV, on the TV, in the browser and on phones, **at home
only**. It sits next to Navidrome (music), Audiobookshelf (books, podcasts) and Immich (photos), and
follows their patterns: media libraries under the media root, shown in Nextcloud, fed by
qBittorrent, backed up, with an assistant in chat.

It is the first Mu3Lab app reached **on the home network instead of the tailnet**, so it needs a
small platform feature (section 3.2).

The recipe is [expansion/04-app-playbook.md](expansion/04-app-playbook.md), minus its fake-server
tests (deleted 2026-10-22) and VM acceptance (the owner tests live at the end).

---

## 1. What research found

| Topic | Finding | Effect on the plan |
|---|---|---|
| Release | Jellyfin dropped the "10." prefix: 10.11 → **12.0** (Sept 2026). Latest stable is **12.4** (26 Oct 2026). | Pin 12.4 by digest. VERIFY the Docker tag format for 12.x. |
| SSO | No built-in OIDC or trusted header. The main SSO plugin (9p4) is archived and forks compete; Authentik's guide now suggests LDAP. | Not used. Mu3Lab-made logins saved to the vault (D1). |
| Clients | Google TV / Android TV, Android, Swiftfin (iPhone, Apple TV), Findroid all sign in with username + password or **Quick Connect** (a code shown on the TV, approved from a signed-in phone). | Logins from the vault; TVs use Quick Connect. |
| Home network | Jellyfin's standard home setup is plain **http on port 8096**. Its Networking settings name the **Local networks** (CIDR) and can switch **remote access** off, so it refuses anyone outside them; each user also has "Allow remote connections". Discovery (UDP 7359) works only on the local subnet. | Bind to the home address on 8096, Jellyfin's own filter on, discovery off (D4). |
| This computer | Home address **192.168.68.50/22** from DHCP (router 192.168.68.1, looks like a TP-Link Deco mesh), on **Wi-Fi** (`wlp8s0`). `ufw` is active, but Docker-published ports bypass ufw. | DHCP reservation in the router; don't rely on ufw; Wi-Fi means transcoding may lower quality for 4K. |
| Ports | qBittorrent holds host loopback **8096** today; 8097 is free. | qBittorrent moves to 8097; Jellyfin takes 8096 (owner's choice). |
| GPU | NVENC/NVDEC on the RTX 3060 through the NVIDIA container runtime; set in Jellyfin's encoding settings. | `uses_gpu: true` + `docker-compose.nvidia.yml`, like Immich. |
| Backups | Jellyfin's databases are SQLite under `/config`; `ctl/database_dumps.py` finds SQLite files by header and copies them live. Media libraries are already one `media` Kopia source. | No new dump code. Leave out cache, transcodes and trickplay. Films and shows are backed up like every library. |
| Disk | Main drive (media lives here): 179 GB free. Storage drive (backups): 450 GB free. | With backups on, roughly **150 GB of films and shows** fits before a bigger drive is needed. Feeds the later resource-checks work. |

---

## 2. Owner decisions (2026-11-05)

- **D1 Sign-in:** Mu3Lab creates each person's Jellyfin account through its API, with a generated
  password saved to their vault (Navidrome's pattern). No SSO plugin. This is an exception to
  "Authentik-only" sign-in, like Navidrome.
- **D2 Libraries:** new media libraries `movies` and `shows`, visible to everyone in Nextcloud as
  `/Movies` and `/Shows`. Navidrome's music also shows in Jellyfin, read-only, when Navidrome is
  installed.
- **D3 Backups:** films and shows **are backed up**, like every media library.
- **D4 Reach: home network only.** No tailnet route, no Tailscale Serve, nothing outside the home.
  - Address: the owner **reserves 192.168.68.50 in the router** (DHCP reservation); Mu3Lab warns if
    the home address changes.
  - Discovery: **off**. People type `http://<home address>:8096` once into each TV or phone app.
  - Encryption: **plain http** on the home network.
  - The server **stays on Wi-Fi**; for 4K, Jellyfin lowers quality on the fly using the GPU.
- **D5 Roles:** Mu3Lab administrators are Jellyfin administrators (they can change Jellyfin's
  settings, libraries and users); everyone else is a normal user (watch only).
- **D6 Chat:** read-only, acting as each person, so Jellyfin's own permissions (libraries, ratings)
  apply to what chat shows. No permission changes through chat.
- **D7 GPU:** NVENC transcoding and tone mapping on NVIDIA machines. Resource checks come in a
  later session.
- **TV accounts:** each person adds their own profile on the TV with Quick Connect (own history and
  limits). No shared TV account.
- **Parental limits:** left to Jellyfin's Users screen; everyone starts seeing every library.
- **Existing media:** none; libraries start empty.
- **Ports:** Jellyfin on its standard **8096**; **qBittorrent moves to 8097**.

---

## 3. Build steps

### 3.1 Research notes

Write `docs/expansion/notes/jellyfin.md` from the pinned 12.4 release, with evidence for each VERIFY:
- Image tag and digest; amd64 published; licence GPL-2.0.
- Startup wizard API in 12.x (10.x: `POST /Startup/Configuration`, `GET`+`POST /Startup/User`,
  `POST /Startup/RemoteAccess`, `POST /Startup/Complete`); nothing else exists before it runs.
- User API (`POST /Users/New`, `POST /Users/{id}/Policy`, `POST /Users/{id}/Password`), API keys
  (`POST /Auth/Keys`), libraries (`POST /Library/VirtualFolders`), encoding
  (`POST /System/Configuration/encoding`), network (`POST /System/Configuration/network`: local
  networks, remote access off, UPnP off if still present, auto-discovery off).
- That remote access off really refuses a tailnet (100.x) client, and how Jellyfin sees the client's
  address behind Docker's port publishing (it must see the real address, not the bridge gateway).
- Quick Connect on by default; `GET /health` → `Healthy`; process user and writable folders.

### 3.2 Platform changes (before the app)

1. **Move qBittorrent to 8097.** `apps/qbittorrent/app.yaml` (`local_port: 8097`),
   `apps/qbittorrent/docker-compose.yml` (`127.0.0.1:8097:8479`), and the adapter's `APP_URL` in
   `apps/qbittorrent/connectors/qbittorrent-adapter/docker-compose.yml`. The installed qBittorrent
   must pick this up on the next `git pull` + re-render (Caddy upstream, health check, connector)
   without being reinstalled. VERIFY that a re-render moves a running app's port; if not, the
   update path does it.
2. **Home-network route** (a new manifest field, e.g. `route.access: home` with `home_port: 8096`):
   - No Caddy site and no `tailscale serve` for the app.
   - Publish the app port on the **home address only** (`<home address>:8096`), plus loopback for
     Mu3Lab's own health checks (`127.0.0.1:8096`). Never on `0.0.0.0` (that would also reach the
     tailnet and Docker networks).
   - A new fact `{{home_address}}`: the IPv4 address of the default-route interface (today
     `wlp8s0`, 192.168.68.50), and `{{home_networks}}` (192.168.68.0/22), detected by a small
     function next to `ctl/hostinfo.py`.
   - When the home address changes, Mu3Lab re-renders home-network apps and shows a notice on
     Home ("Your server's home address changed; reserve it in your router. TVs and phones need the
     new address: …"). A missing address (no network) leaves the app on loopback only.
   - The catalog check that refuses published ports allows exactly this case.
   - Dashboard: the Open button goes to `http://<home address>:8096` and says "Works at home only".
     The app page shows the address to type into TVs and phones.
   - Vault logins use that http address so Bitwarden fills them in the browser.
   - Settings > Sign-in lists the app as "Home network, Jellyfin login" (not Authentik).
   - Router guide: a short help panel on reserving the address (Deco app: More → Advanced → Address
     Reservation), shown on Jellyfin's page until the address has stayed the same for a while.
3. **qBittorrent categories:** add `movies` and `shows` to `LIBRARIES` in
   `apps/qbittorrent/start.py`, and to its assistant instructions and FAQ.

### 3.3 `apps/jellyfin/`

**`docker-compose.yml`**
- `name: mu3lab-jellyfin`; image pinned by digest.
- `ports: ["127.0.0.1:8096:8096", "${MU3LAB_HOME_ADDRESS}:8096:8096"]`. No 7359/udp, no DLNA, no 8920.
- Networks: `mu3lab_frontend` and `mu3lab_backend` (alias `jellyfin`) so the chat connector reaches it.
- Volumes: `…/jellyfin/config:/config`, `…/jellyfin/cache:/cache`,
  `${MU3LAB_MEDIA_MOVIES}:/media/movies:ro`, `${MU3LAB_MEDIA_SHOWS}:/media/shows:ro`, and Navidrome's
  music `:ro` through `integrates_with: music_library` (an empty stand-in folder when Navidrome is
  absent, as qBittorrent does). Read-only media means Jellyfin never deletes or rewrites people's files.
- `JELLYFIN_PublishedServerUrl: http://${MU3LAB_HOME_ADDRESS}:8096`; `TZ`; `no-new-privileges`;
  health check on `/health` with a `start_period` measured on first start.

**`docker-compose.nvidia.yml`**: GPU reservation with `capabilities: [gpu, video]`, as Immich's server.

**`app.yaml`**
- `id: jellyfin`, `category: media`, `capabilities: [videos, media_folders]`, `uses_gpu: true`,
  `service.local_port: 8096`, health `/health`.
- `media:` `movies` ("one folder per film, for example `Film Name (2010)/`") and `shows`
  ("`Show Name/Season 01/…`").
- `route`: the home-network kind from 3.2.
- `sign_in.method: local`, note: "Jellyfin works at home only and has its own login. Mu3Lab made
  yours and saved it to your vault."
- `account.mode: api_bootstrap`; `secrets`: `JELLYFIN_ADMIN_PASSWORD` (Mu3Lab's own
  `mu3lab-service` administrator); `rules: per_person_logins` (household).
- `backup.ignore`: `jellyfin/cache`, plus transcodes and trickplay folders under `/config` (VERIFY paths).
- `mobile:`
  - **Jellyfin for Google TV / Android TV** (official, primary for TVs): on the TV, open Google
    Play, install Jellyfin, choose Add server, type `http://<home address>:8096`, choose Quick
    Connect; on your phone open Jellyfin → your profile → Quick Connect, type the code. Each person
    repeats this to add their profile.
  - **Jellyfin** (Android, official), **Swiftfin** (iPhone, official), **Findroid** (Android,
    community, offline downloads): on home Wi-Fi, add the server address, sign in with the vault login.
  - Web player fallback. Caveat on every client: works only on the home network; downloaded
    films play anywhere.
- `chat.assistant` with FAQs: what is it for; how to add films (Nextcloud `/Movies`, `/Shows`, or
  qBittorrent); how to watch on the TV (Quick Connect); why it doesn't work away from home; who sees
  my watch history; why a film shows the wrong title (folder naming).
- `resource_guidance`: light when playing directly; converting video uses the GPU, or the CPU
  heavily without one.

**`hooks.py`** (modelled on `apps/navidrome/hooks.py`)
- `bootstrap_account`: wait for `/health`; if the wizard is not done, complete it through the API
  with `mu3lab-service` as the first administrator and the household's language and country; then
  create missing libraries (Movies, Shows, and Music when mounted); set Local networks to
  `{{home_networks}}`, remote access off, auto-discovery off, published URL; switch off usage
  reports; on NVIDIA builds set NVENC with hardware decoding and tone mapping. Idempotent.
- `sync_people`: create or update each household member with a generated password saved through
  `person_logins`; `IsAdministrator` follows the Mu3Lab role; "Allow remote connections" off for
  everyone; deactivated people get `IsDisabled: true` (history kept), their devices signed out and
  their saved login forgotten.
- `connector_credentials`: per-person access for the chat connector (3.4).

**Icon**: `jellyfin` in `dashboard/src/components/AppIcon.tsx` (for example lucide `Clapperboard`).

### 3.4 Chat connector `apps/jellyfin/connectors/jellyfin-adapter/`

- A `jellyfin` mode in `platform/mcp-adapter/server.py`, reaching Jellyfin over `mu3lab_backend`
  (unaffected by home-only access).
- Read-only tools: `search_media`, `recently_added`, `list_library`, `continue_watching`, `next_up`.
- `per_person: true`, so each person's tools run as them and Jellyfin's permissions and ratings
  limit the answers. VERIFY whether a Jellyfin API key can be tied to one user; if keys are
  server-wide, sign in as the person with their saved login and use that session token.
- `review.yaml` lists every tool. The main assistant learns Jellyfin from the manifest summary and FAQ.

### 3.5 Nextcloud, uninstall, updates

- Nextcloud: `/Movies` and `/Shows` appear for everyone through `media_folders`; nothing to build.
  Check a large upload (a 20 GB film) through Nextcloud in the browser.
- Uninstall never deletes `movies` or `shows`; deleting data removes Jellyfin's config, cache and the
  vault logins Mu3Lab made.
- `make approve` moves the one Jellyfin image; each release is approved only after a live try
  (database migrations are one-way; backup-first update and Undo cover it).

---

## 4. Edge cases

- Install interrupted after the wizard: the next run signs in as `mu3lab-service` and carries on.
- Home address changes (no reservation, router replaced): notice on Home, app re-rendered, TVs and
  phones need the new address.
- Server off the network or Wi-Fi down: Jellyfin stays on loopback; the app page says it isn't
  reachable at home right now.
- A tailnet device or a phone on mobile data: cannot connect (not published there, and Jellyfin
  refuses non-local addresses).
- Guests on the home Wi-Fi can reach the login page, but no account works without a password.
- Person removed: account disabled, TV and phone sessions signed out, vault login forgotten.
- Role change: next sync sets or clears administrator.
- No GPU: software transcoding works, slowly.
- Disk: films fill the main drive and their backup copies the storage drive; covered by the later
  resource checks.

---

## 5. Live checks for the owner

1. After `git pull`, qBittorrent still opens, its chat tools work, and it listens on 127.0.0.1:8097.
2. Install Jellyfin from Discover. Open (at home) goes to `http://192.168.68.50:8096`; the vault login
   fills in; Movies, Shows and Music exist; no setup wizard.
3. Nextcloud shows `/Movies` and `/Shows`; a film dropped there appears in Jellyfin after a scan.
4. qBittorrent offers `movies` and `shows` categories; a finished download lands in the library.
5. TCL Google TV: install Jellyfin, type the address, Quick Connect approved from the phone; a
   second person adds their own profile.
6. Phone on home Wi-Fi plays a film; the same phone on mobile data with Tailscale on cannot connect.
7. A 4K file that needs converting plays on the TV; `nvidia-smi` shows Jellyfin's `ffmpeg`.
8. A household member is a normal user; deactivating them signs out their TV and phone.
9. Chat: "what was added this week?" and "what am I in the middle of?" answer per person.
10. Back up now: Jellyfin's settings and the Movies and Shows libraries are in the backup.
11. Uninstall keeping data, reinstall: same libraries and watch history, no second administrator.

---

## 6. Built, and not built yet

Built: qBittorrent on 8097 with `movies`/`shows` categories; `listen: home` ports
(`${MU3LAB_HOME_IP}`), the `home_ip` / `home_network` facts and `ctl.service_state.home_address`;
the Open link, vault login and app page use `http://<home address>:8096` and say "at home only";
install skips the Authentik sign-in check for home-network apps; rule `home_network` re-renders and
restarts the app within a minute of the address changing (logged in the worker log and audit);
`apps/jellyfin` (compose, NVIDIA override, manifest, hooks); the `jellyfin` adapter mode and its
per-person connector (`person_connector_credentials` hook); the Jellyfin icon.

Not built:
- A Home-page notice when the home address changes (today: worker log and audit only).
- The router address-reservation help panel on Jellyfin's page.

Order on the live install: after `git pull`, the installed qBittorrent keeps 8096 until its update
(Mu3Lab's changes to its files) is applied from the dashboard. A successful update now refreshes the
app's private address too (`ctl/lifecycle/update.py`, `_refresh_route`), so qBittorrent's page follows
it to 8097. Apply that update **before** installing Jellyfin, or Jellyfin's install stops because 8096
is still taken.
