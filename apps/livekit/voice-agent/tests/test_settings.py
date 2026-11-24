"""The household's settings file: a missing, broken or odd file never stops a call."""

import json

import agent


def test_defaults_without_a_file() -> None:
    settings = agent.load_settings()
    assert settings["responsiveness"] == "balanced"
    assert settings["wait_phrase"] == "One moment." and settings["wait_after"] == 1.5
    assert settings["voice"] == agent.TTS_VOICE


def test_a_broken_file_gives_the_defaults() -> None:
    agent.VOICE_SETTINGS.write_text("{not json")
    assert agent.load_settings()["responsiveness"] == "balanced"
    agent.VOICE_SETTINGS.write_text("[1, 2]")
    assert agent.load_settings()["wait_after"] == 1.5


def test_odd_values_are_kept_sensible() -> None:
    agent.VOICE_SETTINGS.write_text(
        json.dumps({"responsiveness": "instant", "wait_after": "soon", "wait_phrase": None, "voice": ""})
    )
    settings = agent.load_settings()
    assert settings["responsiveness"] == "balanced"
    assert settings["wait_after"] == 1.5
    assert settings["wait_phrase"] == ""
    agent.VOICE_SETTINGS.write_text(json.dumps({"wait_after": 99, "wait_phrase": "x" * 200}))
    settings = agent.load_settings()
    assert settings["wait_after"] == 10 and len(settings["wait_phrase"]) == 80


def test_a_tool_filler_does_not_repeat_the_wait_phrase(monkeypatch) -> None:
    state = agent.CallState(settings=agent.load_settings())
    filler = agent._fillers(state)
    assert [filler(0), filler(1), filler(2)] == ["One moment.", "Still working on it.", None]
    clock = [100.0]
    monkeypatch.setattr(agent.time, "monotonic", lambda: clock[0])
    state.waited_at = 99.0  # the wait phrase was just said
    filler = agent._fillers(state)
    assert filler(0) is None  # skipped once; LiveKit tries again an interval later
    assert [filler(0), filler(1)] == ["Still working on it.", None]
