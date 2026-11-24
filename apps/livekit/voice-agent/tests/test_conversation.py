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


def test_advanced_turn_timing_reaches_the_session() -> None:
    chosen = settings(vad_silence=0.5, min_delay=0.1, max_delay=0, eot_threshold=0.2, interrupt_after=0.8)
    agent.VOICE_SETTINGS.write_text(__import__("json").dumps(chosen))
    loaded = agent.load_settings()
    assert loaded["min_delay"] == agent.END_OF_SPEECH_SECONDS  # LiveKit refuses less with the turn detector
    handling = agent.turn_handling(loaded)
    assert handling["endpointing"] == {"min_delay": 0.25, "max_delay": 2.0}  # 0: from Responsiveness
    assert handling["interruption"]["min_duration"] == 0.8
    loaded["max_delay"] = 4
    assert agent.turn_handling(loaded)["endpointing"]["max_delay"] == 4


def test_voice_gaps_are_audio_that_came_after_the_last_finished() -> None:
    from speech import GapMeter

    meter = GapMeter()
    meter.add(10.0, 0.5)  # plays 10.0 to 10.5
    meter.add(10.1, 0.5)  # ready early: plays 10.5 to 11.0
    meter.add(11.3, 0.5)  # ready 0.3 s after the audio before it ended: a gap
    meter.add(11.82, 0.2)  # 0.02 s late: not heard
    assert (meter.count, round(meter.total, 2), round(meter.longest, 2), meter.audio) == (1, 0.3, 0.3, 1.7)


async def test_a_streaming_model_is_used_only_while_its_recognizer_answers() -> None:
    async def up():
        return True

    async def down():
        return False

    stt, plugin = await agent.choose_stt(settings(stt_engine="kroko"), ready=up)
    assert plugin is stt and plugin.model == "kroko" and plugin._url.endswith("/?model=kroko")
    stt, plugin = await agent.choose_stt(settings(stt_engine="zipformer"), ready=down)
    assert plugin is None and stt.model == agent.STT_MODEL  # Whisper, so the call still works
    stt, plugin = await agent.choose_stt(settings(), ready=up)
    assert plugin is None  # Whisper by default


def test_an_unknown_engine_is_whisper() -> None:
    agent.VOICE_SETTINGS.write_text('{"stt_engine": "magic"}')
    assert agent.load_settings()["stt_engine"] == "whisper"


def test_the_recognizer_is_flushed_when_the_person_stops_talking() -> None:
    session = QuietSession()
    flushed = []
    agent.follow_speech(session, type("Plugin", (), {"flush_all": lambda self: flushed.append(1)})())
    handler = session.handlers["user_state_changed"]
    handler(type("Event", (), {"old_state": "listening", "new_state": "speaking"})())
    assert not flushed
    handler(type("Event", (), {"old_state": "speaking", "new_state": "listening"})())
    assert flushed == [1]
