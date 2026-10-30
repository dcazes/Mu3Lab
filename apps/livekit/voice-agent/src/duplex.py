"""Full-duplex calls: one model (MiniCPM-o 4.5) listens and speaks at the same time.

Mu3Lab switches the voice assistant to this mode in Settings, Voice Assistant (ctl/duplex_voice.py)
and writes ``engine.json`` beside the app tools; the agent reads it at each call's start.

A call here does not use LiveKit's AgentSession, which takes turns. Instead a bridge joins the room,
sends the person's microphone to llama.cpp-omni's WebSocket one second at a time (16 kHz), and
plays the model's speech as it arrives (24 kHz). The model decides itself when to talk, listen or
stop, so there is no voice detection or turn detector here; the browser's echo cancellation keeps
it from hearing itself.

The model can have one tool, a private web search. Its system prompt tells it to say "Let me look
that up: <words>." when it needs current facts; the bridge hears that in the model's text, searches
SearXNG, and hands the results to the model as text with the next second of audio (a Mu3Lab patch
to the server, apps/livekit/duplex/duplex-text.patch).

While duplex is being made reliable, the search and Mu3Lab's own prompt are off (SEARCH_TOOL,
MU3LAB_PROMPT): the model gets the prompt it was trained with, and each second of the call is
logged (what the model heard, and whether it listened or spoke).

Run as ``python src/duplex.py warm`` to load the model onto the graphics card ahead of the first call.
"""

from __future__ import annotations

import array
import asyncio
import base64
import json
import logging
import math
import re
import sys
import time
from typing import Any

import aiohttp
from livekit import rtc

logger = logging.getLogger("mu3lab-voice-agent")

IN_RATE = 16000  # the model hears 16 kHz
OUT_RATE = 24000  # and speaks 24 kHz
CHUNK = IN_RATE  # one second of audio per step, as the model was trained
FRAME = OUT_RATE // 50  # 20 ms frames into LiveKit
# The person's audio waits at most this long while the model catches up; older audio is dropped.
MAX_BACKLOG = 3 * IN_RATE
# Steps sent but not yet answered before the bridge waits; and how long it waits at most.
MAX_IN_FLIGHT = 2
STALL_SECONDS = 3.0
CONNECT_SECONDS = 300  # the first call loads the model
SEARCH_SECONDS = 10
RESULTS = 3
RESULT_CHARS = 300
SEARCH = re.compile(r"let me look (?:that|it|this) up\s*[:,-]?\s*(?:for\s+)?(.{3,160}?)\s*[.?!\n]", re.IGNORECASE)
STATE = "lk.agent.state"  # what the voice page shows (listening, speaking), as LiveKit's agents set it
# Off while testing: the web search, and Mu3Lab's instructions in place of the model's trained prompt.
SEARCH_TOOL = False
MU3LAB_PROMPT = False

TOOL_PROMPT = (
    "You can search the internet. When the person asks about something current or that you do not know "
    "for certain (news, weather, prices, opening hours, sports, recent events), say exactly "
    '"Let me look that up: " followed by a few search words, then a full stop, and wait. '
    "Search results then arrive as text marked [Search results]. Answer from them in one or two short "
    "spoken sentences. Never read out links."
)


def system_prompt(instructions: str) -> str:
    """The duplex model's system prompt: Mu3Lab's instructions after its voice, as the server expects."""
    tool = f"\n\n{TOOL_PROMPT}" if SEARCH_TOOL else ""
    return f"<|audio_end|>{instructions}{tool}<|im_end|>\n"


def _to_float32(pcm16: bytes) -> str:
    samples = array.array("h")
    samples.frombytes(pcm16)
    out = array.array("f", (sample / 32768.0 for sample in samples))
    return base64.b64encode(out.tobytes()).decode("ascii")


