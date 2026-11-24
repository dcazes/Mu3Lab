"""What reaches the voice: typographic characters, glued answers, the wait phrase, and dates and
times in words (adapted from the VoiceAgent project's tests)."""

import asyncio
from datetime import date, datetime, time

import pytest
import speech
from livekit.agents import llm
from speech import ReplyCutter, clean_for_speech, say_date, say_time, say_when, upcoming_days


def test_typographic_characters_become_ascii() -> None:
    text = "November twenty‑fourth at one thirty p.m. — what’s up…"
    assert clean_for_speech(text) == "November twenty-fourth at one thirty p.m. ,  what's up..."
    assert all(ord(c) < 128 for c in clean_for_speech(text))


def cut(chunks: list[str]) -> str:
    cutter = ReplyCutter()
    return "".join(cutter.feed(chunk) for chunk in chunks) + cutter.finish()


def test_glued_answers_keep_the_first() -> None:
    glued = "Great. What day works best for you?Sure, what day would you like?No problem, just let me know."
    expected = "Great. What day works best for you?"
    assert cut([glued]) == expected
    # Split across stream chunks, right at the glue.
    assert cut(["Great. What day works best for you", "?", "S", "ure"]) == expected
    assert cut(["...for you?", "Sure, what day"]) == "...for you?"


def test_normal_replies_pass_unchanged() -> None:
    for text in [
        "Sure. What day works for you?",
        "We open at 9 a.m. Monday to Sunday.",
        "That's in the U.S. Okay?",
        "It's 1.5 hours.",
        "Wait... Yes.",
    ]:
        assert cut([text]) == text
        assert cut(list(text)) == text  # one character per chunk


async def _stream(chunks):
    for chunk in chunks:
        yield chunk


def _chunk(text: str) -> llm.ChatChunk:
    return llm.ChatChunk(id="x", delta=llm.ChoiceDelta(role="assistant", content=text))


async def test_clean_reply_cleans_model_chunks_for_the_voice_and_history() -> None:
    chunks = [_chunk("Thursday, November twenty‑fourth works."), _chunk("Sure, ")]
    out = [c async for c in speech.clean_reply(_stream(chunks))]
    text = "".join(c if isinstance(c, str) else (c.delta.content or "") for c in out)
    assert text == "Thursday, November twenty-fourth works."


async def reply(delay: float, chunks=("Sure, ", "Tuesday works.")):
    await asyncio.sleep(delay)
    for chunk in chunks:
        yield chunk


async def collect(stream) -> list:
    return [chunk async for chunk in stream]


async def test_a_quick_answer_has_no_wait_phrase() -> None:
    told = []
    out = await collect(speech.with_wait_phrase(reply(0.01), 0.2, "One moment.", lambda: told.append(1)))
    assert out == ["Sure, ", "Tuesday works."] and not told


async def test_a_slow_answer_starts_with_the_wait_phrase() -> None:
    told = []
    out = await collect(speech.with_wait_phrase(reply(0.3), 0.1, "One moment.", lambda: told.append(1)))
    assert out == ["One moment. ", "Sure, ", "Tuesday works."] and told == [1]


async def test_an_empty_phrase_is_off() -> None:
    assert await collect(speech.with_wait_phrase(reply(0.2), 0.05, "")) == ["Sure, ", "Tuesday works."]


async def test_empty_answers_and_errors_pass_through() -> None:
    assert await collect(speech.with_wait_phrase(reply(0.01, ()), 0.2, "x")) == []

    async def failing():
        await asyncio.sleep(0.01)
        raise RuntimeError("every route failed")
        yield  # pragma: no cover

    with pytest.raises(RuntimeError, match="every route failed"):
        await collect(speech.with_wait_phrase(failing(), 0.2, "x"))


async def test_an_interruption_while_waiting_closes_the_model_stream() -> None:
    closed = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(10)
            yield "late"
        finally:
            closed.set()

    task = asyncio.create_task(collect(speech.with_wait_phrase(slow(), 5, "x")))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


def test_spoken_dates_and_times() -> None:
    assert say_date(date(2026, 11, 26)) == "Thursday, November twenty sixth"
    assert say_date(date(2026, 12, 1)) == "Tuesday, December first"
    assert say_date(date(2026, 12, 30)) == "Wednesday, December thirtieth"
    assert say_time(time(13, 30)) == "one thirty in the afternoon"
    assert say_time(time(9, 0)) == "nine in the morning"
    assert say_time(time(9, 5)) == "nine oh five in the morning"
    assert say_time(time(12, 0)) == "noon" and say_time(time(0, 0)) == "midnight"
    assert say_time(time(19, 45)) == "seven forty five in the evening"
    assert say_when(datetime(2026, 11, 26, 14, 0)) == "Thursday, November twenty sixth at two in the afternoon"


def test_the_next_days_are_listed_with_their_dates() -> None:
    lines = upcoming_days(date(2026, 11, 24)).splitlines()
    assert len(lines) == 14
    assert lines[0] == "- today, Tuesday, November twenty fourth: 2026-11-24"
    assert lines[1].startswith("- tomorrow, Wednesday")
    assert lines[13].endswith("2026-12-07")
