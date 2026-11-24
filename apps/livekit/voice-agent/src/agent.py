"""Mu3Lab voice agent: speech in, a language model, speech out.

The answer model (chosen in Mu3Lab's Settings, Voice Assistant) and speech both
go through Mu3Lab's LiteLLM gateway with the agent's own key, and voice activity
and turn taking run inside this container. Every address and model name comes
from the environment, so the same code runs on a desktop for
testing (localhost ports) and inside Mu3Lab (service names).

The person talking is named in their room token (the voice page reads their sign-in). A main
assistant talks with them and hands the conversation to an app's assistant, which has only that
app's tools, reached through ContextForge with the person's own key, and hands it back when they
are done. Mu3Lab writes each person's app assistants to MU3LAB_VOICE_TOOLS (ctl/voice_tools.py).
A tool that changes something only prepares it; it runs once the person has heard the details read
back and agreed in a later turn (confirm_change), exactly as read back.

The main assistant can also search the web privately, through the household's SearXNG app.

The household's choices (Settings, Voice Assistant) reach the agent in MU3LAB_VOICE_TOOLS' sibling
settings.json, read at each call's start, so a change needs no restart (ctl/voice_settings.py).

Speed: the greeting waits until speech recognition, the voice and a local answer model are loaded
(their services unload idle models), and the person hears what is happening while an app is checked.
Each reply logs where its wait went (log_turns).
"""

import asyncio
import io
import json
import logging
import os
import re
import shutil
import time
import wave
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import httpx2
import speech
import websearch
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    APIConnectOptions,
    JobContext,
    JobProcess,
    ModelSettings,
    RunContext,
    TurnHandlingOptions,
    cli,
    function_tool,
    get_job_context,
    inference,
    mcp,
    utils,
)
from livekit.agents import llm as lk_llm
from livekit.agents.llm import ChatContext, LLMError, ToolError
from livekit.agents.llm.mcp import MCPToolset
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.plugins import openai, silero

logger = logging.getLogger("mu3lab-voice-agent")

# Local development only: a .env.local file next to the code. Inside the container the
# values come from compose, so this finds nothing and does nothing.
load_dotenv(".env.local")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


AGENT_NAME = _env("MU3LAB_AGENT_NAME", "mu3lab-agent")
# Mu3Lab's gateway holds the real chat model. Its routes, tried in order, set how much the model
# thinks and when a late model hands over to the next (ctl/local_models.py).
LLM_BASE_URL = _env("MU3LAB_LLM_BASE_URL", "http://litellm:4000/v1")
LLM_API_KEY = _env("MU3LAB_LLM_API_KEY", "not-needed")
LLM_MODEL = _env("MU3LAB_LLM_MODEL", "mu3lab-voice")
# Speech recognition and speech output, on one OpenAI-compatible API: LiteLLM's mu3lab-stt and
# mu3lab-tts in Mu3Lab, or Speaches itself (real model names) when LiteLLM is bypassed.
SPEECH_BASE_URL = _env("MU3LAB_SPEECH_URL", "http://litellm:4000/v1")
SPEECH_API_KEY = _env("MU3LAB_SPEECH_API_KEY", LLM_API_KEY)
STT_MODEL = _env("MU3LAB_STT_MODEL", "mu3lab-stt")
TTS_MODEL = _env("MU3LAB_TTS_MODEL", "mu3lab-tts")
TTS_VOICE = _env("MU3LAB_TTS_VOICE", "af_heart")

VOICE_TOOLS = Path(_env("MU3LAB_VOICE_TOOLS", "voice-tools/people.json"))
# The household's choices, beside the app tools (Settings, Voice Assistant; ctl/voice_settings.py).
VOICE_SETTINGS = VOICE_TOOLS.with_name("settings.json")
# One line as each call starts and ends, for Mu3Lab's Home widget (apps/livekit/widgets.py): the
# room, who called and when. Never anything said. Empty: calls are not noted (a desktop test).
CALL_LOG = _env("MU3LAB_CALL_LOG", "")
# Each call's report (what was said, each turn's timing, and the audio when the household keeps it),
# one folder per call, for administrators to find what went wrong (Settings, Voice assistant).
# Empty: no reports (a desktop test).
REPORTS = _env("MU3LAB_REPORTS", "")
DEFAULT_SETTINGS: dict[str, Any] = {
    "responsiveness": "balanced",
    "interruptions": True,
    "voice": "",
    # SearXNG's address when web search is on and the app is installed; empty means no search.
    "search_url": "",
    # Said when the answer's first words take longer than wait_after seconds; empty is off.
    "wait_phrase": "One moment.",
    "wait_after": 1.5,
    # When nobody has spoken for silence_after seconds, ask whether the person is still there (up to
    # silence_reasks times, the same wait apart), then say silence_goodbye and end the call. 0 is off.
    "silence_after": 30,
    "silence_reasks": 1,
    "silence_goodbye": "I'll end the call now. Talk to you soon.",
    # Call reports: kept unless switched off, with the audio unless that is, for reports_days days.
    "reports": True,
    "reports_audio": True,
    "reports_days": 14,
    # Advanced turn timing (Settings, Voice assistant, Advanced; administrators). Silence that ends a
    # stretch of speech (each stretch goes to speech recognition on its own); the shortest wait before
    # the turn may end; the longest when the person sounds unfinished (0: from Responsiveness); the
    # turn detector's "finished" threshold (0: its own); and how long talking over the assistant must
    # last to stop it.
    "vad_silence": 0.25,
    "min_delay": 0.25,
    "max_delay": 0,
    "eot_threshold": 0,
    "interrupt_after": 0.5,
}
SILENCE_REASK = (
    "The person has been quiet for a while. Briefly ask whether they are still there, or repeat your last "
    "question if you asked one."
)

