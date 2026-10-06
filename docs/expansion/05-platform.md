# 05. Platform changes (Phase 1)

Shared features the new apps need. Each section is one task branch (see
[README.md](README.md)). Every feature is **generic**: no app ID may appear
in `ctl/` (`tools/check_no_app_ids.py`). Apps opt in through `app.yaml`.

Before starting, read `ctl/manifest/models.py`, `ctl/manifest/catalog.py`,
`ctl/routes.py`, `ctl/engine/install.py`, `ctl/engine/project.py`,
`ctl/engine/hooks.py`, `ctl/rules/__init__.py`, `ctl/authentik_blueprints.py`,
`ctl/compute.py` and `ctl/status/host.py`.

For every new manifest field: add it to the Pydantic model (`extra="forbid"`
stays), give it a safe default so existing manifests still load, add
validation tests, and document it in a one-line comment.

---

## §2. Task 1-1: Port registry

**Already exists:** `ctl/manifest/catalog.py` rejects duplicate `local`,
`https` and `caddy` ports across apps.

**Add:**

1. Claim connector ports too. Parse `local_health` from each `connector.yaml`
   and claim `connector:<port>`. Today these are 8813–8823.
2. A new optional manifest field `service.extra_ports: tuple[ExtraPort, ...]`,
   where `ExtraPort = {port: Port, purpose: str, protocol: "tcp"|"udp" = "tcp"}`,
   for additional loopback-published ports (MQTT 1883, Wyoming 10300, Frigate
   RTSP 8554). Claim them as `local`, in the same namespace as
   `service.local_port`, because both are host loopback ports.
3. A test that the **compose file's** published host ports equal
   `{local_port} ∪ extra_ports` for every app. This catches a port added to
   compose but not to the manifest.
4. Preflight (`ctl/preflight.py`) checks that an app's ports are free on the
   host before install, with a clear message naming the process if possible.

**Allocation table.** Use exactly these. If one is taken when you start
(someone added an app meanwhile), take the next free value and update this
table in your task.

| App id | local_port (container port) | extra loopback ports | route https | caddy |
|---|---|---|---|---|
| `homebox` | 7745 (7745) | | 8460 | 19477 |
| `beaver-habits` | 8086 (8080) | | 8461 | 19478 |
| `audiobookshelf` | 13378 (80, VERIFY) | | 8462 | 19479 |
| `outline` | 3005 (3000) | | 8463 | 19480 |
| `romm` | 8091 (8080) | | 8464 | 19481 |
| `dawarich` | 3006 (3000) | | 8465 | 19482 |
| `n8n` | 5678 (5678) | | 8466 | 19483 |
| `comfyui` | 8188 (8188) | | 8467 | 19484 |
| `home-assistant` | 8123 (host network) | | 8468 | 19485 |
| `frigate` | 8971 (8971) | 8554 rtsp (tcp) | 8469 | 19486 |
| `kopia` | 51515 (51515) | | 8470 | 19487 |
| `open-webui` | 3212 (8080) | | 8471 | 19488 |
| `hermes-agent` | 9119 (9119 dashboard) | 8642 api | 8472 | 19489 |
| `speaches` | 8092 (8000) | | none (reached via LiteLLM) | |
| `wyoming-bridge` | 10300 (10300, tcp health) | | none | |
| `mosquitto` | 1883 (1883, tcp health) | | none | |
| `photon` | 2322 (2322) | | none | |

Connector `local_health` ports for new connectors: 8824 upward, in Phase 5
order.

Caddy's loopback range is 19460–19499 (`Route.caddy_port`). After this plan,
10 slots remain (19490–19499). Note that in the PR.

Acceptance: tests for 1–3; `make verify`.

---

## §3. Task 1-2: Media root

**Goal:** big, user-provided libraries live outside app data, are shared
safely, and are never deleted by uninstall.

1. Runtime path: `MU3LAB_MEDIA_ROOT`, default `/srv/mu3lab/media`, added to
   `ctl/runtime.py` `RuntimePaths` next to the data root. VERIFY how the data
   root is defined and mirror it. The installer creates the folder owned by
   the owner's user (the same user that owns `/srv/mu3lab/data`; VERIFY),
   mode `0775`.
