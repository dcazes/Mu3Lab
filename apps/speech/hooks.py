"""Prove speech recognition and the voice work before anything relies on them.

WhisperLiveKit downloads and loads its model before it reports healthy, and
Kokoro's model is inside its image, so there is nothing left to download here.
Mu3Lab speaks a sentence and transcribes a bundled recording, so a broken model
is found now rather than the first time someone talks to Mu3Lab.
"""

from __future__ import annotations

import httpx

from ctl.engine.hooks import AppHooks, HookContext

CHECK_SECONDS = 300  # the first transcription can still be slow while the GPU warms up


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        env = ctx.env()
        service = ctx.app.manifest.service
        # WhisperLiveKit on the main port, Kokoro on the one extra port.
        stt_port, tts_port = service.local_port, service.extra_ports[0].port
        ctx.stage("speech_check", "Checking speech recognition and speech output.")
        try:
            with httpx.Client(timeout=CHECK_SECONDS) as client:
                spoken = client.post(
                    f"http://127.0.0.1:{tts_port}/v1/audio/speech",
                    json={
                        "model": env.get("MU3LAB_TTS_MODEL", ""),
                        "voice": env.get("MU3LAB_TTS_VOICE", ""),
                        "input": "Mu3Lab is ready.",
                    },
                )
                sample = (ctx.app.folder / "sample.wav").read_bytes()
                heard = client.post(
                    f"http://127.0.0.1:{stt_port}/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {env.get('WLK_API_TOKEN', '')}"},
                    files={"file": ("sample.wav", sample, "audio/wav")},
                )
        except httpx.HTTPError as exc:
            ctx.fail("speech_check", "speech_check_failed", f"Speech did not answer the speech check ({exc}).")
            return
        text = heard.json().get("text", "") if heard.is_success else ""
        if not spoken.is_success or not spoken.content:
            ctx.fail("speech_check", "speech_check_failed", "The voice (Kokoro) could not read a test sentence aloud.")
        if "ready" not in text.lower():
            ctx.fail(
                "speech_check",
                "speech_check_failed",
                "Speech recognition (WhisperLiveKit) could not transcribe a test recording.",
            )
