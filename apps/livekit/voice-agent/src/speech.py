"""What the assistant says, made ready for the voice. Adapted from the VoiceAgent phone project
(``my-agent/src/speech_text.py`` and ``common.py``), where each of these fixed a real call.

- ``clean_for_speech``: models write typographic characters (a non-breaking hyphen inside
  "twenty-fourth", a narrow space before "p.m.", curly quotes). A voice can read them as noise
  ("twenty-y fourth"). They become plain ASCII, which every voice reads the same way.
- ``ReplyCutter``: some models (gpt-oss, and others behind free routes) write two or three versions
  of one answer glued together ("...for you?Sure, what day...?"). Only the first is kept.
- ``clean_reply``: both of the above on the model's stream, so the history holds what was said.
- ``with_wait_phrase``: when the answer's first words are slow (an outside route can take seconds),
  the person hears a short phrase instead of silence; the answer follows in the same turn.
- ``say_date`` / ``say_time`` / ``upcoming_days``: dates and times in words, and the next days
  with their dates, so a small model looks "next Thursday" up instead of working it out.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable
from datetime import date, datetime, time, timedelta

from livekit.agents import llm

logger = logging.getLogger("mu3lab-voice-agent")

_CHAR_MAP = str.maketrans(
    {
        0x2010: "-",  # hyphen
        0x2011: "-",  # non-breaking hyphen
        0x2012: "-",  # figure dash
        0x2013: "-",  # en dash
        0x2014: ", ",  # em dash: a pause
        0x2015: ", ",  # horizontal bar
        0x2212: "-",  # minus sign
        0x00A0: " ",  # no-break space
        0x2007: " ",  # figure space
        0x202F: " ",  # narrow no-break space
        0x2009: " ",  # thin space
        0x200A: " ",  # hair space
        0x200B: "",  # zero-width space
        0x2018: "'",  # left single quote
        0x2019: "'",  # right single quote (apostrophe)
        0x201A: "'",  # low single quote
        0x2032: "'",  # prime
        0x201C: '"',  # left double quote
        0x201D: '"',  # right double quote
        0x201E: '"',  # low double quote
        0x2033: '"',  # double prime
        0x2026: "...",  # ellipsis
        0x00B7: " ",  # middle dot
        0x2022: " ",  # bullet
    }
)


def clean_for_speech(text: str) -> str:
    """Replace typographic characters with the plain ASCII ones every voice reads the same way."""
    return text.translate(_CHAR_MAP)


# A sentence end followed straight by a capital and a lowercase letter, with no space: "you?Sure".
# The lowercase letter or digit before the mark keeps "U.S." and "a.m." apart.
_GLUE = re.compile(r"(?<=[a-z0-9)'\"])[.?!](?=[A-Z][a-z])")
# Might become a glue once more text arrives.
_TAIL = re.compile(r"[.?!][A-Z]?$")


class ReplyCutter:
    """A streaming filter for one reply: text passes through until two answers are glued together,
    then everything after the first is dropped. ``feed`` returns what is safe to pass on now;
    ``finish`` returns what was held back."""

    def __init__(self) -> None:
        self._held = ""
        self._prev = ""  # the last character passed on, for the look-behind
        self.cut = False
        self.dropped = ""

    def feed(self, text: str) -> str:
        if self.cut:
            self.dropped += text
            return ""
        buffer = self._held + text
        if match := _GLUE.search(self._prev + buffer):
            end = match.end() - len(self._prev)  # keep the punctuation mark
            self.cut = True
            self.dropped = buffer[end:]
            self._held = ""
            return self._emit(buffer[:end])
        tail = _TAIL.search(buffer)
        self._held = buffer[tail.start() :] if tail else ""
        return self._emit(buffer[: len(buffer) - len(self._held)])

    def finish(self) -> str:
        out, self._held = self._held, ""
        return "" if self.cut else self._emit(out)

    def _emit(self, text: str) -> str:
        if text:
            self._prev = text[-1]
        return text


async def clean_reply(stream: AsyncIterable) -> AsyncIterator:
    """The model's stream with its text cleaned for the voice, and only the first of glued answers.
    Tool calls and usage pass unchanged; the history gets the same text the person hears."""
    cutter = ReplyCutter()
    async for chunk in stream:
        if isinstance(chunk, str):
            if out := cutter.feed(clean_for_speech(chunk)):
                yield out
        elif isinstance(chunk, llm.ChatChunk) and chunk.delta and chunk.delta.content:
            out = cutter.feed(clean_for_speech(chunk.delta.content))
            delta = chunk.delta.model_copy(update={"content": out or None})
            if out or delta.tool_calls or chunk.usage or delta.extra:
                yield chunk.model_copy(update={"delta": delta})
        else:
            yield chunk
    if rest := cutter.finish():
        yield rest
    if cutter.cut:
        logger.warning(
            "The model wrote more than one answer in a reply; kept the first (%d characters dropped).",
            len(cutter.dropped),
        )


async def with_wait_phrase(
    stream: AsyncIterable, after: float, phrase: str, said: Callable[[], None] | None = None
) -> AsyncIterator:
    """Pass the model's stream through; if its first chunk takes longer than ``after`` seconds, say
    ``phrase`` first (``said`` is told), so the person hears something. An empty phrase is off."""
    it = stream.__aiter__()
    first = asyncio.ensure_future(it.__anext__())
    try:
        if phrase:
            done, _ = await asyncio.wait({first}, timeout=after)
            if not done:
                logger.info("The answer is slow to start; saying the wait phrase.")
                if said:
                    said()
                yield phrase + " "
        try:
            chunk = await first
        except StopAsyncIteration:
            return
        yield chunk
        async for chunk in it:
            yield chunk
    finally:
        if not first.done():
            first.cancel()
            # Let the cancelled step finish before closing the stream.
            await asyncio.gather(first, return_exceptions=True)
        if hasattr(it, "aclose"):
            await it.aclose()


class GapMeter:
    """Follows a reply's audio as it is made: a gap is audio that arrived after the audio before it
    had finished playing (assuming playback starts with the first frame and runs without pause)."""

    # Shorter waits are not heard.
    NOTICEABLE = 0.05

    def __init__(self) -> None:
        self.frames = 0
        self.audio = 0.0
        self.count = 0
        self.total = 0.0
        self.longest = 0.0
        self._ends_at = 0.0

    def add(self, arrived: float, seconds: float) -> None:
        if self.frames and arrived - self._ends_at > self.NOTICEABLE:
            gap = arrived - self._ends_at
            self.count += 1
            self.total += gap
            self.longest = max(self.longest, gap)
        # Playback of this frame starts when it arrives or when the audio before it ends, whichever is later.
        self._ends_at = max(arrived, self._ends_at) + seconds
        self.frames += 1
        self.audio += seconds


# --- Dates and times in words --------------------------------------------------------------

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty"}
_ORDINAL_ONES = [
    "zeroth",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
]
_ORDINAL_TENS = {20: "twentieth", 30: "thirtieth"}


def _number(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens * 10] + (f" {_ONES[ones]}" if ones else "")


def _ordinal(n: int) -> str:
    if n < 20:
        return _ORDINAL_ONES[n]
    tens, ones = divmod(n, 10)
    if not ones:
        return _ORDINAL_TENS[tens * 10]
    return f"{_TENS[tens * 10]} {_ORDINAL_ONES[ones]}"


def say_date(day: date) -> str:
    """'Thursday, November twenty sixth' (no hyphens: voices read them oddly)."""
    return f"{day:%A}, {day:%B} {_ordinal(day.day)}"


def say_time(moment: time) -> str:
    """'nine in the morning', 'one thirty in the afternoon', 'noon'."""
    if moment.hour == 12 and moment.minute == 0:
        return "noon"
    if moment.hour == 0 and moment.minute == 0:
        return "midnight"
    words = _number(moment.hour % 12 or 12)
    if moment.minute:
        words += f" oh {_number(moment.minute)}" if moment.minute < 10 else f" {_number(moment.minute)}"
    part = "in the morning" if moment.hour < 12 else "in the afternoon" if moment.hour < 17 else "in the evening"
    return f"{words} {part}"


def say_when(moment: datetime) -> str:
    """'Thursday, November twenty sixth at one thirty in the afternoon'."""
    return f"{say_date(moment.date())} at {say_time(moment.time())}"


def upcoming_days(today: date, count: int = 14) -> str:
    """The next ``count`` days with their dates, one per line, for the instructions."""
    lines = []
    for index in range(count):
        day = today + timedelta(days=index)
        label = ("today, ", "tomorrow, ")[index] if index < 2 else ""
        lines.append(f"- {label}{say_date(day)}: {day.isoformat()}")
    return "\n".join(lines)