2. New manifest field:

   ```yaml
   media:
     - name: audiobooks          # folder under the media root
       mount: /audiobooks        # path inside the container
       service: audiobookshelf   # compose service that mounts it
       mode: rw                  # ro | rw
       purpose: Your audiobook files, one folder per book.
       backup: false             # default; owner can opt in (see 06-kopia.md)
   ```

   The engine renders `${MU3LAB_MEDIA_ROOT}/<name>` into the project `.env` as
   `MU3LAB_MEDIA_<NAME>` (upper-cased, `-` → `_`). Compose files use
   `${MU3LAB_MEDIA_AUDIOBOOKS:?…}:/audiobooks`. Two apps may share a media
   name only if both declare it. The catalog validates that their modes are
   compatible: at most one `rw` unless both say `shared_rw: true`.
3. Install creates missing media folders with the owner's ownership. It never
   changes ownership of existing folders; it warns if the container user cannot
   read them (check with a short `docker run --rm` probe in the app's image as
   the app's user, or skip the probe and document it if that is impractical).
4. **Uninstall never deletes media folders**, even with "delete data". The
   dialog says so and shows the path. Add a test.
5. Dashboard: on the app's page (Advanced tab, VERIFY where data paths are
   shown today), show each media folder: path, purpose, mode, and a copy
   button. Get-started text tells the owner how to add files (file manager,
   `scp`, or Nextcloud; see below).
6. Note for later (not in this plan): exposing media folders inside Nextcloud
   as external storage. Record it as future work in
   `docs/expansion/notes/future.md`.

Acceptance: tests for model validation, env rendering, uninstall preservation;
`make verify`.

---

## §4. Task 1-3: GPU sharing (auto-yield)

**Facts:** RTX 3060, 12 GB. GPU users after this plan: Ollama (embeddings
only), Speaches (Whisper, Kokoro), ComfyUI (image generation), Frigate
(object detection, later). `ctl/compute.py` detects the GPU and selects
`docker-compose.nvidia.yml` overlays (see `apps/ollama/`).

**Owner decision:** auto-yield; no chat models in Ollama.

1. **Ollama (core):**
   - Change `OLLAMA_KEEP_ALIVE` from `24h` to `10m` (embeddings reload in
     about a second; ten minutes keeps bursts fast).
   - Add `OLLAMA_MAX_LOADED_MODELS: "1"` and `OLLAMA_NUM_PARALLEL: "2"`
     (VERIFY these names for the pinned Ollama version).
   - Audit for any chat model pull or route to Ollama:
     `grep -rn "ollama" ctl/rules/provider_routing.py apps/litellm apps/*/app.yaml`.
     Remove any chat-model route through Ollama. Update `apps/ollama/app.yaml`
     `summary` and `sign_in.note` ("Internal embedding runner; no chat models").
   - If the dashboard's AI settings offer local chat models, remove that
     offer and say why in the UI copy: "Local chat models are not used; the GPU
     is kept for speech and images."
2. **Per-app GPU metadata** (new optional manifest field):

   ```yaml
   service:
     uses_gpu: true
     gpu:
       typical_mb: 8000        # memory while busy, measured
       idle_release: true      # the app frees memory on its own when idle
       release_after_seconds: 300
   ```

3. **Observation:** in the worker's host observation (`ctl/status/host.py`),
   when an NVIDIA GPU is present, run
   `nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits`
   and `nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits`.
   Map each PID to a container by reading `/proc/<pid>/cgroup` for the
   container ID, then to an app via the Compose project label. If the PID is
   not visible (PID namespaces), show "other". Use a timeout; failure gives
   `gpu: unavailable`, never an exception. Page loads read the saved
   observation only, never run `nvidia-smi` (rule from
   [../development.md](../development.md)).
4. **Dashboard:** Settings → System shows a GPU card: total, used, and a list
   per app. On an app page whose manifest has `gpu.typical_mb`, show a warning
   when `free < typical_mb`: "<App> usually needs 8 GB of graphics memory;
   3 GB is free because <other app> is using it. It will be freed after a few
   minutes idle, or stop <other app>."