INSTRUCTIONS = (
    "You are Mu3Lab's voice assistant for a household. Speak in short, plain sentences "
    "that sound natural when read aloud. Do not use lists, markdown or emoji. Say times the way people "
    'say them ("two thirty in the afternoon", "noon"), and dates as words ("Thursday the twenty sixth"). '
    "Never read out IDs, links, file names or raw dates like 2026-11-24T14:00. Round numbers when the "
    "exact figure does not matter."
)
# The person has stopped talking after this much silence (LiveKit's default is 0.5 s). LiveKit
# refuses to start a call with the turn detector below 0.25 s.
END_OF_SPEECH_SECONDS = 0.25
# When the person sounds unfinished, the turn detector waits for more, at most this long
# (LiveKit's default is 3 s). The household chooses it as Responsiveness.
MAX_WAIT_SECONDS = {"fast": 1.0, "balanced": 2.0, "patient": 3.0}
# Speaches unloads idle models after five minutes and Ollama after ten, and loading one takes
# seconds; the greeting waits at most this long for them.
WARM_SECONDS = 60
# How long the agent waits for the answer's first words. Longer than the plugin's 5 s, so the
# gateway can try its next route when one is late (8 s local, then 4 s for the outside list).
ANSWER_WAIT_SECONDS = 30
# LiteLLM alone picks routes and hands a late one over to the next. LiveKit's own retries (3 by
# default, 2 s apart) would stack on top: in the VoiceAgent project a provider error became ~8 s of
# silence. So the agent asks once, and says sorry when no answer comes.
LLM_RETRIES = 0
NO_ANSWER = "Sorry, I couldn't get an answer just then. Please say that again."
# What the person hears while a tool runs and nothing else is being said.
FILLERS = ("One moment.", "Still working on it.")
FILLER_DELAY_SECONDS = 1.0
FILLER_INTERVAL_SECONDS = 5.0
# How much of the conversation an app's assistant (or the main one, back again) is given: enough
# for the request at hand, little enough for a small local model.
HANDOFF_ITEMS = 8
# A tool that changes something only prepares the change; this is added to its description.
PROPOSE_NOTE = (
    " This only prepares the change: nothing happens yet. Read the returned details back to the person in "
    "plain words and ask whether to go ahead, then wait for their answer."
)


def now_line() -> str:
    """Today's date and time where the household lives, so "tomorrow at 10" means 10 here."""
    name = _env("TZ", "UTC")
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        name, zone = "UTC", ZoneInfo("UTC")
    now = datetime.now(zone)
    offset = now.strftime("%z")
    # Small models get weekday arithmetic wrong; the next days with their dates are looked up instead.
    return (
        f"It is now {now:%A %d %B %Y, %H:%M} in {name} (UTC{offset[:3]}:{offset[3:]}). Give and take times in this "
        "time zone, and send tools times with this UTC offset. Never work a date out yourself; use this list. "
        '"This Thursday" is the first Thursday in it; if "next Thursday" could mean either, say the date and '
        "check.\n" + speech.upcoming_days(now.date())
    )


