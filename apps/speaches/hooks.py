"""Download the configured speech models through Speaches' own API, then prove they work.

Speaches loads models on demand but downloads only on request. Mu3Lab
downloads the chosen speech-to-text and text-to-speech models at install,
then speaks a sentence and transcribes a bundled recording, so a broken
model is found now rather than the first time someone talks to Mu3Lab.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from ctl.engine.hooks import AppHooks, HookContext
from ctl.engine.loopback import LoopbackError, request

DOWNLOAD_SECONDS = 1800  # the best Whisper model is about 1.6 GB
CHECK_SECONDS = 300  # the first use also loads the model into memory


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        env = ctx.env()
        port = ctx.app.manifest.service.local_port
        models = [env.get("MU3LAB_STT_MODEL", ""), env.get("MU3LAB_TTS_MODEL", "")]
        if not all(models):
            ctx.fail("speech_models", "speech_model_missing", "No speech models are configured.")
        for model in models:
            ctx.stage("speech_models", f"Downloading the speech model {model}.")
            try:
                request(port, "/v1/models/" + quote(model), method="POST", json_reply=False, timeout=DOWNLOAD_SECONDS)
            except LoopbackError as exc:
                ctx.fail(
                    "speech_models",
                    "speech_model_download_failed",
                    f"The speech model {model} could not be downloaded; check free disk space and the internet "
                    f"connection, then retry. ({exc})",
                )
        ctx.stage("speech_check", "Checking speech recognition and speech output.")
        base = f"http://127.0.0.1:{port}/v1/audio"
        try:
            with httpx.Client(timeout=CHECK_SECONDS) as client:
                spoken = client.post(
                    base + "/speech",
                    json={"model": models[1], "voice": env.get("MU3LAB_TTS_VOICE", ""), "input": "Mu3Lab is ready."},
                )
                sample = (ctx.app.folder / "sample.wav").read_bytes()
                heard = client.post(
                    base + "/transcriptions",
                    data={"model": models[0]},
                    files={"file": ("sample.wav", sample, "audio/wav")},
                )
        except httpx.HTTPError as exc:
            ctx.fail("speech_check", "speech_check_failed", f"Speaches did not answer the speech check ({exc}).")
            return
        text = heard.json().get("text", "") if heard.is_success else ""
        if not spoken.is_success or not spoken.content or "ready" not in text.lower():
            ctx.fail("speech_check", "speech_check_failed", "Speaches could not speak or transcribe a test sentence.")
