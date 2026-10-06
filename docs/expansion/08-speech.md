# 08. Speech for everything: Whisper and Piper (Phase 4)

## 1. Goal (owner's words, restated)

One local speech service that **any** program can use:

- Home Assistant voice (Assist).
- The chat app (Open WebUI or LobeChat): voice input and read-aloud.
- Other apps on the server.
- **Programs outside the stack:** for example, dictating into Google Docs or
  LibreOffice on a laptop, or a phone, using the server's Whisper. "If I
  install Mu3Lab and only Whisper, I can use it anywhere."

Audio never leaves the server. Ollama is **not** involved (it cannot run
Whisper, and per [01-decisions.md](01-decisions.md) §2.5 it runs no chat
models).

## 2. Architecture

```text
                    ┌──────────────────────── LiteLLM (core, already installed) ───────────────────────┐
 laptop/phone app ──┤ https://<host>:8454/v1/audio/transcriptions   model "mu3lab-stt"               │
 (personal key)     │ https://<host>:8454/v1/audio/speech           model "mu3lab-tts"               ├──► Speaches (GPU/CPU)
 Open WebUI ────────┤ http://litellm:4000/v1/...  (service key)                                       │     faster-whisper (STT)
 LobeChat ──────────┤                                                                                   │     Kokoro / Piper (TTS)
 other apps ────────┤                                                                                   │
 Wyoming bridge ────┘                                                                                   │
      ▲                                                                                                 │
 Home Assistant (host network) ── tcp 127.0.0.1:10300 (Wyoming) ─────────────────────────────────────────┘
```

Why everything goes through LiteLLM:

- **One stable name** per capability (`mu3lab-stt`, `mu3lab-tts`). Swapping
  the engine or model later changes nothing for clients.
- **Per-person keys**, revocation, rate limits and usage, which LiteLLM
  already does.