def household_zone() -> ZoneInfo:
    try:
        return ZoneInfo(_env("TZ", "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def load_people() -> dict[str, Any]:
    """Everyone's app assistants as Mu3Lab wrote them; empty before Mu3Lab has."""
    try:
        return json.loads(VOICE_TOOLS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("No voice tools file at %s; the assistant has no app tools.", VOICE_TOOLS)
        return {}


def load_settings() -> dict[str, Any]:
    """The household's choices for this call; the defaults until Mu3Lab has written them."""
    try:
        data = json.loads(VOICE_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("No voice settings at %s (%s); using the defaults.", VOICE_SETTINGS, exc)
        data = {}
    settings = {**DEFAULT_SETTINGS, **(data if isinstance(data, dict) else {})}
    if settings["responsiveness"] not in MAX_WAIT_SECONDS:
        settings["responsiveness"] = DEFAULT_SETTINGS["responsiveness"]
    settings["voice"] = str(settings["voice"] or TTS_VOICE)
    settings["wait_phrase"] = str(settings["wait_phrase"] or "")[:80]
    settings["wait_after"] = _number(settings["wait_after"], DEFAULT_SETTINGS["wait_after"], 0.5, 10)
    silence = _number(settings["silence_after"], DEFAULT_SETTINGS["silence_after"], 0, 600)
    settings["silence_after"] = silence if silence >= 10 else 0
    settings["silence_reasks"] = int(_number(settings["silence_reasks"], DEFAULT_SETTINGS["silence_reasks"], 0, 3))
    settings["silence_goodbye"] = str(settings["silence_goodbye"] or "")[:160]
    settings["reports"] = bool(settings["reports"])
    settings["reports_audio"] = bool(settings["reports_audio"]) and settings["reports"]
    settings["reports_days"] = int(_number(settings["reports_days"], DEFAULT_SETTINGS["reports_days"], 1, 365))
    settings["vad_silence"] = _number(settings["vad_silence"], DEFAULT_SETTINGS["vad_silence"], 0.1, 1.5)
    # LiveKit refuses a call with the turn detector below 0.25 s.
    settings["min_delay"] = _number(settings["min_delay"], DEFAULT_SETTINGS["min_delay"], END_OF_SPEECH_SECONDS, 2)
    longest = _number(settings["max_delay"], 0, 0, 6)
    settings["max_delay"] = max(longest, settings["min_delay"]) if longest else 0
    threshold = _number(settings["eot_threshold"], 0, 0, 0.99)
    settings["eot_threshold"] = threshold if threshold >= 0.01 else 0
    settings["interrupt_after"] = _number(settings["interrupt_after"], DEFAULT_SETTINGS["interrupt_after"], 0.2, 2)
    return settings


def turn_handling(settings: dict[str, Any]) -> TurnHandlingOptions:
    """How the call decides the person has finished, and when talking over the assistant stops it."""
    detector: dict[str, Any] = {"version": "v1-mini"}
    if settings["eot_threshold"]:
        detector["unlikely_threshold"] = settings["eot_threshold"]
    return TurnHandlingOptions(
        # End-of-turn model that runs locally. "v1-mini" is the local model; the default "v1" would call
        # LiveKit's hosted gateway.
        turn_detection=inference.TurnDetector(**detector),
        endpointing={
            "min_delay": settings["min_delay"],
            "max_delay": settings["max_delay"] or MAX_WAIT_SECONDS[settings["responsiveness"]],
        },
        # Talking over the assistant stops it, judged by voice activity alone ("adaptive" would ask
        # LiveKit Cloud). A false interruption (a cough, the TV) resumes the reply (LiveKit's default).
        interruption={
            "enabled": bool(settings["interruptions"]),
            "mode": "vad",
            "min_duration": settings["interrupt_after"],
        },
        # Prepare the answer and its first spoken sentence before the turn is confirmed.
        preemptive_generation={"enabled": True, "preemptive_tts": True},
    )


def _number(value: Any, default: float, low: float, high: float) -> float:
    """A number from the settings file, kept within sensible bounds; the default when it is not one."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high) if number == number else default


def _silence() -> bytes:
    """Half a second of silence as a WAV file: enough to load the speech recognition model."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(16000))
    return buffer.getvalue()


async def _post(url: str, key: str, **request: Any) -> None:
    http = utils.http_context.http_session()
    timeout = aiohttp.ClientTimeout(total=WARM_SECONDS)
    async with http.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=timeout, **request) as reply:
        reply.raise_for_status()
        await reply.read()


async def warm_speech(voice: str) -> None:
    """Load speech recognition and the voice by using each once."""
    form = aiohttp.FormData()
    form.add_field("model", STT_MODEL)
    form.add_field("language", "en")
    form.add_field("file", _silence(), filename="silence.wav", content_type="audio/wav")
    await asyncio.gather(
        _post(f"{SPEECH_BASE_URL}/audio/transcriptions", SPEECH_API_KEY, data=form),
        _post(
            f"{SPEECH_BASE_URL}/audio/speech",
            SPEECH_API_KEY,
            json={"model": TTS_MODEL, "voice": voice, "input": "Hi.", "response_format": "pcm"},
        ),
    )


async def warm_answer_model(instructions: str) -> None:
    """Load a local answer model with this call's instructions, which Ollama then keeps ready too.

    Outside providers have nothing to load, and a request would spend their free allowance, so
    only a first route to Ollama is warmed. The agent's own key may read where its model runs.
    """
    http = utils.http_context.http_session()
    async with http.get(
        f"{LLM_BASE_URL}/model/info",
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as reply:
        reply.raise_for_status()
        routes = (await reply.json()).get("data", [])
    voice = sorted(
        (r.get("litellm_params") or {} for r in routes if r.get("model_name") == LLM_MODEL),
        key=lambda params: params.get("order") or 0,
    )
    if not voice or not str(voice[0].get("model", "")).startswith("ollama"):
        return
    await _post(
        f"{LLM_BASE_URL}/chat/completions",
        LLM_API_KEY,
        json={
            "model": LLM_MODEL,
            "max_tokens": 1,
            "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": "Hi"}],
        },
    )


async def wait_until_warm(*tasks: asyncio.Task) -> None:
    """Wait for the warm-up; a model that did not load is logged, and the call goes on."""
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), WARM_SECONDS)
    except TimeoutError:
        logger.warning("Models were still loading after %s s; greeting anyway.", WARM_SECONDS)
        return
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("A model could not be loaded before the call: %s", result)


