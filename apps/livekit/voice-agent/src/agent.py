"""Mu3Lab voice agent: speech in, a local language model, speech out.

Everything runs on this server. The chat model (the local model chosen in
Mu3Lab's Settings) and speech both go through Mu3Lab's LiteLLM gateway with
the agent's own key, and voice activity and turn taking run inside this
container. Every address and model
name comes from the environment, so the same code runs on a desktop for
testing (localhost ports) and inside Mu3Lab (service names).
"""

import logging
import os

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    cli,
    inference,
)
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
LLM_MODEL = _env("MU3LAB_LLM_MODEL", "mu3lab-local")
# Speech recognition and speech output, on one OpenAI-compatible API: LiteLLM's mu3lab-stt and
# mu3lab-tts in Mu3Lab, or Speaches itself (real model names) when LiteLLM is bypassed.
SPEECH_BASE_URL = _env("MU3LAB_SPEECH_URL", "http://litellm:4000/v1")
SPEECH_API_KEY = _env("MU3LAB_SPEECH_API_KEY", LLM_API_KEY)
STT_MODEL = _env("MU3LAB_STT_MODEL", "mu3lab-stt")
TTS_MODEL = _env("MU3LAB_TTS_MODEL", "mu3lab-tts")
TTS_VOICE = _env("MU3LAB_TTS_VOICE", "af_heart")

INSTRUCTIONS = (
    "You are Mu3Lab's voice assistant for a household. Speak in short, plain sentences "
    "that sound natural when read aloud. Do not use lists, markdown or emoji."
)


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        # Voice activity: local Silero model, so no LiveKit Cloud call.
        vad=silero.VAD.load(),
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
            reasoning_effort="none",
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
            preemptive_generation={"enabled": True},
        ),
    )

    await session.start(agent=Assistant(), room=ctx.room)
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
