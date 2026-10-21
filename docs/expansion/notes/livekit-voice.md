# LiveKit voice app: research and decisions

Branch: `feature/livekit-voice` (from `feature/mcp-agents` at `79f7fa4`).
Status: built (`apps/livekit`). Nothing has been tested live; that is left to the owner.

## Decisions from the owner

- One Mu3Lab app (`apps/livekit`) containing the LiveKit server, the voice agent,
  and the third-party admin dashboard.
- The agent runs next to the dashboard so the owner can see that it is up and
  working.
- Keep the third-party admin dashboard for now. A Mu3Lab panel can come later.
- Talking to the agent: LiveKit's `agent-starter-react` first, `meet` as the fallback.
- Authentik sign-in is not part of this stage.
- Chat model goes through LiteLLM. Switch to a direct Ollama route only if LiteLLM adds visible lag.
- Default chat model `qwen3.5:4b`. `qwen3.5:9b` is a later option.
- Everything stays local. No LiveKit Cloud features.
- Images and sources are pinned to versions and commits, like the other apps.
- The hand-run `livekit-server --dev` is stopped (it was not running when checked).

## Pinned versions (verified 2026-10-19)

| Item | Pin | Evidence |
| --- | --- | --- |
| livekit-server | `livekit/livekit-server:v1.13.10@sha256:89ca9ce0791e18d6e03bf6e2c49ceb27e1c754eae8bd50c989386c1a5963791a` | latest GitHub release; index digest from `docker buildx imagetools inspect` |
| Python base for the agent | `ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca` | imagetools |
| livekit-agents and plugins | `1.8.8` (`uv.lock` in `apps/livekit/voice-agent`) | latest `livekit-agents` tag; `uv lock` |
| agent-starter-react | commit `9e1fc2f40fd7ceaccbe4a872b21765b49eefea80` (main, 2026-10-19), built from source | GitHub API. Not an image; `ghcr.io` returned 403 for it |
| livekit-dashboard (thinhdanggroup) | commit `248b67c5afa9e1093eb00207bb63b0166923acb9` (2026-07-02), built from source | same checkout the owner runs in `VoiceAgent/` |

## Findings that shape the design

1. **One published port per app.** `ctl/manifest/catalog.py` rejects an app whose
   compose file publishes any port other than `service.local_port`. A route
   serves exactly one local port (`routes.py` proxies to `https_port`, which is
   `local_port`). This app has three browser endpoints (voice UI, LiveKit
   signalling, admin dashboard), so a plain single-route design cannot serve all three.
   This is the decision blocking the compose file and `app.yaml`.
2. **Cloud defaults in the LiveKit SDK.** `AgentSession` defaults its VAD to
   `inference.VAD("silero")`, which is a LiveKit Inference call. The agent passes
   `silero.VAD.load()` instead.
   `inference.TurnDetector` defaults to version `v1`, which is hosted. The agent
   sets `version="v1-mini"`, which runs locally (the SDK's own comment says so).
3. **Speech through Speaches.** The OpenAI STT/TTS plugins accept `base_url` and
   `api_key`. `use_realtime=False` makes STT use the REST transcription endpoint.
4. **LLM.** `openai.LLM(base_url=..., reasoning_effort="none")` covers the
   thinking-off requirement for Qwen 3.5. `with_ollama` is not needed.
5. **Removed from the template:** `inference.LLM`, `inference.STT` with
   AssemblyAI keyterms, `inference.TTS` with Fish Audio, expressive mode (it needs
   Fish markup), ai_coustics noise cancellation (a cloud-linked enhancer; removed
   until it is confirmed to run offline), and adaptive interruptions (billed when self-hosted).
6. **Speaches web UI.** `apps/speaches/docker-compose.yml` now sets `ENABLE_UI: "true"`.
   It is published on `127.0.0.1:8092` only, so it is reachable from this machine only.
   The hand edits from the earlier session (`CHAT_COMPLETION_BASE_URL`,
   `LOOPBACK_HOST_URL`, `model_aliases.json`) are not in the repo and are not added.

## Decisions (2026-10-20)

- **Several ports and routes per app** (platform change, generic): `service.extra_ports` and
  `extra_routes`. LiveKit uses three addresses: Talk (the voice page, Authentik, household),
  the signal route (open; LiveKit checks its own room tokens), and the admin dashboard
  (Authentik, operators).
- **Narrow media ports, no host networking.** WebRTC media (7881/tcp, 7882/udp) is published on
  this server's tailnet address only (`${MU3LAB_TAILNET_IP:?...}`). The catalog refuses any other
  address, and Compose refuses to start without it. One UDP port (a mux) replaces the 50000-60000 range.
- **The agent's own LiteLLM key** (`gateway_key` rule), limited to `mu3lab-voice`, `mu3lab-stt`
  and `mu3lab-tts`. It is registered after install and again when LiteLLM is reinstalled.
- **Answer model:** the agent asks LiteLLM for `mu3lab-voice`, routed by Settings, Voice Assistant
  (outside providers or a local model, independent of chat; it matches chat until saved). The pinned LiteLLM (v1.103.2) turns `reasoning_effort: "none"` into Ollama's
  `think: false` (read in `llms/ollama/chat/transformation.py`), so Qwen does not think first.
- **Sign-in through Authentik, not the dashboard's own login.** The admin dashboard supports only
  one username and password (HTTP Basic, from its environment). Mu3Lab generates them, and Caddy sends
  them after Authentik admits an administrator (`upstream_basic_auth`, as Kopia does). The voice
  page's token endpoint refuses to run in production unless something authenticates visitors
  first; Authentik's gate is that layer.

## Live findings (2026-10-20)

- Rooms and dispatch already worked: the voice page's token names `mu3lab-agent`, LiveKit created the
  room and handed the agent the job. Nothing needs creating by hand.
- The agent's media never connected ("wait_pc_connection timed out"). LiveKit advertised only the tailnet
  address, which the agent cannot reach from the internal backend network. Fixed with
  `advertise_internal_ip: true` (supported by v1.13.10's rtcconfig with a fixed `node_ip`); tested live:
  the agent became active in a test room within a second.
- The installed agent still asked for `mu3lab-local`, which LiteLLM no longer routes once voice has its own
  setting. Reinstalling the app gives it `mu3lab-voice` and re-registers its key for that name.
- The agent now greets the person first (`session.generate_reply`), as in LiveKit's voice-AI guide.

## Still to confirm on the live install

- Docker publishing on the tailnet address at boot: if Docker starts before `tailscale0` has its
  address, the `server` container cannot bind and stays down until restarted.
- Browser microphone and WebRTC over the tailnet from another device (phone).
- The agent worker's health answer on port 8081 inside the container.
- The voice page and dashboard image builds (network needed at build time).
- Latency through LiteLLM compared with going direct (README, "Bypassing LiteLLM").

## Voice agent (written)

`apps/livekit/voice-agent/`: `pyproject.toml`, `uv.lock`, `Dockerfile` (the
template's build, minus the cloud pieces), `src/agent.py`, `.dockerignore`,
`.gitignore`. Every URL and model name comes from environment variables with
defaults that match the Mu3Lab services.

Static checks run: the module's names resolve against the installed `livekit-agents
1.8.8` and OpenAI plugin. This is not a live run.