@dataclass
class CallState:
    """One call's state, on ``session.userdata``: who is talking and the household's settings,
    kept apart from anything the model says."""

    settings: dict[str, Any]
    person: dict[str, Any] | None = None
    guide: str = ""
    # When the wait phrase was last said (monotonic seconds), so a tool's filler does not repeat it.
    waited_at: float = 0.0
    # The person's finished turns so far: a change is confirmed only by a later one.
    user_turns: int = 0
    # The latest change a tool prepared; only it can be confirmed, once.
    pending: "PendingChange | None" = None
    proposals: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def propose(self, toolset: str, tool: str, arguments: dict[str, Any]) -> "PendingChange":
        """Hold a change for the person to agree to; it replaces any earlier one."""
        self.proposals += 1
        self.pending = PendingChange(
            action_id=f"C{self.proposals}",
            toolset=toolset,
            tool=tool,
            arguments=arguments,
            turn=self.user_turns,
            at=time.time(),
        )
        return self.pending

    def heard(self, role: str, interrupted: bool, started_at: float) -> None:
        """A message joined the conversation. The person's count as turns. The assistant's, when it
        began after the change was prepared (not words said before the tool ran) and played to the
        end, mean the person heard the read-back."""
        pending = self.pending
        if role == "user":
            self.user_turns += 1
        elif (
            role == "assistant"
            and pending
            and not interrupted
            and started_at >= pending.at
            and self.user_turns == pending.turn
        ):
            pending.heard = True

    def take(self, toolset: str, action_id: str) -> "PendingChange":
        """The change to run now, or a ToolError telling the model its next step. Code owns this
        decision; the model only judged that the person meant yes."""
        pending = self.pending
        if pending is None or pending.action_id != action_id or pending.toolset != toolset:
            raise ToolError("That is not the latest prepared change. Prepare the change again.")
        if not pending.heard:
            raise ToolError("The person has not heard the details in full. Read the change back to them and ask again.")
        if self.user_turns <= pending.turn:
            raise ToolError("The person has not answered yet. Wait for their answer.")
        self.pending = None
        return pending


@dataclass
class PendingChange:
    """A change a tool prepared: exactly what will run once the person agrees."""

    action_id: str
    toolset: str
    tool: str
    arguments: dict[str, Any]
    # The person's turn count and the time (seconds since 1970) when it was prepared, and whether
    # its read-back played in full.
    turn: int
    at: float
    heard: bool = False

    @property
    def search_url(self) -> str:
        return str(self.settings.get("search_url") or "")


def _fillers(state: CallState):
    """What the person hears while a tool runs. Right after the wait phrase ("One moment."), the
    first filler waits one more interval and the second ("Still working on it.") is said instead."""
    waited = time.monotonic() - state.waited_at < FILLER_INTERVAL_SECONDS
    skipped = False

    def filler(step: int) -> str | None:
        nonlocal skipped
        if waited and not skipped:
            skipped = True
            return None  # LiveKit tries again after FILLER_INTERVAL_SECONDS
        index = step + (1 if waited else 0)
        return FILLERS[index] if index < len(FILLERS) else None

    return filler


class SpokenReplies:
    """For an assistant: its answers are cleaned for the voice (``speech.clean_reply``) and, when the
    first words are slow, start with the household's wait phrase. Put it first in the bases."""

    async def llm_node(
        self, chat_ctx: ChatContext, tools: list[lk_llm.Tool], model_settings: ModelSettings
    ) -> AsyncIterator:
        state: CallState = self.session.userdata  # type: ignore[attr-defined]
        stream = speech.clean_reply(Agent.default.llm_node(self, chat_ctx, tools, model_settings))  # type: ignore[arg-type]

        def said() -> None:
            state.waited_at = time.monotonic()

        settings = state.settings
        async for chunk in speech.with_wait_phrase(stream, settings["wait_after"], settings["wait_phrase"], said):
            yield chunk

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings) -> AsyncIterator:
        """The voice, as LiveKit makes it, with the gaps logged: moments when the next audio was ready
        only after the audio before it had finished playing, which make a voice sound broken."""
        gaps = speech.GapMeter()
        async for frame in Agent.default.tts_node(self, text, model_settings):  # type: ignore[arg-type]
            gaps.add(time.monotonic(), frame.duration)
            yield frame
        if gaps.frames:
            logger.info(
                "Voice: %s of audio, %d gaps totalling %s (longest %s)",
                _seconds(gaps.audio),
                gaps.count,
                _seconds(gaps.total),
                _seconds(gaps.longest),
            )


def _seconds(value: Any) -> str:
    return f"{value:.2f} s" if isinstance(value, (int, float)) else "?"