5. **Yield helpers:** apps that do not release on their own get a shared rule
   `gpu_release` (`ctl/rules/gpu_release.py`) with params
   `{url_path, method, body, idle_seconds}`. Its `periodic` hook calls the
   app's own unload endpoint when the app has been idle (VERIFY a reliable
   idle signal per app; for ComfyUI the queue endpoint reports running and
   pending jobs). Used by ComfyUI (`POST /free`). Speaches and Ollama use
   their own idle TTLs.
6. **Out-of-memory handling:** when an app job or chat tool fails with a CUDA
   out-of-memory signature (the strings are listed in each app spec), the
   user-facing message is "Not enough graphics memory right now — <apps>
   are using it. Try again in a few minutes or stop one of them." Code:
   `gpu_memory_busy`.
7. **No GPU (VM, other hardware):** every GPU app's spec gives its CPU
   fallback. The engine already chooses the overlay by compute mode; make
   sure GPU apps still install without the overlay, and that `uses_gpu` apps
   show "Running on CPU (slow)" when compute mode is CPU.

Acceptance: unit tests for the parser and PID mapping (fixtures of
`nvidia-smi` output); a dashboard test for the warning; `make verify`. On the
owner's host the GPU card is checked by the **owner** after merge (you must
not touch the running stack); give them the steps.

---

## §5. Task 1-4: Route features

Read `ctl/routes.py` `_block()` first. Add, each with tests of the rendered
Caddy text **and** a `caddy validate` run using the pinned Caddy image (as
the Grocy validation did):

1. **Identity value choice for trusted headers.** Today the trusted header
   always carries `X-Authentik-Username`. Add
   `route.trusted_value: "username" | "email" = "username"`. `email` uses
   `{http.request.header.X-Authentik-Email}` (VERIFY that the Authentik
   outpost sets `X-Authentik-Email` and that it is in the `copy_headers` list;
   add it automatically when `trusted_value` is `email`). Needed by Beaver
   Habits (VERIFY its header expects an email).
2. **Role header.** `route.role_header: {name: str, map: {group: value}, default: str, separator: ","}`.
   Caddy cannot map values by itself. Implement the mapping **in Authentik**
   instead: a per-app proxy-provider property mapping that emits
   `X-Mu3lab-Role: admin|viewer` computed from group membership. VERIFY how
   proxy providers expose custom headers (Authentik "proxy provider property
   mappings" with `ak_proxy` / additional headers; check the docs of the
   pinned Authentik version). Caddy then copies that header and **replaces**
   any client-supplied value, exactly like the identity header. Needed by
   Frigate. If Authentik cannot emit a custom header, fall back to copying
   `X-Authentik-Groups` and let the app map groups (Frigate's `role_map`
   supports this). Record the choice.
3. **Gate-exempt paths** for app-authenticated endpoints on a gated route:
   `route.app_auth_paths: tuple[str, ...]`. Requests to these paths skip
   Authentik **but** have identity headers stripped. Use only when the app
   itself requires authentication on that path. Every use needs a comment in
   `app.yaml` saying which app mechanism authenticates it. Needed by n8n
   webhooks (n8n header auth) and the LiteLLM `/v1/*` API (key auth; see
   [08-speech.md](08-speech.md)). Prefer the existing `token_bypass` when the
   client sends a fixed header; use `app_auth_paths` only when it cannot.
4. **Streaming and long requests.** `route.streaming: bool = False` adds
   `flush_interval -1` to the `reverse_proxy` block (SSE / streamed chat /
   MCP) and `route.timeout_seconds` for long uploads. VERIFY Caddy defaults
   first; only add what testing shows is needed.
5. **WebSockets:** Caddy proxies them by default. Add a test helper in
   `tests/` that asserts nothing in a generated block disables them. VERIFY
   through Tailscale Serve in the VM during app acceptance (n8n, HA, ComfyUI,
   Open WebUI, Outline).

Acceptance: rendered-block tests for each feature; `caddy validate` passes;
`make verify`.

---

## §6. Task 1-5: Soft dependencies (wiring between apps)

**Need:** some apps work better when another app is present, but must not
require it. Open WebUI uses ComfyUI for images and Speaches for voice; Home
Assistant uses the speech bridge and Mosquitto; Frigate uses Mosquitto (hard)
and Home Assistant (soft). Hard dependencies already exist (`depends_on`).

