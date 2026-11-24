"""LiveKit speech-recognition plugin for the LiveKit app's own streaming recognizer
(``apps/livekit/stt-server``), adapted from the VoiceAgent project's ``sherpa_stt.py``.

Audio goes over a WebSocket as 16 kHz mono 16-bit PCM, to ``<url>/?model=<name>``. The server
returns text while the person talks, and a final when it hears an endpoint or is asked to flush.

Quick finals: LiveKit ends a turn only once it has a final, and the server's own silence detection
takes about a second. So when the session's voice activity says the person stopped talking, the
agent calls ``SherpaSTT.flush_all()`` (``follow_speech`` in agent.py); the final then arrives within
a few tens of milliseconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref

import aiohttp
from livekit import rtc
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    stt,
    utils,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

logger = logging.getLogger("mu3lab-voice-agent")

SAMPLE_RATE = 16000


class SherpaSTT(stt.STT):
    def __init__(
        self,
        *,
        url: str = "ws://livekit-stt:6006",
        language: str = "en",
        model_name: str = "kroko",
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=True, interim_results=True, offline_recognize=False)
        )
        self._url = f"{url.rstrip('/')}/?model={model_name}"
        self._language = language
        self._model_name = model_name
        self._streams: weakref.WeakSet[SherpaRecognizeStream] = weakref.WeakSet()

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def provider(self) -> str:
        return "sherpa-onnx"

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SherpaRecognizeStream:
        s = SherpaRecognizeStream(stt=self, conn_options=conn_options)
        self._streams.add(s)
        return s

    def flush_all(self) -> None:
        """Ask every open stream to finalize its current segment now."""
        for s in list(self._streams):
            s.request_final()

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        """One-shot recognition (not used by AgentSession; handy for tests)."""
        frame = rtc.combine_audio_frames(buffer)
        if frame.sample_rate != SAMPLE_RATE or frame.num_channels != 1:
            resampler = rtc.AudioResampler(frame.sample_rate, SAMPLE_RATE, num_channels=1)
            frames = resampler.push(frame) + resampler.flush()
            frame = rtc.combine_audio_frames(frames)
        async with aiohttp.ClientSession() as http, http.ws_connect(self._url) as ws:
            await ws.receive_json(timeout=conn_options.timeout)  # ready
            await ws.send_bytes(bytes(frame.data))
            await ws.send_str(json.dumps({"type": "end"}))
            async for msg in ws:
                data = json.loads(msg.data)
                if data.get("type") == "final":
                    return _event(
                        stt.SpeechEventType.FINAL_TRANSCRIPT,
                        data["text"],
                        self._language,
                    )
        raise APIConnectionError("sherpa stt closed without a final transcript")


def _event(kind: stt.SpeechEventType, text: str, language: str) -> stt.SpeechEvent:
    return stt.SpeechEvent(
        type=kind,
        alternatives=[stt.SpeechData(language=language, text=text, confidence=1.0)],
    )


class SherpaRecognizeStream(stt.RecognizeStream):
    def __init__(self, *, stt: SherpaSTT, conn_options: APIConnectOptions) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=SAMPLE_RATE)
        self._sherpa = stt
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._send_lock = asyncio.Lock()
        self._loop = asyncio.get_running_loop()
        self._flush_requested_at: float | None = None
        self._pending_sends: set[asyncio.Task] = set()

    def request_final(self) -> None:
        """Thread-safe: send a flush so the server emits a final now."""
        self._loop.call_soon_threadsafe(self._schedule_flush)

    def _schedule_flush(self) -> None:
        if self._ws is not None and not self._ws.closed:
            self._flush_requested_at = time.perf_counter()
            task = asyncio.ensure_future(self._send_json({"type": "flush"}))
            self._pending_sends.add(task)
            task.add_done_callback(self._pending_sends.discard)

    async def _send_json(self, msg: dict) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            return
        async with self._send_lock:
            await ws.send_str(json.dumps(msg))

    async def _run(self) -> None:
        lang = self._sherpa._language
        try:
            async with (
                aiohttp.ClientSession() as http,
                http.ws_connect(self._sherpa._url, timeout=aiohttp.ClientWSTimeout(ws_close=5)) as ws,
            ):
                ready = await asyncio.wait_for(ws.receive_json(), self._conn_options.timeout)
                if ready.get("type") != "ready":
                    raise APIStatusError(f"unexpected hello from stt server: {ready}")
                self._ws = ws
                send = asyncio.create_task(self._send_task(ws))
                recv = asyncio.create_task(self._recv_task(ws, lang))
                try:
                    done, _ = await asyncio.wait([send, recv], return_when=asyncio.FIRST_COMPLETED)
                    for t in done:
                        t.result()  # surface errors
                    if send in done:
                        # input ended: wait for the server's last final, then close
                        await asyncio.wait_for(recv, 5)
                finally:
                    await utils.aio.gracefully_cancel(send, recv)
                    self._ws = None
        except (TimeoutError, aiohttp.ClientError, ConnectionError) as e:
            raise APIConnectionError(f"sherpa stt connection failed: {e}") from e

    async def _send_task(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for item in self._input_ch:
            if isinstance(item, rtc.AudioFrame):
                data = bytes(item.data)
                async with self._send_lock:
                    await ws.send_bytes(data)
            elif isinstance(item, self._FlushSentinel):
                await self._send_json({"type": "flush"})
        await self._send_json({"type": "end"})

    async def _recv_task(self, ws: aiohttp.ClientWebSocketResponse, lang: str) -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                continue
            data = json.loads(msg.data)
            kind, text = data.get("type"), data.get("text", "")
            # Start/end of speech come from the session's Silero VAD, so this
            # plugin only reports transcripts.
            if kind == "partial" and text:
                self._event_ch.send_nowait(_event(stt.SpeechEventType.INTERIM_TRANSCRIPT, text, lang))
            elif kind == "final":
                if self._flush_requested_at is not None:
                    logger.debug(
                        "final after flush",
                        extra={"flush_to_final_ms": round((time.perf_counter() - self._flush_requested_at) * 1000)},
                    )
                    self._flush_requested_at = None
                # always forward finals (even empty) so the session stops waiting
                self._event_ch.send_nowait(_event(stt.SpeechEventType.FINAL_TRANSCRIPT, text, lang))
                audio_s = float(data.get("audio_s") or 0.0)
                if audio_s:
                    self._event_ch.send_nowait(
                        stt.SpeechEvent(
                            type=stt.SpeechEventType.RECOGNITION_USAGE,
                            recognition_usage=stt.RecognitionUsage(audio_duration=audio_s),
                        )
                    )
        if ws.close_code not in (None, 1000):
            raise APIConnectionError(f"sherpa stt closed with code {ws.close_code}")