def log_turns(session: AgentSession) -> None:
    """Log one line per reply saying where the wait went, from LiveKit's own per-turn measurements.

    Read them with ``docker logs mu3lab-livekit-agent-1 2>&1 | grep "Reply after"``. The parts can
    overlap: the answer is prepared before the turn is confirmed (preemptive generation).
    """
    heard: dict[str, Any] = {}

    @session.on("conversation_item_added")
    def added(event) -> None:
        item = event.item
        if getattr(item, "type", "") != "message":
            return
        metrics = dict(getattr(item, "metrics", None) or {})
        if item.role == "user":
            heard.clear()
            heard.update(metrics)
            return
        if item.role == "assistant" and getattr(item, "interrupted", False):
            played = len((item.text_content or "").split())
            logger.info("The person talked over the reply; %d words of it had played.", played)
        if item.role != "assistant" or "e2e_latency" not in metrics:
            return
        spoke = None
        if "started_speaking_at" in heard and "stopped_speaking_at" in heard:
            spoke = heard["stopped_speaking_at"] - heard["started_speaking_at"]
        agent = session.current_agent
        logger.info(
            "Reply after %s from the end of speech (%s): transcript %s, end of turn %s, first words %s, "
            "first sound %s; the person spoke for %s",
            _seconds(metrics["e2e_latency"]),
            getattr(agent, "title", "main assistant"),
            _seconds(heard.get("transcription_delay")),
            _seconds(heard.get("end_of_turn_delay")),
            _seconds(metrics.get("llm_node_ttft")),
            _seconds(metrics.get("tts_node_ttfb")),
            _seconds(spoke),
        )


class _EndOfTurnScore(logging.Filter):
    """On LiveKit's logger: its "eot prediction" debug line becomes one readable INFO line (the turn
    detector's score, its threshold and the wait it chose); its other debug lines are dropped."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.INFO:
            return True
        if record.msg != "eot prediction":
            return False
        found = record.__dict__
        record.levelno, record.levelname = logging.INFO, "INFO"
        record.msg = "End of turn: score %s against threshold %s, so waited %s (%s)"
        record.args = (
            _score(found.get("probability")),
            _score(found.get("unlikely_threshold")),
            _seconds(found.get("endpointing_delay")),
            found.get("trigger") or "?",
        )
        return True


class _InfoAndUp(logging.Filter):
    """On the root handlers: drop the debug lines LiveKit's child loggers make once its level is DEBUG."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.INFO


def _score(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "?"


def log_end_of_turn_scores() -> None:
    """Log the turn detector's score on every turn, beside "Reply after", to tune Responsiveness from
    real turns (plan item 1.5). LiveKit logs it only at debug level. Once per process."""
    lk_logger = logging.getLogger("livekit.agents")
    if any(isinstance(item, _EndOfTurnScore) for item in lk_logger.filters):
        return
    for handler in logging.getLogger().handlers:
        handler.addFilter(_InfoAndUp())
    lk_logger.addFilter(_EndOfTurnScore())
    lk_logger.setLevel(logging.DEBUG)


async def end_call(session: AgentSession, reason: str) -> None:
    """End the call: close the session and the room, which ends it on the person's page too."""
    job = get_job_context()

    async def delete_room() -> None:
        await job.delete_room()

    job.add_shutdown_callback(delete_room)
    session.shutdown()
    job.shutdown(reason=reason)


def check_on_silence(session: AgentSession, settings: dict[str, Any], end=end_call) -> None:
    """When nobody has spoken for a while (LiveKit marks the person "away" after silence_after
    seconds), ask whether they are still there, up to silence_reasks times the same wait apart, then
    say goodbye and end the call, so an open page does not keep the speech and answer models busy.
    Any sound from the person stops it."""
    task: asyncio.Task | None = None

    async def check_in() -> None:
        for _ in range(settings["silence_reasks"]):
            logger.info("The person has been quiet; checking they are still there.")
            await session.generate_reply(instructions=SILENCE_REASK)
            await asyncio.sleep(settings["silence_after"])
        logger.info("The person has been quiet; ending the call.")
        if settings["silence_goodbye"]:
            await session.say(settings["silence_goodbye"], allow_interruptions=False, add_to_chat_ctx=False)
        await end(session, "the person was quiet")

    @session.on("user_state_changed")
    def changed(event) -> None:
        nonlocal task
        if event.new_state == "away":
            if task is None or task.done():
                task = asyncio.create_task(check_in())
        elif task is not None and not task.done():
            task.cancel()  # they are back; whatever is being said still plays
            task = None


def follow_turns(session: AgentSession, state: CallState) -> None:
    """Count the person's turns and whether a read-back was heard in full, for confirm_change."""

    @session.on("conversation_item_added")
    def added(event) -> None:
        item = event.item
        if getattr(item, "type", "") == "message":
            state.heard(item.role, bool(getattr(item, "interrupted", False)), float(item.created_at))


def apologize_on_failure(session: AgentSession) -> None:
    """When the answer model gives up (every route failed), say so instead of going quiet."""

    @session.on("error")
    def failed(event) -> None:
        if isinstance(event.error, LLMError) and not event.error.recoverable:
            logger.warning("No answer from the model: %s", event.error.label)
            session.say(NO_ANSWER, add_to_chat_ctx=False)


def log_interruptions(session: AgentSession) -> None:
    """Log each interruption LiveKit judged false (a cough, an "mm-hm", the TV), and whether the reply
    carried on, to tune Advanced's interruption length (plan item 3.3)."""

    @session.on("agent_false_interruption")
    def false_interruption(event) -> None:
        logger.info("A false interruption; the reply %s.", "carried on" if event.resumed else "stopped")


def _handoff_context(agent: Agent) -> ChatContext:
    """The recent conversation, without tool calls or the other assistant's instructions."""
    return agent.chat_ctx.copy(exclude_function_call=True, exclude_instructions=True).truncate(max_items=HANDOFF_ITEMS)


# An ISO 8601 date and time with an offset, as app tools return them: 2026-11-24T14:00:00-05:00.
ISO_MOMENT = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})\b")


