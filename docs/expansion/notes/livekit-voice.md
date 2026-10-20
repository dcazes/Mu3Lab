# LiveKit voice app: research and decisions

Branch: `feature/livekit-voice` (from `feature/mcp-agents` at `79f7fa4`).
Status: research done, voice agent written, compose and manifest waiting on the
routing decision below. Nothing has been tested live; that is left to the owner.

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

## Open routing question (blocking)

LiveKit needs three browser-facing endpoints. Mu3Lab gives each app one route.
Options are listed in the chat reply.

## Voice agent (written)

`apps/livekit/voice-agent/`: `pyproject.toml`, `uv.lock`, `Dockerfile` (the
template's build, minus the cloud pieces), `src/agent.py`, `.dockerignore`,
`.gitignore`. Every URL and model name comes from environment variables with
defaults that match the Mu3Lab services.

Static checks run: the module's names resolve against the installed `livekit-agents
1.8.8` and OpenAI plugin. This is not a live run.
