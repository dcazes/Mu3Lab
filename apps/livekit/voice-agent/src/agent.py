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
A tool that changes something runs only right after the person says yes.

Mu3Lab can switch the assistant to one full-duplex model instead (duplex.py): it listens and speaks
at once, and its only tool is web search.

Speed: the greeting waits until speech recognition, the voice and a local answer model are loaded
(their services unload idle models), and the person hears what is happening while an app is checked.
"""

import asyncio
import io
import json
import logging
import os
import re
import wave
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import duplex
import httpx2
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    TurnHandlingOptions,
    cli,
    function_tool,
    inference,
    mcp,
    utils,
)
from livekit.agents.llm import ChatContext, ToolError
from livekit.agents.llm.mcp import MCPToolset
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
# How the assistant talks, beside the app tools: separate steps, or one full-duplex model
# (Settings, Voice Assistant; ctl/duplex_voice.py).
VOICE_ENGINE = VOICE_TOOLS.with_name("engine.json")

INSTRUCTIONS = (
    "You are Mu3Lab's voice assistant for a household. Speak in short, plain sentences "
    "that sound natural when read aloud. Do not use lists, markdown or emoji."
)
# The person has stopped talking after this much silence (LiveKit's default is 0.5 s). The turn
# detector still waits longer, up to 3 s, when they sound unfinished. LiveKit refuses to start a
# call with the turn detector below 0.25 s.
END_OF_SPEECH_SECONDS = 0.25
# Speaches unloads idle models after five minutes and Ollama after ten, and loading one takes
# seconds; the greeting waits at most this long for them.
WARM_SECONDS = 60
# How long the agent waits for the answer's first words. Longer than the plugin's 5 s, so the
# gateway can try its next route when one is late (8 s local, then 4 s for the outside list).
ANSWER_WAIT_SECONDS = 30
# What the person hears while an app's tool runs and nothing else is being said.
FILLERS = ("One moment.", "Still working on it.")
FILLER_DELAY_SECONDS = 1.0
FILLER_INTERVAL_SECONDS = 5.0
# How much of the conversation an app's assistant (or the main one, back again) is given: enough
# for the request at hand, little enough for a small local model.
HANDOFF_ITEMS = 8
# Words that answer "Shall I ...?" with yes, at the start of what the person said.
YES = re.compile(
    r"^\W*(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|please do|please|correct|confirm|that's right|right)\b",
    re.IGNORECASE,
)
ASK_FIRST = (
    "Not done: this changes something. Tell the person exactly what you will change and ask them to say yes. "
    "Call this tool again only after they say yes."
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
    return (
        f"It is now {now:%A %d %B %Y, %H:%M} in {name} (UTC{offset[:3]}:{offset[3:]}). Give and take times in this "
        "time zone, and send tools times with this UTC offset."
    )


def load_people() -> dict[str, Any]:
    """Everyone's app assistants as Mu3Lab wrote them; empty before Mu3Lab has."""
    try:
        return json.loads(VOICE_TOOLS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("No voice tools file at %s; the assistant has no app tools.", VOICE_TOOLS)
        return {}


def load_engine() -> dict[str, Any]:
    """How this call talks; separate steps until Mu3Lab says otherwise."""
    try:
        data = json.loads(VOICE_ENGINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"engine": "pipeline"}
    return data if isinstance(data, dict) else {"engine": "pipeline"}


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


async def warm_speech() -> None:
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
            json={"model": TTS_MODEL, "voice": TTS_VOICE, "input": "Hi.", "response_format": "pcm"},
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


def _filler(step: int) -> str | None:
    return FILLERS[step] if step < len(FILLERS) else None


def _handoff_context(agent: Agent) -> ChatContext:
    """The recent conversation, without tool calls or the other assistant's instructions."""
    return agent.chat_ctx.copy(exclude_function_call=True, exclude_instructions=True).truncate(max_items=HANDOFF_ITEMS)


def _latest_words(history: ChatContext) -> tuple[str, str] | None:
    """The ID and text of the person's latest message."""
    for item in reversed(history.items):
        if getattr(item, "type", "") == "message" and item.role == "user":
            return item.id, item.text_content or ""
    return None


class ConfirmedToolset(MCPToolset):
    """One app's tools through ContextForge; a tool that is not read-only runs only after a spoken yes.

    Each yes allows one change, so a second change needs its own. While a tool runs and nothing is
    being said, the person hears that it is still working.
    """

    def __init__(self, *, id: str, mcp_server: mcp.MCPServer, reads: list[str]) -> None:
        super().__init__(id=id, mcp_server=mcp_server)
        self._reads = tuple(f"-{name}" for name in reads)
        self._used: set[str] = set()

    async def setup(self, *, reload: bool = False):
        await super().setup(reload=reload)
        self._tools = [self._wrap(tool, read=tool.info.name.endswith(self._reads)) for tool in self._tools]
        return self

    def _wrap(self, tool, *, read: bool):
        async def call(raw_arguments: dict[str, Any], context: RunContext) -> Any:
            if not read:
                said = _latest_words(context.session.history)
                if said is None or said[0] in self._used or not YES.search(said[1]):
                    raise ToolError(ASK_FIRST)
                self._used.add(said[0])
            async with context.with_filler(
                _filler, delay=FILLER_DELAY_SECONDS, interval=FILLER_INTERVAL_SECONDS, max_steps=len(FILLERS)
            ):
                return await tool(raw_arguments=raw_arguments)

        return function_tool(call, raw_schema=tool.info.raw_schema)


class Assistant(Agent):
    """The main assistant: answers general questions and hands the person to an app's assistant."""

    def __init__(self, person: dict[str, Any] | None, guide: str, chat_ctx: ChatContext | None = None) -> None:
        self.person, self.guide = person, guide
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
        if guide:
            instructions += f"\n\nThe household's apps, for general questions:\n{guide}"
        super().__init__(
            instructions=instructions,
            tools=[self._open(agent) for agent in agents],
            chat_ctx=chat_ctx,
        )
        self._handed_back = chat_ctx is not None

    def _open(self, spec: dict[str, Any]):
        async def open_app(context: RunContext):
            return AppAssistant(spec, self.person, self.guide, _handoff_context(self))

        return function_tool(
            open_app,
            name=f"open_{re.sub(r'[^a-z0-9]+', '_', spec['id'].lower())}",
            description=f"Hand the person to the {spec['title']} assistant: {spec['description']}",
        )

    async def on_enter(self) -> None:
        # Back from an app's assistant: carry on with what the person asked.
        if self._handed_back:
            self.session.generate_reply()


class AppAssistant(Agent):
    """One app's assistant, with only that app's tools, acting as the person talking."""

    def __init__(self, spec: dict[str, Any], person: dict[str, Any], guide: str, chat_ctx: ChatContext) -> None:
        self.person, self.guide, self.title = person, guide, spec["title"]
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
        return Assistant(self.person, self.guide, _handoff_context(self))

    async def on_enter(self) -> None:
        # Say what is happening at once; the answer is worked out while this plays. Kept out of the
        # history, so the model does not say it again.
        self.session.say(f"Let me check {self.title}.", add_to_chat_ctx=False)
        # Answer the request the main assistant handed over.
        self.session.generate_reply()


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    # Voice activity: local Silero model, so no LiveKit Cloud call. Loaded once per worker
    # process, before any call, instead of at the start of each.
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=END_OF_SPEECH_SECONDS)


