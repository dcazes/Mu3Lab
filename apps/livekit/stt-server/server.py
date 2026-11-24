"""Streaming speech recognition for Mu3Lab's voice assistant, on sherpa-onnx (streaming Zipformer).

Adapted from the VoiceAgent project's stt-server. Text arrives while the person talks, and a final
comes a few tens of milliseconds after the agent asks for it (a flush, sent when voice activity
says they stopped), instead of after a whole recording is sent to Whisper.

Each model is loaded the first time a call asks for it and kept; every connection gets its own
decoding stream. The models live in STT_MODELS/<name> (Dockerfile): "kroko" (punctuation and
capitals) and "zipformer" (capitals only, turned into ordinary sentence case here).

Protocol, one WebSocket connection per call, ``/?model=<name>``:
  client -> server
    binary frame : 16 kHz, mono, signed 16-bit little-endian PCM
    text frame   : {"type": "flush"}  finish the current segment now
                   {"type": "end"}    finish it and close
  server -> client (JSON text frames)
    {"type": "ready", "sample_rate": 16000, "model": "..."}
    {"type": "partial", "segment": n, "text": "..."}   the text so far changed
    {"type": "final", "segment": n, "text": "...", "audio_s": x}
        on an endpoint (trailing silence) or a flush; a flush always gets a final, even empty.

Health: HTTP GET /health -> 200 "ok".
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import re
import signal
import threading
import time
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

import numpy as np
import sherpa_onnx
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

log = logging.getLogger("stt-server")

SAMPLE_RATE = 16000
# Silence added on a flush so the model gives its last words.
FLUSH_PADDING_S = 0.5
MODELS = os.environ.get("STT_MODELS", "/models")
DEFAULT_MODEL = os.environ.get("STT_DEFAULT_MODEL", "kroko")
NAME = re.compile(r"^[a-z0-9-]{1,40}$")


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _pick(model_dir: str, part: str) -> str:
    files = sorted(glob.glob(os.path.join(model_dir, f"{part}*.onnx")))
    if not files:
        raise FileNotFoundError(f"no {part}*.onnx in {model_dir}")
    int8 = [path for path in files if ".int8." in path]
    return (int8 or files)[0]


_recognizers: dict[str, sherpa_onnx.OnlineRecognizer] = {}
_lock = threading.Lock()


def recognizer(name: str) -> sherpa_onnx.OnlineRecognizer:
    """The model's recognizer, loaded the first time a call asks for it."""
    with _lock:
        if name in _recognizers:
            return _recognizers[name]
        model_dir = os.path.join(MODELS, name)
        if not NAME.match(name) or not os.path.isdir(model_dir):
            raise FileNotFoundError(name)
        started = time.monotonic()
        loaded = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=os.path.join(model_dir, "tokens.txt"),
            encoder=_pick(model_dir, "encoder"),
            decoder=_pick(model_dir, "decoder"),
            joiner=_pick(model_dir, "joiner"),
            num_threads=int(os.environ.get("STT_NUM_THREADS", "1")),
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            enable_endpoint_detection=True,
            # rule1: silence with nothing decoded; rule2: silence after speech; rule3: break a very
            # long utterance. The agent's flush usually comes first.
            rule1_min_trailing_silence=_env_float("STT_RULE1_SILENCE", 2.4),
            rule2_min_trailing_silence=_env_float("STT_RULE2_SILENCE", 0.5),
            rule3_min_utterance_length=_env_float("STT_RULE3_LENGTH", 20.0),
        )
        _recognizers[name] = loaded
        log.info("loaded model %s in %.2fs", name, time.monotonic() - started)
        return loaded


_I = re.compile(r"\bi\b")


def tidy(text: str, name: str) -> str:
    """Zipformer writes CAPITALS with no punctuation; give it sentence case, as people (and the turn
    detector, which reads the text) expect. Kroko's text is already written properly."""
    if name != "zipformer" or not text:
        return text
    lowered = _I.sub("I", text.lower())
    return lowered[0].upper() + lowered[1:]


