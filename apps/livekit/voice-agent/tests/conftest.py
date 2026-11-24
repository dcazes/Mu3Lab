"""The agent's tests run without a network and without Mu3Lab: each test gets its own empty
settings folder, so nothing on this computer changes what they see."""

import agent
import pytest


@pytest.fixture(autouse=True)
def _own_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "VOICE_TOOLS", tmp_path / "people.json")
    monkeypatch.setattr(agent, "VOICE_SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(agent, "CALL_LOG", "")
