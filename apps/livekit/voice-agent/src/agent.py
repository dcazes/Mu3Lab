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
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    TurnHandlingOptions,
    cli,
    function_tool,
    inference,
    mcp,
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
# Mu3Lab's gateway holds the real chat model. Qwen 3.5 thinks by default and is far too slow
# for voice, so the agent always sends reasoning_effort="none".
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

INSTRUCTIONS = (
    "You are Mu3Lab's voice assistant for a household. Speak in short, plain sentences "
    "that sound natural when read aloud. Do not use lists, markdown or emoji."
)
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


def load_people() -> dict[str, Any]:
    """Everyone's app assistants as Mu3Lab wrote them; empty before Mu3Lab has."""
    try:
        return json.loads(VOICE_TOOLS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("No voice tools file at %s; the assistant has no app tools.", VOICE_TOOLS)
        return {}


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

    Each yes allows one change, so a second change needs its own.
    """

    def __init__(self, *, id: str, mcp_server: mcp.MCPServer, reads: list[str]) -> None:
        super().__init__(id=id, mcp_server=mcp_server)
        self._reads = tuple(f"-{name}" for name in reads)
        self._used: set[str] = set()

    async def setup(self, *, reload: bool = False):
        await super().setup(reload=reload)
        self._tools = [tool if tool.info.name.endswith(self._reads) else self._guard(tool) for tool in self._tools]
        return self

    def _guard(self, tool):
        async def call(raw_arguments: dict[str, Any], context: RunContext) -> Any:
            said = _latest_words(context.session.history)
            if said is None or said[0] in self._used or not YES.search(said[1]):
                raise ToolError(ASK_FIRST)
            self._used.add(said[0])
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
        instructions = f"{INSTRUCTIONS}\n\n{situation}"
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
        self.person, self.guide = person, guide
        server = mcp.MCPServerHTTP(
            url=spec["url"],
            headers={"Authorization": f"Bearer {spec['token']}"},
            timeout=10,
            client_session_timeout_seconds=30,
        )
        super().__init__(
            instructions=spec["instructions"],
            # back_to_main below joins these by itself.
            tools=[ConfirmedToolset(id=spec["id"], mcp_server=server, reads=spec.get("reads", []))],
            chat_ctx=chat_ctx,
        )

    @function_tool
    async def back_to_main(self, context: RunContext):
        """Hand the person back to the main assistant: when they are done here or ask about another app."""
        return Assistant(self.person, self.guide, _handoff_context(self))

    async def on_enter(self) -> None:
        # Answer the request the main assistant handed over.
        self.session.generate_reply()


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        # Voice activity: local Silero model, so no LiveKit Cloud call. A 0.3 s pause (default
        # 0.55 s) ends speech sooner; the turn detector still waits when someone sounds unfinished.
        vad=silero.VAD.load(min_silence_duration=0.3),
        stt=openai.STT(
            base_url=SPEECH_BASE_URL,
            api_key=SPEECH_API_KEY,
            model=STT_MODEL,
            language="en",
            # Speaches' REST transcription endpoint, one request per utterance.
            use_realtime=False,
        ),
        llm=openai.LLM(
            model=LLM_MODEL,
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            # "low" is the lowest level every provider accepts: Gemini 3, GitHub Models, Groq and
            # xAI reject "none" or "minimal". FreeLLMAPI adapts it per provider, or drops it.
            reasoning_effort="low",
        ),
        tts=openai.TTS(
            base_url=SPEECH_BASE_URL,
            api_key=SPEECH_API_KEY,
            model=TTS_MODEL,
            voice=TTS_VOICE,
        ),
        # End-of-turn model that runs locally. "v1-mini" is the local model; the default
        # "v1" would call LiveKit's hosted gateway.
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(version="v1-mini"),
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
    await session.start(agent=Assistant(person, str(people.get("guide") or "")), room=ctx.room)
    # Speak first, so the person hears at once that the call is working. A fixed sentence needs
    # no model call: an instruction-only request has no messages, which Gemini rejects. Kept out
    # of the chat history so the model's first request still starts with the person's words.
    session.say("Hi, how can I help?", add_to_chat_ctx=False)


if __name__ == "__main__":
    cli.run_app(server)
