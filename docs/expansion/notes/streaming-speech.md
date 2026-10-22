# Streaming speech: WhisperLiveKit and Kokoro-FastAPI replace Speaches

Branch: `feature/streaming-speech` (from `feature/livekit-voice` at `e77fff3`).
Status: built. The Speech images were built and checked on this computer; a real call is left to the owner.

## Decisions from the owner (2026-10-21)

- The voice assistant streams speech recognition instead of sending one clip per pause.
  Recognition no longer goes through LiteLLM for the agent.
- WhisperLiveKit does the streaming; Kokoro-FastAPI reads aloud. Together they replace Speaches,
  so the server keeps one Whisper instead of two (Open WebUI's own small Whisper is unchanged).
- Afterwards: Silero's pause before speech ends goes from 0.55 s to 0.3 s, and the first spoken
  sentence is prepared before the turn is confirmed (`preemptive_tts`).

## Why this pairing

- Speaches' last stable release is the 0.8.x line Mu3Lab ran; 0.9.0 stayed a release candidate
  (Dec 2025) and `main` had no commits after April 2026.
- WhisperLiveKit (Apache-2.0) streams Whisper with SimulStreaming (AlignAtt) and has two APIs Mu3Lab
  needs: a Deepgram-compatible `/v1/listen` WebSocket and an OpenAI-compatible
  `/v1/audio/transcriptions`. Its `--api-token` protects both; `/health` stays open.
- LiveKit's official Deepgram plugin takes a custom `base_url`, so the agent needs no protocol code of
  its own. Kokoro-FastAPI is the Kokoro server LiveKit's docs point to.

## Pinned versions (verified 2026-10-21)

| Item | Pin | Evidence |
| --- | --- | --- |
| WhisperLiveKit | tag `v0.2.26`, commit `aceed595a43e9eed5008c142d90a3f6e86747fbf`, built from source with its `uv.lock` | `git ls-remote`; PyPI 0.2.26 |
| uv (build stage) | `ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3eb…2500` | upstream Dockerfile's tag; imagetools |
| CPU bases | `debian:bookworm-slim@sha256:7ff6ba…e3f8` | upstream `Dockerfile.cpu`; imagetools |
| CUDA bases | `nvidia/cuda:12.9.2-cudnn-{devel,runtime}-ubuntu24.04` by digest | upstream `Dockerfile`; imagetools |
| Kokoro-FastAPI | `ghcr.io/remsky/kokoro-fastapi-{cpu,gpu}:v0.9.0` by digest | latest GitHub release (2026-09-10); imagetools |
| livekit-plugins-deepgram | `1.8.8`, matching livekit-agents | `uv lock` |

## How it fits together

- `apps/speech`: `stt` (WhisperLiveKit, `--model large-v3-turbo --language en`, port 8092) and `tts`
  (Kokoro-FastAPI, model inside the image, port 8095). On NVIDIA both use the GPU
  (`docker-compose.nvidia.yml`). Models live in `data/speech/models` and are not backed up.
- LiteLLM: `mu3lab-stt` → WhisperLiveKit REST with the Speech key (read from LiteLLM's environment,
  never written to `config.yaml`); `mu3lab-tts` → Kokoro. Voice keys keep working unchanged.
- Voice assistant: `deepgram.STT(base_url=<stt>/v1/listen, language="en", interim_results=True)`;
  its LiteLLM key now covers only `mu3lab-voice` and `mu3lab-tts`.

## Known gaps

- WhisperLiveKit refuses Deepgram's `Finalize` message by closing the stream. LiveKit's plugin sends
  it only when the stream ends (hang-up), so expect one closed-stream line per call, not a broken call.
- WhisperLiveKit's Deepgram mode is tested upstream with Deepgram's own SDK, not LiveKit's plugin; it
  was checked here by streaming the bundled recording through the plugin.
- WhisperLiveKit keeps its model in graphics memory all the time (Speaches unloaded after five idle
  minutes). Confidence scores are always 0; nothing in Mu3Lab uses them.
- An existing install has the old `speaches` app. Before switching to this branch, uninstall the voice
  assistant and then Speech; afterwards install Speech and the voice assistant again.