- **One authenticated public door** (LiteLLM's route) instead of exposing
  Speaches.

Speaches itself is reachable only on the internal network from LiteLLM.

## 3. Task 4-1: Speaches app

### 3.1 VERIFY list

- Project `speaches-ai/speaches`: latest stable release, image names for
  CUDA and CPU variants (`ghcr.io/speaches-ai/speaches:<ver>-cuda` and
  `-cpu`, VERIFY), digests, licence (MIT reported).
- Internal port (8000), health endpoint (`/health`).
- OpenAI-compatible endpoints: `/v1/audio/transcriptions`,
  `/v1/audio/translations`, `/v1/audio/speech`, `/v1/models`.
- Model management API (download and list models; for example
  `POST /v1/models/{id}` and `GET /v1/registry`). Model IDs for:
  Whisper large-v3-turbo (GPU), a small Whisper (CPU), Kokoro (TTS) and
  Piper voices.
- Idle unload settings (model TTL env names) and their defaults.
- Whether it supports an API key (if yes, set one; LiteLLM holds it).
- The Hugging Face cache path inside the container.
- Voices: which `voice` values each TTS model accepts, and what happens when
  a client sends an OpenAI voice name such as `alloy`.

### 3.2 Design

- App folder `apps/speaches/`. `tier: optional`, `group: ai`,
  `category: ai`, capabilities `[speech]`, `service.uses_gpu: true`,
  `gpu: {typical_mb: 3000, idle_release: true, release_after_seconds: 300}`
  (measure and correct `typical_mb`).
- Compose: base file uses the **CPU** image. `docker-compose.nvidia.yml`
  swaps the image to the CUDA variant and adds the GPU reservation (VERIFY
  that a Compose override can replace `image:`; it can). Loopback port
  `127.0.0.1:8092:8000` for health only. Network: an internal network shared
  with LiteLLM only (e.g. `mu3lab_speech`), plus whatever lets it download
  models (egress on `mu3lab_frontend`; VERIFY which network gives egress
  today).
- Data: `data/speaches/hf-cache` (models). **Excluded from backups**
  (`backup.exclude: ["**"]` or no data folder in backups; models can be
  downloaded again). Note the size in `resource_guidance`.
- `configuration:` fields:
  - `language` (default from host locale, e.g. `en`): used for the default
    Piper voice and the HA pipeline.
  - `stt_quality`: `fast` | `best` (default `best` on GPU, `fast` on CPU).
  - `tts_engine`: `kokoro` | `piper` (default `kokoro` if its language is
    supported, else `piper`).
- **Model download at install:** a generic rule `needs_models` (rename and
  generalise `ctl/rules/needs_embedding_model.py` if it fits; keep the old
  rule name working for Ollama, or migrate Ollama's manifest in the same
  task). It downloads the configured models through the app's own API,
  shows progress in the job, retries with backoff, and verifies with one
  real request (transcribe a bundled 2-second WAV in the app folder; speak
  "Mu3Lab ready"). Download failures fail the install with
  `speech_model_download_failed` and a plain message (disk, network).
- Idle unload via Speaches's TTL env (300 s).
- No route, no assistant, no connector.
- `mobile:` none here; device instructions live on the Voice key page (§5).

## 4. Task 4-2 (part 1): LiteLLM speech routes

1. LiteLLM becomes a **consumer** of capability `speech` through
   `integrates_with` ([05-platform.md](05-platform.md) §6) with
   `rule: provider_routing` (VERIFY that re-running the rule regenerates
   `config.yaml` and restarts LiteLLM safely).
2. In `ctl/rules/provider_routing.py` (mode `models`), when a speech provider
   is installed, add to `model_list` (VERIFY the LiteLLM config schema for
   audio models in the pinned version):

   ```yaml
   - model_name: mu3lab-stt
     litellm_params: {model: openai/<speaches-stt-model-id>, api_base: http://speaches:8000/v1, api_key: os.environ/SPEACHES_API_KEY}
     model_info: {mode: audio_transcription}
   - model_name: mu3lab-tts
     litellm_params: {model: openai/<speaches-tts-model-id>, api_base: http://speaches:8000/v1, api_key: os.environ/SPEACHES_API_KEY}
     model_info: {mode: audio_speech}
   ```

   Model IDs come from the Speaches app's configuration (rendered into
   LiteLLM's env by the soft-dependency wiring). No app ID appears in
   `ctl/`: the provider is found by capability, and its internal host and
   port come from its manifest.
3. **Voices:** if Speaches rejects OpenAI voice names, configure a default
   voice in Speaches (if supported), or document per-client voice names on
   the Voice page. Do not build a voice-translation proxy unless the owner
   asks.
4. **Privacy:** make sure LiteLLM does not store request or response bodies
   for audio (VERIFY settings such as `store_prompts_in_spend_logs` and
   turn them off for these models, or globally if already off).
5. When the speech provider is removed, the routes disappear on rewire.
   Clients get a plain "speech is not installed" error from LiteLLM.

## 5. Task 4-2 (part 2): Personal voice keys and use outside the stack

### 5.1 LiteLLM route

LiteLLM's route today is `access: gate`, `audience: operators`. Add
`token_bypass: {path: /v1/*, header: Authorization, header_prefix: "Bearer "}`.
Requests with a bearer key skip Authentik; LiteLLM checks the key. Requests
without one still hit the gate (the admin UI stays operator-only). Test both
paths. VERIFY that no LiteLLM admin endpoint lives under `/v1/` that a
non-admin virtual key could reach (key management is under `/key/*`, which
stays gated).

### 5.2 Keys

- Dashboard **Settings → My voice & AI key** (every person, not only
  operators):
  - **Create key:** Mu3Lab calls LiteLLM `/key/generate` with the master key:
    `models: [mu3lab-stt, mu3lab-tts]`, `user_id: <authentik uid>`,
    `key_alias: "<name> voice key"`, rate limit (e.g. 60 rpm), no expiry.
    The key is shown **once** with a copy button. Mu3Lab does not keep it
    (LiteLLM stores a hash). Offer "Save to my vault" (queue a vault item
    for that person: name "Mu3Lab voice key", password = key, URL = base URL).
  - Shows the **base URL** `https://<host>:8454/v1`, the model names, and a
    **Test** button (transcribes the bundled sample with the person's key).
  - **Revoke** and **Replace**.
- Operators can tick "also allow chat models" per person (adds the household
  chat model aliases to that key). Default off.
- A removed person's keys are revoked by the worker (periodic, by `user_id`).

### 5.3 Client guide (shown on the page and in `docs/voice-anywhere.md`)

Write numbered, beginner-level steps. Tailscale must be connected on the
device. VERIFY each client below (licence, still maintained, supports a
**custom OpenAI-compatible base URL plus API key**, types into the focused
app on Wayland and X11 for Linux). Drop any client that fails; keep at
least one per platform or say "none verified yet".

| Use | Candidate clients (VERIFY) | Settings |
|---|---|---|
| Dictate into **any** desktop app (Google Docs in a browser, LibreOffice, email) | OpenWhispr; whisper-writer; OpenWhisper (Windows/macOS/Linux) | Provider "OpenAI-compatible", base URL, key, model `mu3lab-stt`, hotkey |
| Read text aloud on desktop | A browser extension or desktop tool with custom OpenAI TTS endpoint (find one; VERIFY) | base URL, key, model `mu3lab-tts` |
| Phone dictation | Android: an open-source keyboard or voice-input app with a custom Whisper endpoint (VERIFY). iOS: a Shortcut that records audio and posts it to `/v1/audio/transcriptions` | base URL, key |
| Scripts/other servers | Any OpenAI SDK: `OpenAI(base_url=…, api_key=…)` | |

Include a `curl` example in the doc:

```bash
curl -s https://<host>:8454/v1/audio/transcriptions \
  -H "Authorization: Bearer $MU3LAB_VOICE_KEY" \
  -F model=mu3lab-stt -F file=@note.m4a
```

### 5.4 Chat apps and other server apps

- Open WebUI: audio env from [07-chat-choice.md](07-chat-choice.md) §5.1,
  pointing at `http://litellm:4000/v1` with Open WebUI's service key; models
  `mu3lab-stt`, `mu3lab-tts`. Wired by soft dependency.
- LobeChat: VERIFY whether LobeChat's speech-to-text and text-to-speech can
  use a custom OpenAI-compatible endpoint server-side (env such as
  `OPENAI_PROXY_URL` affects all OpenAI calls; check for a TTS/STT-specific
  setting). If possible, wire it by soft dependency; if not, document "voice
  input uses the browser's own speech recognition in LobeChat".
- Nextcloud (already in the catalog): its "OpenAI and LocalAI integration"
  app accepts a custom service URL for speech-to-text (VERIFY). Record it as
  an optional follow-up in `notes/future.md`; do not build it in this phase.

## 6. Task 4-3: Wyoming bridge for Home Assistant

### 6.1 VERIFY list

- Project `roryeckel/wyoming_openai`: image, version, digest, licence.
- Env/flags for STT and TTS base URLs, API keys, model names, voices,
  languages, listen URI (`tcp://0.0.0.0:10300`).
- Home Assistant: how to add a Wyoming integration config entry by API
  (config flow `wyoming`, host, port) and how to set the default Assist
  pipeline's STT/TTS engines (WebSocket `assist_pipeline/pipeline/*`).

### 6.2 Design

- App folder `apps/wyoming-bridge/`, `tier: optional`, capability
  `speech_wyoming`, `depends_on: [speaches]` (hard: useless without it).
  No route. Health `kind: tcp` on `127.0.0.1:10300`.
- Talks to `http://litellm:4000/v1` with its own LiteLLM key (speech models
  only).
- Hidden from Discover as a standalone item if possible (VERIFY a manifest
  way to mark "installed automatically as a helper"; otherwise list it in
  "Infrastructure" with a plain description). Home Assistant's manifest
  declares `integrates_with: speech_wyoming` with a rule that creates the HA
  config entry and sets the pipeline (see
  [apps/home-assistant.md](apps/home-assistant.md) §Voice). Offer "Install
  local voice" on HA's page, which installs Speaches and the bridge.

## 7. Edge cases

| # | Case | Handling |
|---|---|---|
| S1 | Browser audio formats (webm/opus, m4a) | Speaches uses ffmpeg (VERIFY); the test suite sends webm, m4a, wav. |
| S2 | Long recordings (meetings) | VERIFY Speaches and LiteLLM size limits; set `route.timeout_seconds` on LiteLLM high enough (e.g. 600); document the limit on the Voice page. |
| S3 | Many requests at once | Speaches queues; LiteLLM rpm per key prevents one device hogging it. |
| S4 | GPU busy (ComfyUI generating) | Speaches loads when memory frees; if CUDA OOM, the plain `gpu_memory_busy` message ([05-platform.md](05-platform.md) §4.6). Consider CPU fallback for STT when GPU OOM (VERIFY whether Speaches can be told per request; if not, do not build it). |
| S5 | CPU-only machine | `stt_quality: fast` default; the Voice page states expected speed. |
| S6 | Language other than English | `language` config; Whisper autodetects if unset; the Piper voice chosen by language. |
| S7 | Key leaked | Revoke on the Voice page; rate limit limits damage; the key reaches speech models only by default. |
| S8 | Device not on tailnet | Connection error; the doc's first step is "Connect Tailscale". |
| S9 | Speech removed while HA uses it | Rewire removes HA's Wyoming entry or marks it unavailable; HA shows its own error; the HA page says "Local voice not installed". |
| S10 | Model download interrupted | Retries; partial files cleaned by Speaches (VERIFY); install fails clearly after the retries. |