def speakable(result: Any) -> Any:
    """A tool's text result with each date and time it holds said in words, in the household's time
    zone, so the model reads "Thursday, November twenty sixth at two in the afternoon" rather than
    working it out. Other results pass unchanged."""
    if not isinstance(result, str):
        return result
    spoken = {}
    for found in dict.fromkeys(ISO_MOMENT.findall(result)):
        try:
            moment = datetime.fromisoformat(found.replace("Z", "+00:00")).astimezone(household_zone())
        except ValueError:
            continue
        spoken[found] = speech.say_when(moment)
        if len(spoken) == 20:
            break
    if not spoken:
        return result
    return result + "\n\nSay these times as: " + "; ".join(f"{iso} = {words}" for iso, words in spoken.items())


def _readable(name: str) -> str:
    """'nextcloud-create_event' reads as 'create event'."""
    return name.rsplit("-", 1)[-1].replace("_", " ")


class ConfirmedToolset(MCPToolset):
    """One app's tools through ContextForge. A tool that only reads runs at once. Any other only
    prepares its change (``CallState.propose``); ``confirm_change`` runs exactly those arguments
    once the person has heard them read back and agreed in a later turn (``CallState.take``).
    While a tool runs and nothing is being said, the person hears that it is still working.
    """

    def __init__(self, *, id: str, mcp_server: mcp.MCPServer, reads: list[str]) -> None:
        super().__init__(id=id, mcp_server=mcp_server)
        self._reads = tuple(f"-{name}" for name in reads)
        self._writes: dict[str, Any] = {}

    async def setup(self, *, reload: bool = False):
        await super().setup(reload=reload)
        self._tools = self.arrange(self._tools)
        return self

    def arrange(self, found: list) -> list:
        """The tools the assistant gets for the app's MCP tools: reads as they are, each write as a
        tool that prepares it, and confirm_change when there is any write."""
        tools = []
        self._writes = {}
        for tool in found:
            if tool.info.name.endswith(self._reads):
                tools.append(self._run(tool))
            else:
                self._writes[tool.info.name] = tool
                tools.append(self._prepare(tool))
        if self._writes:
            tools.append(self._confirm())
        return tools

    @staticmethod
    async def _call(tool, arguments: dict[str, Any], context: RunContext) -> Any:
        started = time.monotonic()
        try:
            async with context.with_filler(
                _fillers(context.userdata),
                delay=FILLER_DELAY_SECONDS,
                interval=FILLER_INTERVAL_SECONDS,
                max_steps=len(FILLERS),
            ):
                return speakable(await tool(raw_arguments=arguments))
        finally:
            logger.info("Tool %s took %s", tool.info.name, _seconds(time.monotonic() - started))

    def _run(self, tool):
        async def call(raw_arguments: dict[str, Any], context: RunContext) -> Any:
            return await self._call(tool, raw_arguments, context)

        return function_tool(call, raw_schema=tool.info.raw_schema)

    def _prepare(self, tool):
        schema = dict(tool.info.raw_schema)
        schema["description"] = str(schema.get("description") or "").rstrip() + PROPOSE_NOTE

        async def prepare(raw_arguments: dict[str, Any], context: RunContext) -> dict[str, Any]:
            pending = context.userdata.propose(self.id, tool.info.name, dict(raw_arguments))
            logger.info("Prepared %s as %s; waiting for the person.", tool.info.name, pending.action_id)
            return {
                "status": "needs_confirmation",
                "action_id": pending.action_id,
                "change": _readable(tool.info.name),
                "details": pending.arguments,
                "next": "Read these details back in plain words and ask whether to go ahead. If they agree, "
                "call confirm_change with this action_id. If they change anything, prepare it again.",
            }

        return function_tool(prepare, raw_schema=schema)

    def _confirm(self):
        async def confirm_change(context: RunContext, action_id: str) -> Any:
            """Make the change prepared most recently, exactly as it was read back. Call it only after
            the person heard the details and agreed in their answer; one call makes one change.

            Args:
                action_id: The action_id the preparing tool returned.
            """
            pending = context.userdata.take(self.id, action_id)
            logger.info("Making %s (%s), as the person agreed.", pending.tool, pending.action_id)
            return await self._call(self._writes[pending.tool], pending.arguments, context)

        return function_tool(confirm_change)


def search_tool(base_url: str):
    """The main assistant's web search, through the household's SearXNG app."""

    async def search_web(context: RunContext, query: str) -> str:
        """Search the internet for current facts: news, weather, opening hours, prices, sports and recent events.

        Args:
            query: A few search words.
        """
        started = time.monotonic()
        async with context.with_filler(
            _fillers(context.userdata),
            delay=FILLER_DELAY_SECONDS,
            interval=FILLER_INTERVAL_SECONDS,
            max_steps=len(FILLERS),
        ):
            found = await websearch.search(utils.http_context.http_session(), base_url, query)
        logger.info("Web search took %s", _seconds(time.monotonic() - started))
        return found

    return function_tool(search_web)