def _level(pcm16: bytes) -> str:
    """How much of a second was the person's audio, and its loudness in dBFS (below -50 is silence)."""
    samples = array.array("h")
    samples.frombytes(pcm16)
    if not samples:
        return "nothing"
    power = sum(sample * sample for sample in samples) / len(samples)
    loudness = 10 * math.log10(power / 32768**2) if power else -120.0
    return f"{len(samples) * 1000 // IN_RATE} ms at {loudness:.0f} dBFS"


def _to_int16(b64: str) -> bytes:
    samples = array.array("f")
    samples.frombytes(base64.b64decode(b64))
    out = array.array("h", (max(-32768, min(32767, int(sample * 32767.0))) for sample in samples))
    return out.tobytes()


async def search(http: aiohttp.ClientSession, base_url: str, query: str) -> str:
    """The top results from SearXNG as plain text the model reads, or why there are none."""
    try:
        async with http.get(
            f"{base_url}/search",
            params={"q": query, "format": "json", "safesearch": "1"},
            timeout=aiohttp.ClientTimeout(total=SEARCH_SECONDS),
        ) as reply:
            reply.raise_for_status()
            data = await reply.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        logger.warning("Web search failed: %s", exc)
        return f'[Search results for "{query}"]\nThe search did not work just now. Say so briefly.\n[End of search results]\n'
    lines = [str(answer) for answer in data.get("answers") or []][:1]
    for result in (data.get("results") or [])[:RESULTS]:
        title = str(result.get("title") or "").strip()
        content = " ".join(str(result.get("content") or "").split())[:RESULT_CHARS]
        lines.append(f"- {title}: {content}" if content else f"- {title}")
    if not lines:
        lines.append("Nothing was found.")
    body = "\n".join(lines)
    return f'\n[Search results for "{query}"]\n{body}\n[End of search results]\n'