server.setup_fnc = prewarm


async def duplex_entrypoint(ctx: JobContext, engine: dict[str, Any]) -> None:
    """A full-duplex call: the model listens and speaks itself, with web search as its one tool."""
    await ctx.connect()
    participant = await ctx.wait_for_participant()
    person = (load_people().get("people") or {}).get(participant.identity.lower())
    who = f"You are talking with {person['name']}. " if person else ""
    instructions = (
        f"{INSTRUCTIONS}\n\n{now_line()}\n\n{who}In this mode you cannot see or change anything in the "
        "household's apps; if asked, say that the household can switch the voice assistant back to separate "
        "steps in Mu3Lab's Settings to use them."
    )
    await duplex.run_call(ctx.room, participant, engine, instructions)


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    engine = load_engine()
    if engine.get("engine") == "duplex":
        await duplex_entrypoint(ctx, engine)
        return
    # Speech models load while the person joins.
    speech_ready = asyncio.create_task(warm_speech())

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
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
            timeout=httpx2.Timeout(connect=15.0, read=ANSWER_WAIT_SECONDS, write=5.0, pool=5.0),
        ),
        tts=openai.TTS(
            base_url=SPEECH_BASE_URL,
            api_key=SPEECH_API_KEY,
            model=TTS_MODEL,
            voice=TTS_VOICE,
            # Raw 24 kHz audio, as the plugin plays it: no MP3 to encode in Speaches and decode here.
            response_format="pcm",
        ),
        # End-of-turn model that runs locally. "v1-mini" is the local model; the default
        # "v1" would call LiveKit's hosted gateway.
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(version="v1-mini"),
            endpointing={"min_delay": END_OF_SPEECH_SECONDS},
            # Prepare the answer and its first spoken sentence before the turn is confirmed.
            preemptive_generation={"enabled": True, "preemptive_tts": True},
        ),
    )

    await ctx.connect()
    # The person's identity is their email, set from their sign-in by the voice page.
    participant = await ctx.wait_for_participant()
    people = load_people()
    person = (people.get("people") or {}).get(participant.identity.lower())
    if person is None:
        logger.warning("No voice tools for this caller; answering without app tools.")
    assistant = Assistant(person, str(people.get("guide") or ""))
    answer_ready = asyncio.create_task(warm_answer_model(assistant.instructions))
    await session.start(agent=assistant, room=ctx.room)
    await wait_until_warm(speech_ready, answer_ready)
    # Speak first, once everything is loaded, so the first answer is quick. A fixed sentence needs
    # no model call: an instruction-only request has no messages, which Gemini rejects. Kept out
    # of the chat history so the model's first request still starts with the person's words.
    session.say("Hi, how can I help?", add_to_chat_ctx=False)


if __name__ == "__main__":
    cli.run_app(server)