class Assistant(SpokenReplies, Agent):
    """The main assistant: answers general questions and hands the person to an app's assistant."""

    def __init__(self, state: CallState, chat_ctx: ChatContext | None = None) -> None:
        self.state = state
        person, guide, search_url = state.person, state.guide, state.search_url
        agents = (person or {}).get("agents", [])
        if person is None:
            situation = (
                "You do not know who is talking, so you cannot reach anyone's apps. If asked, say that "
                "the person should open the voice assistant from Mu3Lab, signed in."
            )
        elif agents:
            situation = (
                f"You are talking with {person['name']}. You cannot see their app data yourself. When they want "
                "to see or do something in one of these apps, call its open_ tool at once, without announcing it; "
                "that app's assistant then answers them: "
                + "; ".join(f"{agent['title']} ({agent['description']})" for agent in agents)
            )
        else:
            situation = (
                f"You are talking with {person['name']}. No app assistants are switched on for voice yet, so you "
                "cannot see or change anything in their apps; an administrator turns them on in Mu3Lab."
            )
        instructions = f"{INSTRUCTIONS}\n\n{now_line()}\n\n{situation}"
        tools = [self._open(agent) for agent in agents]
        if search_url:
            instructions += (
                "\n\nFor current facts you do not know for certain (news, weather, prices, opening hours, sports, "
                "recent events), call search_web, then answer in one or two sentences from what it finds. Never "
                "read out links."
            )
            tools.append(search_tool(search_url))
        if guide:
            instructions += f"\n\nThe household's apps, for general questions:\n{guide}"
        super().__init__(instructions=instructions, tools=tools, chat_ctx=chat_ctx)
        self._handed_back = chat_ctx is not None

    def _open(self, spec: dict[str, Any]):
        async def open_app(context: RunContext):
            return AppAssistant(spec, self.state, _handoff_context(self))

        return function_tool(
            open_app,
            name=f"open_{re.sub(r'[^a-z0-9]+', '_', spec['id'].lower())}",
            description=f"Hand the person to the {spec['title']} assistant: {spec['description']}",
        )

    async def on_enter(self) -> None:
        # Back from an app's assistant: carry on with what the person asked.
        if self._handed_back:
            self.session.generate_reply()


class AppAssistant(SpokenReplies, Agent):
    """One app's assistant, with only that app's tools, acting as the person talking."""

    def __init__(self, spec: dict[str, Any], state: CallState, chat_ctx: ChatContext) -> None:
        self.state, self.title = state, spec["title"]
        server = mcp.MCPServerHTTP(
            url=spec["url"],
            headers={"Authorization": f"Bearer {spec['token']}"},
            timeout=10,
            client_session_timeout_seconds=30,
        )
        super().__init__(
            instructions=f"{now_line()}\n\n{spec['instructions']}",
            # back_to_main below joins these by itself.
            tools=[ConfirmedToolset(id=spec["id"], mcp_server=server, reads=spec.get("reads", []))],
            chat_ctx=chat_ctx,
        )

    @function_tool
    async def back_to_main(self, context: RunContext):
        """Hand the person back to the main assistant: when they are done here or ask about another app."""
        return Assistant(self.state, _handoff_context(self))

    async def on_enter(self) -> None:
        # Say what is happening at once; the answer is worked out while this plays. Kept out of the
        # history, so the model does not say it again.
        self.session.say(f"Let me check {self.title}.", add_to_chat_ctx=False)
        # Answer the request the main assistant handed over, without a second lead-in.
        self.session.generate_reply(
            instructions=f'You have just said "Let me check {self.title}." Do not say that or anything like it '
            "again: go straight to the answer, or to the tool you need."
        )


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    # The OpenAI client imports its chat modules on the first request, which held up the start of
    # every call by ~140 ms in the VoiceAgent project. Import them here, before any call.
    import openai.pagination
    import openai.resources.chat.completions.completions  # noqa: F401

    # Voice activity: local Silero model, so no LiveKit Cloud call. Loaded once per worker
    # process, before any call, instead of at the start of each.
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=END_OF_SPEECH_SECONDS)


server.setup_fnc = prewarm


def recording(settings: dict[str, Any]) -> dict[str, bool] | bool:
    """What LiveKit records for the call's report. Self-hosted, nothing is uploaded: the report and
    audio stay on this server (save_report copies them into REPORTS)."""
    if not (REPORTS and settings["reports"]):
        return False
    return {"audio": settings["reports_audio"], "transcript": True, "traces": False, "logs": False}


def prune_reports(folder: Path, days: int, now: float | None = None) -> int:
    """Delete reports older than ``days``; returns how many went."""
    cutoff = (now or time.time()) - days * 86400
    removed = 0
    for report in folder.iterdir() if folder.is_dir() else []:
        try:
            if report.is_dir() and report.stat().st_mtime < cutoff:
                shutil.rmtree(report)
                removed += 1
        except OSError:
            logger.warning("An old call report could not be deleted: %s", report.name)
    return removed