class DuplexCall:
    """One call between the person in the room and the duplex model."""

    def __init__(self, room: rtc.Room, track: rtc.Track, settings: dict[str, Any], instructions: str) -> None:
        self.room, self.track = room, track
        self.omni_url = str(settings.get("omni_url") or "ws://minicpm-omni:9060/backend")
        self.search_url = str(settings.get("search_url") or "http://voice-search:8080")
        self.instructions = instructions
        self.heard = bytearray()  # the person's 16 kHz audio not yet sent
        self.pending_text = ""  # search results for the next step
        self.in_flight = 0
        self.steps = 0
        # Per step sent, for the log: how much of the second was the person's audio, and how loud.
        self.heard_log: dict[int, str] = {}
        # Bytes of speech since the last logged step (speech lags its step, so it is not per step).
        self.spoken = 0
        self.done_ids: set[str] = set()
        self.last_answer = time.monotonic()
        self.speech: asyncio.Queue[bytes] = asyncio.Queue()
        self.texts: dict[str, str] = {}
        self.searched: set[str] = set()
        self.closed = asyncio.Event()
        self.source = rtc.AudioSource(OUT_RATE, 1, queue_size_ms=2000)
        self.state = ""

    async def run(self) -> None:
        voice = rtc.LocalAudioTrack.create_audio_track("assistant-voice", self.source)
        await self.room.local_participant.publish_track(
            voice, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        async with aiohttp.ClientSession() as http:
            try:
                ws = await http.ws_connect(self.omni_url, max_msg_size=0, heartbeat=30)
            except aiohttp.ClientError as exc:
                logger.error("The duplex model is not reachable: %s", exc)
                await self._say_in_text(
                    "The voice model is not running. An administrator can check Settings, Voice Assistant."
                )
                return
            async with ws:
                payload: dict[str, Any] = {"mode": "full_duplex", "use_tts": True}
                if MU3LAB_PROMPT:
                    payload["system_prompt"] = system_prompt(self.instructions)
                await ws.send_json({"type": "session.init", "payload": payload})
                if not await self._created(ws):
                    await self._say_in_text("The voice model is in another call. Try again when it ends.")
                    return
                await self._set_state("listening")
                tasks = [
                    asyncio.create_task(self._hear()),
                    asyncio.create_task(self._send(ws)),
                    asyncio.create_task(self._receive(ws, http)),
                    asyncio.create_task(self._play()),
                ]
                await self.closed.wait()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    def close(self) -> None:
        self.closed.set()

    async def _created(self, ws: aiohttp.ClientWebSocketResponse) -> bool:
        """Wait for the model to accept the call (it loads on the first one); False when refused."""
        try:
            async with asyncio.timeout(CONNECT_SECONDS):
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        return False
                    event = json.loads(message.data)
                    if event.get("type") == "session.created":
                        return True
        except TimeoutError:
            logger.error("The duplex model did not accept the call within %s s.", CONNECT_SECONDS)
        return False

    async def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            await self.room.local_participant.set_attributes({STATE: state})

    async def _say_in_text(self, text: str) -> None:
        try:
            await self.room.local_participant.send_text(text, topic="lk.chat")
        except Exception as exc:  # the room may already be closing
            logger.warning("Could not tell the person: %s", exc)

    async def _hear(self) -> None:
        stream = rtc.AudioStream.from_track(track=self.track, sample_rate=IN_RATE, num_channels=1)
        async for event in stream:
            self.heard.extend(bytes(event.frame.data))
            if len(self.heard) > MAX_BACKLOG * 2:
                del self.heard[: len(self.heard) - MAX_BACKLOG * 2]

    async def _send(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Every second, send that second of the person (silence when quiet or muted)."""
        tick = time.monotonic()
        while True:
            tick += 1.0
            await asyncio.sleep(max(0.0, tick - time.monotonic()))
            stalled = time.monotonic() - self.last_answer > STALL_SECONDS
            if self.in_flight >= MAX_IN_FLIGHT and not stalled:
                continue  # the model is behind; the audio waits in ``heard``
            if stalled:
                self.in_flight = 0
            chunk = bytes(self.heard[: CHUNK * 2])
            del self.heard[: CHUNK * 2]
            self.steps += 1
            self.heard_log[self.steps] = _level(chunk)
            chunk += bytes(CHUNK * 2 - len(chunk))
            step: dict[str, Any] = {"audio_base64": _to_float32(chunk)}
            if self.pending_text:
                step["text"], self.pending_text = self.pending_text, ""
            await ws.send_json({"type": "input.append", "input": step})
            self.in_flight += 1

    def _answered(self, response_id: str, listened: bool) -> None:
        self.last_answer = time.monotonic()
        if response_id and response_id in self.done_ids:
            return
        if response_id:
            self.done_ids.add(response_id)
        self.in_flight = max(0, self.in_flight - 1)
        self._log_step(response_id, listened)

    def _log_step(self, response_id: str, listened: bool) -> None:
        """One line per second of the call: what the model heard, and whether it listened or spoke."""
        step = int(response_id.rpartition("_")[2]) if response_id.rpartition("_")[2].isdigit() else 0
        heard = self.heard_log.pop(step, "?")
        text = self.texts.get(response_id, "").strip()
        played, self.spoken = self.spoken / 2 / OUT_RATE, 0
        action = "listen" if listened and not text else f"speak {text!r}" + (" then listen" if listened else "")
        logger.info(
            "Duplex step %s: heard %s; %s; %.1fs of speech arrived; %s in flight",
            step,
            heard,
            action,
            played,
            self.in_flight,
        )

    async def _receive(self, ws: aiohttp.ClientWebSocketResponse, http: aiohttp.ClientSession) -> None:
        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                break
            event = json.loads(message.data)
            kind, rid = event.get("kind"), str(event.get("response_id") or "")
            if event.get("type") == "response.output.delta":
                if kind == "audio" and event.get("audio"):
                    speech = _to_int16(event["audio"])
                    self.spoken += len(speech)
                    await self.speech.put(speech)
                elif kind == "text":
                    self.texts[rid] = self.texts.get(rid, "") + str(event.get("text") or "")
                    if SEARCH_TOOL:
                        await self._maybe_search(http, self.texts[rid])
                elif kind == "listen":
                    self._answered(rid, listened=True)
            elif event.get("type") == "response.done":
                self._answered(rid, listened=False)
                text = self.texts.pop(rid, "") or str(event.get("text") or "")
                if text.strip():
                    await self._caption(text.strip(), rid)
            elif event.get("type") == "session.closed":
                break
        logger.info("The duplex model closed the call.")
        self.close()

    async def _maybe_search(self, http: aiohttp.ClientSession, text: str) -> None:
        found = SEARCH.search(text)
        if not found:
            return
        query = found.group(1).strip().strip('"')
        if query.lower() in self.searched:
            return
        self.searched.add(query.lower())
        logger.info("Searching the web for the model: %s", query)
        self.pending_text = await search(http, self.search_url, query)

    async def _caption(self, text: str, segment: str) -> None:
        """What the model said, for the voice page's transcript."""
        try:
            await self.room.local_participant.send_text(
                text,
                topic="lk.transcription",
                attributes={"lk.segment_id": segment or "duplex", "lk.transcription_final": "true"},
            )
        except Exception as exc:
            logger.debug("Caption not sent: %s", exc)

    async def _play(self) -> None:
        """Play the model's speech in 20 ms frames; the voice page shows speaking while it plays."""
        pending = b""
        while True:
            try:
                pending += await asyncio.wait_for(self.speech.get(), timeout=0.3)
            except TimeoutError:
                if not pending:
                    await self._set_state("listening")
                    continue
            await self._set_state("speaking")
            while len(pending) >= FRAME * 2 or (pending and self.speech.empty()):
                piece, pending = pending[: FRAME * 2], pending[FRAME * 2 :]
                if len(piece) < FRAME * 2:
                    piece += bytes(FRAME * 2 - len(piece))
                frame = rtc.AudioFrame(piece, OUT_RATE, 1, FRAME)
                await self.source.capture_frame(frame)


async def run_call(
    room: rtc.Room, participant: rtc.RemoteParticipant, settings: dict[str, Any], instructions: str
) -> None:
    """Hold one duplex call until the person leaves or the model ends it."""
    track = await _microphone(room, participant)
    call = DuplexCall(room, track, settings, instructions)

    def left(gone: rtc.RemoteParticipant) -> None:
        if gone.identity == participant.identity:
            call.close()

    room.on("participant_disconnected", left)
    room.on("disconnected", lambda *_: call.close())
    await call.run()


async def _microphone(room: rtc.Room, participant: rtc.RemoteParticipant) -> rtc.Track:
    for publication in participant.track_publications.values():
        if publication.kind == rtc.TrackKind.KIND_AUDIO and publication.track is not None:
            return publication.track
    found: asyncio.Future[rtc.Track] = asyncio.get_running_loop().create_future()

    def subscribed(track: rtc.Track, _publication, who: rtc.RemoteParticipant) -> None:
        if who.identity == participant.identity and track.kind == rtc.TrackKind.KIND_AUDIO and not found.done():
            found.set_result(track)

    room.on("track_subscribed", subscribed)
    return await found


async def warm(url: str) -> None:
    """Open and close one call so the server loads every part of the model onto the card."""
    async with aiohttp.ClientSession() as http, http.ws_connect(url, max_msg_size=0) as ws:
        await ws.send_json({"type": "session.init", "payload": {"mode": "full_duplex", "use_tts": True}})
        async with asyncio.timeout(CONNECT_SECONDS):
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise RuntimeError("The model server closed the call while loading (another call may be active).")
                if json.loads(message.data).get("type") == "session.created":
                    print("The full-duplex model is loaded.")
                    return
        raise RuntimeError("The model server did not accept the call.")


if __name__ == "__main__":
    if sys.argv[1:2] != ["warm"]:
        sys.exit("usage: duplex.py warm [url]")
    asyncio.run(warm(sys.argv[2] if len(sys.argv) > 2 else "ws://minicpm-omni:9060/backend"))
