# Frigate (task 5-12)

Camera recording (NVR) with local object detection. **The owner has no
cameras yet.** Build and test it with a simulated camera; Discover keeps
Install disabled until the owner enters a camera address
([../05-platform.md](../05-platform.md) §9).

## 1. VERIFY list

- Latest stable release (0.16.x or newer); images
  `ghcr.io/blakeblackshear/frigate:<ver>` (standard) and the NVIDIA variant
  (`<ver>-tensorrt`, or whichever tag supports NVIDIA ONNX/TensorRT in the
  pinned version); digests; licence (MIT).
- Whether Frigate **starts with zero cameras** (older versions required at
  least one camera in config). If it does, we could allow install without a
  camera; still keep the gate (owner decision: "install when you have
  cameras").
- Ports: 8971 (authenticated UI/API), 5000 (internal, unauthenticated; must
  **not** be published), 8554 (RTSP restream), 8555 (WebRTC tcp/udp), 1984
  (go2rtc API; internal).
- **Proxy authentication:** `auth: enabled: false` plus
  `proxy: header_map: {user: <header>, role: <header>}`,
  `proxy: separator`, `default_role`, and `role_map` (reported). Confirm
  which port honours the proxy headers (8971) and how a request without
  headers is treated. Confirm that `auth: enabled: false` does not disable
  the header-based role checks.
- Config file `/config/config.yml` (or `.yaml`), env substitution for
  secrets (`{FRIGATE_RTSP_PASSWORD}` style; VERIFY the variable prefix rules).
- Detectors on NVIDIA: `onnx` detector with a YOLO-family model (which
  models are bundled vs downloaded; licence of the model), or TensorRT.
  CPU detector for the VM.
- `shm_size` guidance per camera count/resolution; tmpfs for `/tmp/cache`.
- Hardware decode: `ffmpeg: hwaccel_args: preset-nvidia` (VERIFY name).
- MQTT config (`mqtt: host, port, user, password, topic_prefix`).
- Home Assistant integration: **Frigate's HA integration is a custom
  component** (HACS), not in HA core. That is open question **Q2**.
- Health: `/api/version` on 5000 internally, or on 8971 with headers.
- MCP servers for Frigate (search). Recommend none (see §6).

## 2. Services and storage

| Service | Image | Ports | Data / media |
|---|---|---|---|
| `frigate` | frigate (NVIDIA variant via overlay) | 8971 → `127.0.0.1:8971`; 8554 → `127.0.0.1:8554` (extra port) | `data/frigate/config` → `/config`; media `frigate` → `/media/frigate` (rw, recordings and clips, **excluded** from backups); tmpfs `/tmp/cache` (size per VERIFY) |

- `shm_size` from a formula based on camera count (VERIFY the formula in
  Frigate's docs); start with 256 MB for 1–2 cameras.
- GPU via overlay; `gpu: {typical_mb: 1500, idle_release: false}`. Frigate
  holds its detector model while running. That is expected; it is small.
- `depends_on: [mosquitto]`; `integrates_with: home_automation` (soft).
- `install_requires`: configuration `cameras` non-empty; hardware GPU
  `recommended`.
- Network: `mu3lab_frontend` (camera streams over the LAN need **egress to
  the LAN**: Docker bridge networks can reach LAN IPs by default; VERIFY on
  the host) and `mu3lab_mqtt`.

## 3. Camera configuration

- `configuration:` → **cameras**: a list (if the ConfigField model has no
  list type, add a `list` type in the platform first, in a small
  sub-task, or STOP) of `{name, rtsp_url, username, password(secret)}`.
- Mu3Lab renders `config.yml` from a template: one camera block each, using
  go2rtc restreams, detect at the sub-stream if provided, recording retention
  defaults (`record: retain: days: 7, mode: motion`; `alerts`/`detections`
  retention 14 days; VERIFY the 0.16 schema), object list
  `[person, car, dog, cat]`.
- Secrets never in `config.yml` plain text: use env substitution.
- **Ownership of `config.yml`:** Frigate's UI has a config editor. If the
  owner edits the config in Frigate, Mu3Lab must not overwrite it. Render
  `config.yml` **only on first install**. After that, camera changes in
  Mu3Lab's settings are written **only** if the file still has the hash Mu3Lab
  last wrote; otherwise show "Frigate's config was edited in Frigate; make
  camera changes there." Test both paths.

## 4. Access and roles

- `route.access: trusted_header`, identity header `X-Mu3lab-User`, role
  header via [../05-platform.md](../05-platform.md) §5.2: operators → `admin`,
  household → `viewer`.
- Frigate config: `auth: enabled: false`, `proxy.header_map.user:
  x-mu3lab-user`, `proxy.header_map.role: x-mu3lab-role` (or the groups
  header with `role_map`, per the 05 §5.2 decision), `default_role: viewer`.
- **Port 5000 is never published** (unauthenticated). The compose file must
  not expose it, and a test asserts that.
- The RTSP restream (8554) is loopback-only. Viewing in other apps (HA) goes
  through MQTT plus the Frigate API, or the tailnet URL.

## 5. Home Assistant link (soft dependency)

- Without Q2 approval: MQTT only. Frigate publishes events to Mosquitto; HA
  can use them with MQTT triggers in automations. The HA page explains this.
- With Q2 approval: install the Frigate custom integration into
  `/config/custom_components/frigate` at a **pinned release** (download URL
  plus SHA256 recorded), and add its config entry pointing at Frigate's
  internal URL. Note: HA is on the host network and reaches Frigate at
  `127.0.0.1:8971`, but that port expects proxy headers. VERIFY how the
  integration authenticates (it may need port 5000 or a Frigate user). Do
  **not** publish port 5000 to solve this; STOP if no safe path exists.

## 6. Chat connector

**None.** Camera events and snapshots of people at home are sensitive, and
the assistant is shared. Record in `notes/future.md`.

## 7. Phone clients

Web/PWA (Frigate's UI is mobile-friendly). HA Companion app shows Frigate
cameras if the HA integration is installed (Q2).

## 8. Backups

`data/frigate/config` (config and Frigate's database of events). Recordings
excluded (media). Stop-snapshot-start is acceptable (short gap in recording;
schedule only, `quiet_hours_only: true`).

## 9. Edge cases

| # | Case | Handling |
|---|---|---|
| F1 | Camera unreachable | Frigate shows the camera offline; the Mu3Lab app stays "running" with a warning from Frigate's stats API (VERIFY endpoint). |
| F2 | Disk filling with recordings | Retention days in settings; the app page shows the size of `media/frigate`. Frigate also deletes the oldest recordings when space is low (VERIFY). |
| F3 | Wrong RTSP credentials | Frigate logs auth errors; surface "Camera <name> rejected the username/password". |
| F4 | GPU absent | CPU detector; `install_requires` warns. |
| F5 | WebRTC over Tailscale | Default to MSE/HLS through the HTTPS route; WebRTC (8555/udp) not exposed in this plan. Note it as future work. |
| F6 | shm too small | Frigate logs a clear error; bump the formula; test 2 cameras at 1080p. |
| F7 | Config edited in Frigate | §3 hash rule. |

## 10. Acceptance (VM, no real camera)

Run a fake RTSP camera in the VM: a `mediamtx` container (pinned) plus an
`ffmpeg` container looping a short, freely licensed video with a person in it
(record its source and licence in the notes). Enter it as a camera; install;
an operator sees the live view and admin settings; a household member sees
the live view but **not** settings; a forged role header from another
device has no effect; a person-detection event reaches Mosquitto (subscribe
and see the message); port 5000 is not reachable from the host.