async def save_report(ctx: JobContext) -> None:
    """After a call: its report (each message, tool call and turn timing) and, when kept, its audio, in
    REPORTS/<time>_<room>/, with who called; then reports older than the household keeps are deleted."""
    state: CallState | None = ctx.proc.userdata.get("sessions", {}).pop(ctx.room.name, None)
    settings = state.settings if state else load_settings()
    if not (REPORTS and settings["reports"]):
        return
    try:
        report = ctx.make_session_report()
    except RuntimeError as exc:  # no session started, or the recording did not close
        logger.warning("No call report: %s", exc)
        return
    folder = Path(REPORTS)
    target = folder / f"{datetime.now(UTC):%Y%m%d-%H%M%SZ}_{re.sub(r'[^A-Za-z0-9_-]', '_', ctx.room.name)}"
    audio = report.audio_recording_path
    about = {
        "room": ctx.room.name,
        "who": str(state.extra.get("identity") or "") if state else "",
        "name": str((state.person or {}).get("name") or "") if state else "",
        "started_at": report.started_at,
        "ended_at": report.timestamp,
        "audio": bool(audio and audio.exists()),
    }

    def write() -> None:
        target.mkdir(parents=True, exist_ok=True)
        (target / "report.json").write_text(json.dumps(report.to_dict(), default=str), encoding="utf-8")
        (target / "call.json").write_text(json.dumps(about), encoding="utf-8")
        if about["audio"]:
            shutil.copy(audio, target / "audio.ogg")
        prune_reports(folder, settings["reports_days"])

    try:
        await asyncio.to_thread(write)
    except OSError:
        logger.warning("The call report could not be saved.", exc_info=True)
        return
    logger.info("Call report saved as %s.", target.name)


def note_call(event: str, room: str, who: str = "") -> None:
    """Add a call's start or end to the call log; a failure only costs the widget a line."""
    if not CALL_LOG:
        return
    line = {"event": event, "room": room, "at": round(time.time(), 1)}
    if who:
        line["who"] = who
    try:
        with open(CALL_LOG, "a", encoding="utf-8") as log:
            log.write(json.dumps(line) + "\n")
    except OSError:
        logger.warning("The call could not be noted for Mu3Lab's Home widget.", exc_info=True)


@server.rtc_session(agent_name=AGENT_NAME, on_session_end=save_report)
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    log_end_of_turn_scores()
    settings = load_settings()
    # Speech models load while the person joins.
    speech_ready = asyncio.create_task(warm_speech(settings["voice"]))

    state = CallState(settings=settings)
    vad: silero.VAD = ctx.proc.userdata["vad"]
    vad.update_options(min_silence_duration=settings["vad_silence"])
    session = AgentSession[CallState](
        userdata=state,
        vad=vad,
        stt=openai.STT(
            base_url=SPEECH_BASE_URL,
            api_key=SPEECH_API_KEY,
            model=STT_MODEL,
            language="en",
            # Speaches' REST transcription endpoint, one request per utterance.
            use_realtime=False,
        ),
        # No reasoning level here: the route in LiteLLM sets it (see LLM_BASE_URL above).
        llm=openai.LLM(
            model=LLM_MODEL,
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            # One tool call at a time: small models otherwise make two conflicting calls in one turn.
            parallel_tool_calls=False,
            timeout=httpx2.Timeout(connect=15.0, read=ANSWER_WAIT_SECONDS, write=5.0, pool=5.0),
        ),
        conn_options=SessionConnectOptions(llm_conn_options=APIConnectOptions(max_retry=LLM_RETRIES)),
        tts=openai.TTS(
            base_url=SPEECH_BASE_URL,
            api_key=SPEECH_API_KEY,
            model=TTS_MODEL,
            voice=settings["voice"],
            # Raw 24 kHz audio, as the plugin plays it: no MP3 to encode in Speaches and decode here.
            response_format="pcm",
        ),
        turn_handling=turn_handling(settings),
        # The person counts as away after this much silence on both sides (check_on_silence).
        user_away_timeout=settings["silence_after"] or None,
    )

    log_turns(session)
    log_interruptions(session)
    apologize_on_failure(session)
    follow_turns(session, state)
    if settings["silence_after"]:
        check_on_silence(session, settings)

    await ctx.connect()
    # The person's identity is their email, set from their sign-in by the voice page.
    participant = await ctx.wait_for_participant()
    room = ctx.room.name
    note_call("start", room, participant.identity.lower())
    state.extra["identity"] = participant.identity.lower()
    ctx.proc.userdata.setdefault("sessions", {})[room] = state

    async def ended(_reason: str = "") -> None:
        note_call("end", room)

    ctx.add_shutdown_callback(ended)
    people = load_people()
    state.person = (people.get("people") or {}).get(participant.identity.lower())
    state.guide = str(people.get("guide") or "")
    if state.person is None:
        logger.warning("No voice tools for this caller; answering without app tools.")
    assistant = Assistant(state)
    answer_ready = asyncio.create_task(warm_answer_model(assistant.instructions))
    await session.start(agent=assistant, room=ctx.room, record=recording(settings))
    await wait_until_warm(speech_ready, answer_ready)
    # Speak first, once everything is loaded, so the first answer is quick. A fixed sentence needs
    # no model call: an instruction-only request has no messages, which Gemini rejects. Kept out
    # of the chat history so the model's first request still starts with the person's words.
    session.say("Hi, how can I help?", add_to_chat_ctx=False)


if __name__ == "__main__":
    cli.run_app(server)
