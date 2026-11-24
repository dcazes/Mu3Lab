"""The conversation around the answers: quiet calls end, times in tool results come with words,
and the turn detector's score is logged (plan items 4.3, 3.1 and 1.5)."""

import asyncio
import logging

import agent


class QuietSession:
    """Records what the silence check says; speech 'plays' at once."""

    def __init__(self) -> None:
        self.handlers: dict = {}
        self.said: list[str] = []

    def on(self, event):
        def register(handler):
            self.handlers[event] = handler
            return handler

        return register

    async def generate_reply(self, instructions: str) -> None:
        self.said.append("still there?")

    async def say(self, text: str, allow_interruptions: bool = True, add_to_chat_ctx: bool = True) -> None:
        self.said.append(text)

    def person(self, state: str) -> None:
        self.handlers["user_state_changed"](type("Event", (), {"new_state": state, "old_state": "listening"})())


def settings(**changes):
    return {**agent.load_settings(), **changes}


async def test_a_quiet_call_is_checked_on_then_ended() -> None:
    session, ended = QuietSession(), []

    async def end(_session, reason):
        ended.append(reason)

    agent.check_on_silence(session, settings(silence_after=0, silence_reasks=2, silence_goodbye="Bye."), end=end)
    session.person("away")
    for _ in range(20):
        await asyncio.sleep(0)
    assert session.said == ["still there?", "still there?", "Bye."] and ended == ["the person was quiet"]


async def test_speaking_again_stops_the_check() -> None:
    session, ended = QuietSession(), []

    async def end(_session, reason):
        ended.append(reason)

    agent.check_on_silence(session, settings(silence_after=5, silence_reasks=2), end=end)
    session.person("away")
    await asyncio.sleep(0.01)
    session.person("speaking")
    await asyncio.sleep(0.01)
    assert session.said == ["still there?"] and ended == []


def test_silence_settings_are_kept_sensible() -> None:
    agent.VOICE_SETTINGS.write_text('{"silence_after": 3, "silence_reasks": 9}')
    chosen = agent.load_settings()
    assert chosen["silence_after"] == 0  # too short to be meant: off
    assert chosen["silence_reasks"] == 3
    assert agent.load_settings()["silence_goodbye"]


def test_tool_times_come_with_words_in_the_households_zone(monkeypatch) -> None:
    monkeypatch.setenv("TZ", "America/Toronto")
    text = '{"events": [{"title": "Dentist", "start": "2026-11-26T19:30:00Z"}, {"start": "2026-11-26T19:30:00Z"}]}'
    spoken = agent.speakable(text)
    assert spoken.startswith(text)
    assert spoken.endswith("2026-11-26T19:30:00Z = Thursday, November twenty sixth at two thirty in the afternoon")
    assert spoken.count("2026-11-26T19:30:00Z =") == 1
    assert agent.speakable('{"count": 3}') == '{"count": 3}'
    assert agent.speakable({"not": "text"}) == {"not": "text"}


def test_the_date_list_is_in_the_instructions() -> None:
    line = agent.now_line()
    assert "- today, " in line and "- tomorrow, " in line and line.count("\n- ") == 14


def test_the_end_of_turn_score_is_one_readable_line() -> None:
    record = logging.LogRecord("livekit.agents", logging.DEBUG, __file__, 1, "eot prediction", (), None)
    record.__dict__.update(probability=0.123, unlikely_threshold=0.36, endpointing_delay=2.0, trigger="vad")
    assert agent._EndOfTurnScore().filter(record)
    assert record.levelname == "INFO"
    assert record.getMessage() == "End of turn: score 0.12 against threshold 0.36, so waited 2.00 s (vad)"
    other = logging.LogRecord("livekit.agents", logging.DEBUG, __file__, 1, "something else", (), None)
    assert not agent._EndOfTurnScore().filter(other)


def test_reports_record_what_the_household_keeps(monkeypatch) -> None:
    monkeypatch.setattr(agent, "REPORTS", "/reports")
    assert agent.recording(settings()) == {"audio": True, "transcript": True, "traces": False, "logs": False}
    assert agent.recording(settings(reports_audio=False))["audio"] is False
    assert agent.recording(settings(reports=False)) is False
    monkeypatch.setattr(agent, "REPORTS", "")
    assert agent.recording(settings()) is False


def test_old_reports_are_deleted(tmp_path) -> None:
    import os
    import time

    old, new = tmp_path / "20261101-100000Z_room-a", tmp_path / "20261124-100000Z_room-b"
    for folder in (old, new):
        folder.mkdir()
        (folder / "call.json").write_text("{}")
    long_ago = time.time() - 20 * 86400
    os.utime(old, (long_ago, long_ago))
    assert agent.prune_reports(tmp_path, 14) == 1
    assert not old.exists() and new.exists()
    assert agent.prune_reports(tmp_path / "missing", 14) == 0