1. New capabilities (strings in each provider's `capabilities:`):
   `speech` (Speaches), `speech_wyoming` (Wyoming bridge), `geocoder`
   (Photon), `mqtt_broker` (Mosquitto), `image_generation` (ComfyUI),
   `home_automation` (Home Assistant), `backup_server` (Kopia UI). Follow
   `by_capability()` usage; VERIFY its location and semantics.
2. New manifest field on the consumer:

   ```yaml
   integrates_with:
     - capability: image_generation
       env:                                # rendered only when a provider is installed
         COMFYUI_BASE_URL: 'http://{{provider.internal_host}}:{{provider.internal_port}}'
         ENABLE_IMAGE_GENERATION: 'true'
       absent_env:                         # rendered when no provider is installed
         ENABLE_IMAGE_GENERATION: 'false'
       rule: ''                            # optional rule name to run after wiring (e.g. create an HA config entry)
   ```

   Define the provider template variables (`internal_host` = compose network
   alias, `internal_port` = container port) in `ctl/engine/template.py`.
   VERIFY the existing template syntax and reuse it.
3. **Rewiring:** when a provider is installed, removed, started or stopped,
   enqueue one durable `rewire` job per installed consumer. It re-renders the
   consumer's `.env`. If anything changed, it restarts only the affected
   services and runs the optional `rule`. It never runs during the consumer's
   own install job (the install renders the current state itself). Rewire
   jobs are idempotent and coalesce (one pending job per consumer).
4. **Credentials between apps** (e.g. Frigate's MQTT password) use the
   provider's provisioning: the provider exposes a `provision_client` script
   that creates or returns a per-consumer credential, stored in the consumer's
   secret scope. Never share one password between consumers.
5. Dashboard: the consumer's page shows "Works with: ComfyUI (installed ✓),
   Speech (not installed — Install)".

Acceptance: unit tests for rendering present/absent, rewire coalescing,
restart only on change; `make verify`.

---

## §7. Task 1-6: Per-person local accounts

**Need:** Home Assistant has no SSO. The decision is that Mu3Lab creates
**each person's** HA account through HA's official API and saves the login to
**that person's** vault. Build it generically.

1. New rule `per_person_accounts` (`ctl/rules/per_person_accounts.py`) with
   params:

   ```yaml
   - rule: per_person_accounts
     with:
       audience: household          # household | operators
       admin_for: operators         # who gets the app's admin role
       create: { hook: create_person }   # method name in apps/<id>/hooks.py
       disable: { hook: disable_person }
       set_role: { hook: set_person_role }
       login_url_path: /            # for the vault item URL (port included)
   ```

   The app's `hooks.py` implements `create_person(ctx, person, password, admin) -> app_user_id`,
   `disable_person(ctx, app_user_id)` and `set_person_role(ctx, app_user_id, admin)`
   using the app's official API. The rule owns everything else.
2. **When it runs:** `after_healthy` at install (for every current person
   with access), and in `periodic` (every worker cycle, cheap when nothing
   changed). It diffs Authentik people (`ctl/people.py`) against a stored
   mapping `{authentik_uid → app_user_id, role}` in the encrypted onboarding
   state (`ctl/onboarding_state.py`; VERIFY its API for adding a record
   type).
3. **Passwords:** generated (32+ chars), persisted encrypted **before**
   creating the account (the same crash-safety as owner logins), queued as
   that person's pending vault login. VERIFY that `vault_sync.items_for()`
   already includes `onboarding_state.pending_logins(uid)` for any person,
   not only the owner (it reads `pending_logins(uid, paths)`, which looks
   right). Delete the durable copy after the vault save succeeds (existing
   behaviour).
4. **Removed person:** disable, don't delete (keeps history, reversible).
   **Role change:** `set_person_role`. **Re-added person:** re-enable and
   rotate the password into their vault.
5. **Failures** for one person never block others. Each failure is a
   per-person status visible to operators ("Home Assistant account for Sam
   could not be created: <reason>"), retried next cycle with backoff.

Acceptance: unit tests with a fake hooks object: create, idempotent re-run,
remove → disable, role change, crash between persist and create; `make verify`.

---

## §8. Task 1-7: Host networking and devices

**Need:** Home Assistant should use the host network for device discovery
(mDNS/SSDP: Chromecast, Sonos, printers, ESPHome). Later Zigbee needs USB
devices.

1. Allow `network_mode: host` for a service when the manifest declares
   `service.host_network: true`. `tools/validate_compose.py` must reject host
   networking anywhere else.
2. A host-network app **must bind to loopback** for its web port. Each such
   app spec says how (HA: `http: server_host`, VERIFY). Add an install-time
   check: after health passes, run `ss -ltnp` (or read `/proc/net/tcp`) and
   fail with `host_port_exposed` if the app's `local_port` listens on a
   non-loopback address. Explain in plain words why: "it would be reachable
   from your home network without Mu3Lab's protection".
3. Caddy reaches it at `127.0.0.1:<local_port>` exactly as today.
4. Docker-network peers (Mosquitto, the Wyoming bridge) are reached from a
   host-network app through their **loopback-published** ports
   (`extra_ports` / `local_port`). Containers on Docker networks reach the
   host-network app through `host.docker.internal` with
   `extra_hosts: ["host.docker.internal:host-gateway"]` (VERIFY on the Docker
   version the installer pins), **but** only if the app also listens there.
   Since it binds loopback only, design so that **host-network apps call
   others, not the other way round**. Frigate → HA communication goes through
   MQTT, which satisfies this.
5. Devices: `service.devices: tuple[Device, ...]` with
   `{host_path, container_path, purpose, optional}`. Rendered only if the host
   path exists. A missing optional device gives a warning, never a failure.
   Nothing in this plan uses it yet except Frigate's optional Coral; it is
   built now for Zigbee later.

Acceptance: validation tests; a loopback-check unit test with fixture
`/proc/net/tcp` contents; `make verify`.

---

## §9. Task 1-8: Install gates (prerequisites shown in Discover)

**Need:** Frigate is useless without a camera. Some apps need a GPU to be
practical.

1. New manifest field:

   ```yaml
   install_requires:
     - kind: configuration           # a configuration field must be filled first
       key: first_camera_url
       message: Add at least one camera's RTSP address before installing Frigate.
     - kind: hardware
       gpu: recommended              # required | recommended
       message: Runs best with an NVIDIA graphics card; without one it is very slow.
   ```

2. Discover shows the message and keeps Install disabled until each
   `required` condition holds. `recommended` shows a warning but allows
   install.
3. The API enforces the same check; the UI is not the only guard. Error code
   `install_requirement_missing`.

Acceptance: API and UI tests; `make api-schema`; `make verify`.

---

## §10. Task 1-9: OIDC extras

Read `ctl/authentik_blueprints.py` `render_oidc_blueprint` first.

1. **Custom-scheme redirect URIs** for phone apps. Today `redirect_paths`
   must start with `/` and are joined to the app's origin. Add
   `sign_in.oidc.extra_redirect_uris: tuple[str, ...]`, validated as
   `^[a-z][a-z0-9+.-]*://[^\s]+$` and **not** `http:` or `https:` (absolute
   web URLs stay origin-bound). Render them with `matching_mode: strict`.
   Needed by Audiobookshelf (`audiobookshelf://oauth`, VERIFY), and possibly
   Home Assistant later.
2. **Per-app role claims.** `sign_in.oidc.role_claim: {claim: str, map: {mu3lab-operators: admin, mu3lab-users: user}, multi: true}`.
   Render a per-app scope mapping `mu3lab-<app>-roles` that emits `claim` as
   a list (or a single value when `multi: false`) computed from the user's
   groups, and add it to the provider's `property_mappings`. The scope name
   is added to the app's requested scopes via its env (each app spec gives
   the env name). Python expression must handle users in neither group
   (emit nothing; the app's default role applies).
3. **Tests:** blueprint rendering for both features, and the existing
   Authentik blueprint tests updated. Apply in the VM and inspect the token
   claims (Authentik's provider "Preview" tab, or decode an ID token from a
   test login) and record them.
4. Remember the lesson in [../rebuild-handoff.md](../rebuild-handoff.md) and the
   SSO memory: Mu3Lab applies blueprints itself (`ctl/authentik_apply.py`),
   in a known order. Do not rely on the blueprint file watcher.