class Session:
    """Decoding state for one connection; the decoding itself runs in a worker thread."""

    def __init__(self, rec: sherpa_onnx.OnlineRecognizer, name: str, ws: ServerConnection) -> None:
        self.rec, self.name, self.ws = rec, name, ws
        self.stream = rec.create_stream()
        self.segment = 0
        self.last_partial = ""
        self.segment_audio_s = 0.0

    def _decode(self) -> tuple[str, bool]:
        while self.rec.is_ready(self.stream):
            self.rec.decode_stream(self.stream)
        return tidy(self.rec.get_result(self.stream).strip(), self.name), self.rec.is_endpoint(self.stream)

    def _accept_and_decode(self, pcm: bytes) -> tuple[str, bool]:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self.stream.accept_waveform(SAMPLE_RATE, samples)
        return self._decode()

    def _flush_and_decode(self) -> str:
        self.stream.accept_waveform(SAMPLE_RATE, np.zeros(int(FLUSH_PADDING_S * SAMPLE_RATE), dtype=np.float32))
        return self._decode()[0]

    async def send(self, message: dict) -> None:
        await self.ws.send(json.dumps(message))

    async def _final(self, text: str) -> None:
        await self.send(
            {"type": "final", "segment": self.segment, "text": text, "audio_s": round(self.segment_audio_s, 3)}
        )
        self.rec.reset(self.stream)
        self.segment += 1
        self.last_partial = ""
        self.segment_audio_s = 0.0

    async def on_audio(self, pcm: bytes) -> None:
        self.segment_audio_s += len(pcm) / 2 / SAMPLE_RATE
        text, endpoint = await asyncio.to_thread(self._accept_and_decode, pcm)
        if text != self.last_partial and not endpoint:
            self.last_partial = text
            await self.send({"type": "partial", "segment": self.segment, "text": text})
        if endpoint:
            if text:
                await self._final(text)
            else:  # silence only: start again without a final
                self.rec.reset(self.stream)
                self.last_partial = ""

    async def on_flush(self) -> None:
        await self._final(await asyncio.to_thread(self._flush_and_decode))


async def handler(ws: ServerConnection) -> None:
    query = parse_qs(urlsplit(ws.request.path if ws.request else "/").query)
    name = (query.get("model") or [DEFAULT_MODEL])[0]
    try:
        rec = await asyncio.to_thread(recognizer, name)
    except FileNotFoundError:
        await ws.close(1008, f"no model {name}"[:100])
        return
    started = time.monotonic()
    session = Session(rec, name, ws)
    await session.send({"type": "ready", "sample_rate": SAMPLE_RATE, "model": name})
    try:
        async for message in ws:
            if isinstance(message, bytes):
                await session.on_audio(message)
                continue
            try:
                kind = json.loads(message).get("type")
            except (ValueError, AttributeError):
                kind = None
            if kind == "flush":
                await session.on_flush()
            elif kind == "end":
                await session.on_flush()
                break
    except ConnectionClosed:
        pass
    finally:
        log.info("call with %s ended after %.1fs, %d segments", name, time.monotonic() - started, session.segment)


def health(conn: ServerConnection, request: Request) -> Response | None:
    if request.path == "/health":
        return conn.respond(HTTPStatus.OK, "ok\n")
    return None


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    # The health check would otherwise log two lines a minute.
    logging.getLogger("websockets.server").setLevel(logging.WARNING)
    # The usual model is ready before the first call.
    await asyncio.to_thread(recognizer, DEFAULT_MODEL)
    host, port = os.environ.get("STT_HOST", "0.0.0.0"), int(os.environ.get("STT_PORT", "6006"))
    stop = asyncio.get_running_loop().create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set_result, None)
    async with serve(handler, host, port, process_request=health, max_size=2**20, compression=None):
        log.info("listening on ws://%s:%d", host, port)
        await stop


if __name__ == "__main__":
    asyncio.run(main())
